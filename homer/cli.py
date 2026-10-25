"""homer command line: picks the backend per account and prints results."""
import argparse
import email
import email.policy
import imaplib
import io
import os
import re
import signal
import smtplib
import subprocess
import sys
from pathlib import Path

from . import daemon, gmail, outlook
from .common import (attachments, body_text, build_forward, build_message, build_reply, die, get_account, load_config,
                     private_dirs, save_attachments)

BACKENDS = {"gmail": gmail, "outlook": outlook}
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # all but \t and \n: escape sequences, \r, bells


def plain(text):
    """Text from a mail or the server, safe to print: no escape sequences that recolour, move the cursor,
    rewrite earlier lines or set the window title (S5)."""
    return CONTROL.sub("", str(text))


def account(cfg, a):
    name, acct = get_account(cfg, a.account)
    provider = acct.get("provider", "gmail")
    backend = BACKENDS.get(provider) or die(f"account {name!r}: unknown provider {provider!r}")
    return name, acct, backend


def cmd_accounts(a, cfg):
    for name, acct in cfg.get("accounts", {}).items():
        mark = "*" if name == cfg.get("default") else " "
        print(f"{mark} {name:12} {acct['email']:35} {acct.get('provider', 'gmail')}")


def cmd_login(a, cfg):
    name, acct, backend = account(cfg, a)
    backend.login(name, acct, rules=a.rules)
    daemon.call({"stop": True})  # the helper still holds the old sign-in
    print(f"ok: {name} logged in{' (rules too)' if a.rules else ''}")


def cmd_folders(a, cfg):
    name, acct, backend = account(cfg, a)
    print(plain("\n".join(backend.folders(name, acct))))


def cmd_list(a, cfg):
    name, acct, backend = account(cfg, a)
    for ref, unread, when, who, subject in backend.list_messages(name, acct, a.folder, a.n, a.unread, a.search):
        date = when.astimezone().strftime("%Y-%m-%d %H:%M") if when else ""
        print(plain(f"{ref:>7} {'*' if unread else ' '} {date:16}  {plain(who)[:28]:28}  {subject}"))


def cmd_read(a, cfg):
    name, acct, backend = account(cfg, a)
    msg = email.message_from_bytes(backend.fetch_raw(name, acct, a.folder, a.id), policy=email.policy.default)
    for h in ("From", "To", "Cc", "Date", "Subject"):
        if msg[h]:
            print(plain(f"{h}: {msg[h]}"))
    print()
    print(plain(body_text(msg)))
    parts = attachments(msg)
    if parts:
        print("\nAttachments:")
        for n, p in enumerate(parts, 1):
            size = len(p.get_payload(decode=True) or b"")
            print(plain(f"  [{n}] {p.get_filename()} ({p.get_content_type()}, {size} bytes)"))
    if a.save:
        for path in save_attachments(parts, a.save):
            print(f"saved {path}")


def cmd_send(a, cfg):
    name, acct, backend = account(cfg, a)
    missing = [f for f in a.attach if not Path(f).is_file()]
    if missing:
        die(f"attachment not found: {', '.join(missing)}")
    if a.body is None and sys.stdin.isatty():
        die("give -b BODY or pipe the body on stdin")
    body = a.body if a.body is not None else sys.stdin.read()
    msg = build_message(acct, a.to, a.cc, a.bcc, a.subject, body, a.attach, a.html)
    if a.draft:
        backend.save_draft(name, acct, msg)
        print(f"draft saved in {acct['email']} Drafts (not sent)")
    else:
        backend.send(name, acct, msg, a.to + a.cc + a.bcc)
        print(f"sent from {acct['email']} to {', '.join(a.to + a.cc + a.bcc)}")


