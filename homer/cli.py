"""homer command line: picks the backend per account and prints results."""
import argparse
import email
import email.policy
import imaplib
import signal
import smtplib
import subprocess
import sys
from pathlib import Path

from . import gmail, outlook
from .common import (attachments, body_text, build_message, build_reply, die, get_account, load_config,
                     save_attachments)

BACKENDS = {"gmail": gmail, "outlook": outlook}


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
    backend.login(name, acct)
    print(f"ok: {name} logged in")


def cmd_folders(a, cfg):
    name, acct, backend = account(cfg, a)
    print("\n".join(backend.folders(name, acct)))


def cmd_list(a, cfg):
    name, acct, backend = account(cfg, a)
    for ref, unread, when, who, subject in backend.list_messages(name, acct, a.folder, a.n, a.unread, a.search):
        date = when.astimezone().strftime("%Y-%m-%d %H:%M") if when else ""
        print(f"{ref:>7} {'*' if unread else ' '} {date:16}  {who[:28]:28}  {subject}")


def cmd_read(a, cfg):
    name, acct, backend = account(cfg, a)
    msg = email.message_from_bytes(backend.fetch_raw(name, acct, a.folder, a.id), policy=email.policy.default)
    for h in ("From", "To", "Cc", "Date", "Subject"):
        if msg[h]:
            print(f"{h}: {msg[h]}")
    print()
    print(body_text(msg))
    parts = attachments(msg)
    if parts:
        print("\nAttachments:")
        for n, p in enumerate(parts, 1):
            print(f"  [{n}] {p.get_filename()} ({p.get_content_type()}, {len(p.get_payload(decode=True) or b'')} bytes)")
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
        print(f"reply draft saved in {acct['email']} Drafts (not sent): {msg['Subject']}")
    else:
        backend.send(name, acct, msg, rcpts)
        print(f"replied from {acct['email']} to {', '.join(rcpts)}: {msg['Subject']}")


def cmd_tui(a, cfg):
    name, _, _ = account(cfg, a)
    from . import tui  # curses loads only for the TUI
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


def main():
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # `homer read ... | head` exits quietly
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-a", "--account", help="account name from config (default: config's default)")
    folder = argparse.ArgumentParser(add_help=False)
    folder.add_argument("-f", "--folder", default="INBOX")
    id_help = "Gmail: UID from `list`; Outlook: # from the last `list`, or a full message id"

    ap = argparse.ArgumentParser(prog="homer", description="multi-account mail CLI (Gmail/IMAP, Microsoft 365)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts", help="list configured accounts").set_defaults(fn=cmd_accounts)
    sub.add_parser("login", parents=[common], help="store app password / OAuth sign-in").set_defaults(fn=cmd_login)
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

    a = ap.parse_args()
    try:
        a.fn(a, load_config())
    except (imaplib.IMAP4.error, smtplib.SMTPException, OSError, subprocess.CalledProcessError) as e:
        die(e)