def cmd_reply(a, cfg):
    name, acct, backend = account(cfg, a)
    missing = [f for f in a.attach if not Path(f).is_file()]
    if missing:
        die(f"attachment not found: {', '.join(missing)}")
    if a.body is None and sys.stdin.isatty():
        die("give -b BODY or pipe the body on stdin")
    body = a.body if a.body is not None else sys.stdin.read()
    orig = email.message_from_bytes(backend.fetch_raw(name, acct, a.folder, a.id), policy=email.policy.default)
    msg = build_reply(acct, orig, body, a.all, a.attach, a.html)
    rcpts = [ad.addr_spec for h in ("To", "Cc") if msg[h] for ad in msg[h].addresses]
    if a.draft:
        backend.save_draft(name, acct, msg)
        print(plain(f"reply draft saved in {acct['email']} Drafts (not sent): {msg['Subject']}"))
    else:
        backend.send(name, acct, msg, rcpts)
        print(plain(f"replied from {acct['email']} to {', '.join(rcpts)}: {msg['Subject']}"))


def cmd_forward(a, cfg):
    name, acct, backend = account(cfg, a)
    missing = [f for f in a.attach if not Path(f).is_file()]
    if missing:
        die(f"attachment not found: {', '.join(missing)}")
    body = a.body if a.body is not None else "" if sys.stdin.isatty() else sys.stdin.read()
    orig = email.message_from_bytes(backend.fetch_raw(name, acct, a.folder, a.id), policy=email.policy.default)
    msg = build_forward(acct, orig, a.to, a.cc, a.bcc, body, a.attach, a.html, a.attached)
    if a.draft:
        backend.save_draft(name, acct, msg)
        print(plain(f"forward draft saved in {acct['email']} Drafts (not sent): {msg['Subject']}"))
    else:
        backend.send(name, acct, msg, a.to + a.cc + a.bcc)
        print(plain(f"forwarded from {acct['email']} to {', '.join(a.to + a.cc + a.bcc)}: {msg['Subject']}"))


def cmd_tui(a, cfg):
    name, _, _ = account(cfg, a)
    try:
        from . import tui  # Textual loads only for the TUI; the CLI stays stdlib-only
    except ModuleNotFoundError as e:
        die(f"the TUI needs Textual ({e}); install it with: pip install textual")
    tui.main(cfg, name)


def cmd_mkdir(a, cfg):
    name, acct, backend = account(cfg, a)
    backend.mkdir(name, acct, a.folder)
    print(f"created folder {a.folder!r}")


def cmd_move(a, cfg):
    name, acct, backend = account(cfg, a)
    backend.move(name, acct, a.folder, a.ids, a.to)
    print(f"moved {len(a.ids)} message(s) to {a.to!r}")


def cmd_mark(a, cfg):
    name, acct, backend = account(cfg, a)
    backend.mark(name, acct, a.folder, a.ids, a.state == "read")
    print(f"marked {len(a.ids)} message(s) {a.state}")


RULE_KEYS = ("name", "from", "to", "subject", "has", "move", "archive", "read", "delete", "forward")


def describe_rule(r):
    cond = [f"{k}: {' or '.join(r[k])}" for k in ("from", "to", "subject", "has") if r[k]]
    act = ([f"move to {r['move']}"] if r["move"] else []) + [f"label {x}" for x in r["label"]] + \
          (["archive"] if r["archive"] else []) + \
          (["mark read"] if r["read"] else []) + (["delete"] if r["delete"] else []) + \
          [f"forward to {x}" for x in r["forward"]]
    return f"{', '.join(cond) or 'all mail'} -> {', '.join(act) or 'nothing'}{' (+ more)' if r['other'] else ''}"


def cmd_rules(a, cfg):
    name, acct, backend = account(cfg, a)
    if a.action in (None, "list"):
        for r in backend.rules_list(name, acct):
            label = (r["name"] or "-") + ("" if r["enabled"] else " (off)")
            print(f"{r['id']}  {label}  {describe_rule(r)}")
    elif a.action == "rm":
        for rid in a.ids:
            backend.rm_rule(name, acct, rid)
        print(f"removed {len(a.ids)} rule(s)")
    else:
        changes = {k: getattr(a, k.replace("from", "frm")) for k in RULE_KEYS}
        if a.action == "add":
            if not any(changes[k] for k in ("from", "to", "subject", "has")):
                die("give at least one of --from, --to, --subject, --has")
            if not any(changes[k] for k in ("move", "archive", "read", "delete", "forward")):
                die("give at least one of --move, --archive, --read, --delete, --forward")
            print(f"added rule {backend.add_rule(name, acct, changes)}")
        else:
            if all(v is None for v in changes.values()):
                die("nothing to change")
            print(f"updated rule {backend.edit_rule(name, acct, a.id, changes)}")


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-a", "--account", help="account name from config (default: config's default)")
    folder = argparse.ArgumentParser(add_help=False)
    folder.add_argument("-f", "--folder", default="INBOX")
    id_help = "Gmail: UID from `list`; Outlook: # from the last `list`, or a full message id"

    ap = argparse.ArgumentParser(prog="homer", description="multi-account mail CLI (Gmail/IMAP, Microsoft 365)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts", help="list configured accounts").set_defaults(fn=cmd_accounts)
    p = sub.add_parser("login", parents=[common], help="store app password / OAuth sign-in")
    p.add_argument("--rules", action="store_true", help="also grant access to rules/filters (Gmail: Google sign-in)")
    p.set_defaults(fn=cmd_login)
    sub.add_parser("folders", parents=[common], help="list mail folders").set_defaults(fn=cmd_folders)

    p = sub.add_parser("list", parents=[common, folder], help="list messages, newest first")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--unread", action="store_true")
    p.add_argument("--search", help='Gmail: IMAP SEARCH, e.g. \'FROM "bob" SINCE 1-Oct-2026\'; '
                                    'Outlook: KQL, e.g. \'from:bob subject:exam\'')
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("read", parents=[common, folder], help="show a message (does not mark it read)")
    p.add_argument("id", help=id_help)
    p.add_argument("--save", metavar="DIR", help="save attachments into DIR")
    p.set_defaults(fn=cmd_read)

    p = sub.add_parser("send", parents=[common], help="send a message (or save it as a draft)")
    p.add_argument("--to", action="append", required=True)
    p.add_argument("--cc", action="append", default=[])
    p.add_argument("--bcc", action="append", default=[])
    p.add_argument("-s", "--subject", required=True)
    p.add_argument("-b", "--body", help="body text (default: read stdin)")
    p.add_argument("--attach", action="append", default=[], metavar="FILE")
    p.add_argument("--draft", action="store_true", help="save to Drafts instead of sending")
    p.add_argument("--html", action="store_true", help="body is HTML (a plain-text copy is added)")
    p.set_defaults(fn=cmd_send)

    p = sub.add_parser("reply", parents=[common, folder], help="reply to a message (quotes it, keeps the thread)")
    p.add_argument("id", help=id_help)
    p.add_argument("-b", "--body", help="reply text (default: read stdin)")
    p.add_argument("--all", action="store_true", help="reply to everyone on the original, not just the sender")
    p.add_argument("--attach", action="append", default=[], metavar="FILE")
    p.add_argument("--draft", action="store_true", help="save to Drafts instead of sending")
    p.add_argument("--html", action="store_true", help="reply text is HTML (a plain-text copy is added)")
    p.set_defaults(fn=cmd_reply)

    p = sub.add_parser("forward", parents=[common, folder], help="forward a message with its attachments")
    p.add_argument("id", help=id_help)
    p.add_argument("--to", action="append", required=True)
    p.add_argument("--cc", action="append", default=[])
    p.add_argument("--bcc", action="append", default=[])
    p.add_argument("-b", "--body", help="note above the forwarded message (default: stdin if piped, else none)")
    p.add_argument("--attach", action="append", default=[], metavar="FILE")
    p.add_argument("--draft", action="store_true", help="save to Drafts instead of sending")
    p.add_argument("--html", action="store_true", help="note is HTML (a plain-text copy is added)")
    p.add_argument("--attached", action="store_true", help="send the original as an .eml attachment, not inline")
    p.set_defaults(fn=cmd_forward)

    sub.add_parser("tui", parents=[common], help="browse and manage mail interactively").set_defaults(fn=cmd_tui)

    p = sub.add_parser("mkdir", parents=[common], help="create a folder (Gmail: a label)")
    p.add_argument("folder")
    p.set_defaults(fn=cmd_mkdir)

    p = sub.add_parser("move", parents=[common, folder], help="move messages to another folder")
    p.add_argument("ids", nargs="+", help=id_help)
    p.add_argument("--to", required=True, metavar="FOLDER")
    p.set_defaults(fn=cmd_move)

    p = sub.add_parser("mark", parents=[common, folder], help="mark messages read or unread")
    p.add_argument("state", choices=["read", "unread"])
    p.add_argument("ids", nargs="+", help=id_help)
    p.set_defaults(fn=cmd_mark)

    p = sub.add_parser("rules", parents=[common], help="list, add, edit or remove server-side rules (Gmail filters)")
    p.set_defaults(fn=cmd_rules)
    acts = p.add_subparsers(dest="action", metavar="{list,add,edit,rm}")
    leaf = argparse.ArgumentParser(add_help=False)  # SUPPRESS: `homer rules -a X add` keeps the -a given before add
    leaf.add_argument("-a", "--account", default=argparse.SUPPRESS, help="account name from config")
    acts.add_parser("list", parents=[leaf], help="list rules (the default)")
    fields = argparse.ArgumentParser(add_help=False)
    fields.add_argument("--name", help="rule name (Outlook only; Gmail filters have none)")
    g = fields.add_argument_group("conditions (repeat a flag to match any of its values)")
    g.add_argument("--from", dest="frm", action="append", metavar="ADDR", help="sender contains")
    g.add_argument("--to", action="append", metavar="ADDR", help="recipients contain")
    g.add_argument("--subject", action="append", metavar="TEXT", help="subject contains")
    g.add_argument("--has", action="append", metavar="TEXT", help="Gmail: matches a search; Outlook: body or subject contains")
    g = fields.add_argument_group("actions")
    x = g.add_mutually_exclusive_group()
    x.add_argument("--move", metavar="FOLDER", help="move to FOLDER (Gmail: label it and skip the inbox)")
    x.add_argument("--archive", action="store_true", default=None, help="skip the inbox (Outlook: move to Archive)")
    g.add_argument("--read", action=argparse.BooleanOptionalAction, help="mark read")
    g.add_argument("--delete", action=argparse.BooleanOptionalAction, help="move to Trash / Deleted Items")
    g.add_argument("--forward", action="append", metavar="ADDR", help="forward to ADDR (Gmail: one verified address)")
    acts.add_parser("add", parents=[leaf, fields], help="add a rule")
    q = acts.add_parser("edit", parents=[leaf, fields], help="change a rule; flags you leave out stay as they are")
    q.add_argument("id", help="rule id from `homer rules`")
    q = acts.add_parser("rm", parents=[leaf], help="remove rules")
    q.add_argument("ids", nargs="+", help="rule ids from `homer rules`")
    return ap


def run(a):
    try:
        a.fn(a, load_config())
    except (imaplib.IMAP4.error, smtplib.SMTPException, OSError, subprocess.CalledProcessError) as e:
        die(e)


def main():
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # `homer read ... | head` exits quietly
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-a", "--account") or argv[0].startswith("--account="):
        argv = ["tui", *argv]  # bare `homer` (or `homer -a inst`) opens the TUI
    a = parser().parse_args(argv)  # here, so --help and usage errors never reach the helper
    private_dirs()
    if a.cmd in daemon.COMMANDS and os.environ.get("HOMER_DAEMON") != "0" and "HOMER_CONFIG" not in os.environ:
        piped = getattr(a, "body", "") is None and not sys.stdin.isatty()  # send/reply/forward read stdin then
        stdin = sys.stdin.read() if piped else None
        reply = daemon.call({"argv": argv, "cwd": os.getcwd(), "stdin": stdin})
        if reply:
            sys.stdout.write(reply["out"])
            sys.stderr.write(reply["err"])
            sys.exit(reply["code"])
        daemon.spawn()  # none running, or it stepped aside: start one for next time and run this one here
        if piped:
            sys.stdin = io.StringIO(stdin)
    run(a)
