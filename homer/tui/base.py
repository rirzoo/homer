"""Constants, colour roles and the text helpers every window draws with."""

import base64
import email
import email.policy
import re
import subprocess
import time
import uuid
from email.utils import formataddr, getaddresses
from pathlib import Path

from rich.cells import cell_len, set_cell_size
from rich.text import Text

from .. import store
from ..common import attachments, body_text, part_text


DOWNLOADS = Path("~/Downloads").expanduser()
DRAFTS = Path("~/.cache/homer/drafts").expanduser()  # what you write stays here until it's sent or saved (D14)
VIEW = Path("~/.cache/homer/view").expanduser()  # mail and attachments handed to the browser or an app
SEND_DELAY, READ_AFTER, WIDE = 10, 2.0, 100
SYNC_EVERY, PAGE = store.SYNC_EVERY, store.PAGE
CAUGHT = (Exception, SystemExit)  # backends die() with SystemExit; a worker must never take the app down
ADDR = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL = re.compile(r"https?://[^\s<>\"'\]]+")
# `v` pages: only the mail's own inline images and styles load; no scripts, no remote images or tracking (S2)
CSP = ("""<meta http-equiv="Content-Security-Policy" """
       """content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">""")
# Colour roles, overridable in [tui.colors]; $names are the Textual theme's colours (README, "Colours")
ROLES = {"unread": "bold $primary", "sent": "$success", "thread": "$success", "me": "bold $accent", "quote": "dim",
         "selected": "bold $warning"}
STYLE = {k: "" for k in ROLES}  # resolved by Homer.on_mount
OUT_MARK = {"wait": ("↑", "bold cyan"), "sending": ("↑", "bold cyan"), "sent": ("✓", "bold green"),
            "failed": ("!", "bold red")}

HELP = {
    "list": """\
Move      j k  ↑ ↓   g G top/bottom   space b scroll the preview
Open      Enter full view   v open in the browser, images and all
Write     c new   r reply   R reply all   F forward
Handle    e archive   d delete   m read/unread   M move   u undo
Select    x select row   Esc clear selection or search
Find      /  search (Tab inside it: this folder or all folders)
          from: to: subject: is:unread is:read has:attachment
          before:2026-10-01 after:2026-09-01 and plain words
Go        f folders and accounts
View      T conversations or single mails   + 50 older   ctrl+r sync now
          ctrl+t theme and colours, with a sample to judge them by
Quit      q""",
    "reader": """\
Scroll    j k  space b   g G top/bottom
Mails     n p next/previous mail   Enter expand or collapse it
Open      o links   s save attachments   v open in the browser
Handle    r R F reply/all/forward   e archive   d delete   m read/unread   M move
Back      q or Esc""",
    "compose": """\
Fields    Tab Shift+Tab or ↓ ↑ move between fields; type to fill in
          ↓ ↑ and Tab or Enter pick an address suggestion
          Enter in Subject opens your editor for the body
Commands  Esc leaves the fields, then:
          s send   b edit body   a attach   o open attachment   X remove one
          d save draft   q close (keeps a draft)   D discard
Anywhere  ctrl+s send""",
    "folders": """\
Move      j k  ↑ ↓
Open      Enter on a folder opens it; Enter on another account expands it
Manage    n new folder   E edit what feeds it   D delete (homer only)
          R rename a folder of yours, or the account the cursor is on
          N desktop notifications for new mail in it on/off (♪; needs homer daemon enable)
Close     f or Esc""",
}


def err(e):
    return ((str(e) or type(e).__name__).removeprefix("homer: ").splitlines() or [""])[0]


def parse(raw):
    return email.message_from_bytes(raw, policy=email.policy.default)


def fit(s, width):
    s = re.sub(r"\s+", " ", s)
    return set_cell_size(s, width - 1) + "…" if cell_len(s) > width else set_cell_size(s, width)


def human(n):
    return f"{n} B" if n < 1024 else f"{n // 1024} KB" if n < 1 << 20 else f"{n / (1 << 20):.1f} MB"


def bad_addresses(value):
    return [a or n for n, a in getaddresses([value]) if (a or n) and not ADDR.match(a)]


def addresses(value):
    return [formataddr((n, a)) for n, a in getaddresses([value]) if a]


def open_external(target):
    """xdg-open, detached, so its chatter never lands on the TUI's screen."""
    subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def links_of(msg):
    found = URL.findall(body_text(msg))
    html = msg.get_body(("html",))
    if html is not None:
        found += re.findall(r"""href=["'](https?://[^"']+)""", part_text(html))
    return list(dict.fromkeys(u.rstrip(".,;:!?)") for u in found))


def readable(text):
    """Mail text without the link and image clutter of newsletters: lines that are only a URL or an image
    placeholder go, <URL> link targets go, and long bare URLs shrink to their host. The links stay on o."""
    text = re.sub(r"(?m)^[ \t>]*(?:<?https?://\S+>?|\[(?:image|cid):[^\]]*\])[ \t]*(?:\n|$)", "", text)
    text = re.sub(r"[ \t]*<https?://[^>\s]+>", "", text)
    text = re.sub(r"\[(?:image|cid):[^\]]*\]", "", text)
    text = URL.sub(lambda m: m[0] if len(m[0]) <= 50 else m[0].split("/")[2] + "/…", text)
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)


def inline(part):
    """An image drawn inside the mail (a logo, a signature), as opposed to a file someone attached."""
    return part.get_content_maintype() == "image" and bool(
        part["Content-ID"] or part.get_content_disposition() == "inline")


def clean_view():
    """Delete what `v` and attachments left in VIEW over a day ago (S4). Not at quit: the browser may still be
    reading a file then."""
    for f in VIEW.glob("*"):
        if f.is_file() and f.stat().st_mtime < time.time() - 86400:
            f.unlink(missing_ok=True)


def open_in_browser(msg):
    """The mail as its sender formatted it, inline images included, in the browser."""
    VIEW.mkdir(parents=True, exist_ok=True)
    html = msg.get_body(("html",))
    if html is None:
        path = VIEW / f"{uuid.uuid4().hex}.txt"
        path.write_text(body_text(msg))
    else:
        page = part_text(html)
        for p in msg.walk():
            if not p.is_multipart() and p["Content-ID"]:
                data = base64.b64encode(p.get_payload(decode=True) or b"").decode()
                page = page.replace("cid:" + p["Content-ID"].strip("<>"), f"data:{p.get_content_type()};base64,{data}")
        page = re.sub(r"(?i)^(\s*<!doctype[^>]*>)?", lambda m: (m[1] or "") + CSP, page, count=1)  # no quirks mode
        path = VIEW / f"{uuid.uuid4().hex}.html"
        path.write_text(page)
    open_external(path)


def loc(acct, m):
    return acct.name, m["folder"], m["id"]


def list_row(c, width, me, selected=False):
    """One conversation as a list row: marks, who, count, subject, date (W1)."""
    out = c.get("out")
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append("■" if selected else " ", STYLE["selected"])
    mark, style = (OUT_MARK[out["state"]] if out else ("●", STYLE["unread"]) if c["unread"]
                   else ("→", STYLE["sent"]) if c["mine"] else (" ", ""))
    t.append(mark + " ", style)
    w = 20 if width >= 70 else 14
    n = len(c["msgs"])
    count = f" ({n})" if n > 1 else ""
    who = STYLE["sent"] if c["mine"] else STYLE["unread"] if c["unread"] else ""
    t.append(fit(c["who"], w - len(count)), who)  # the count never gets cut
    replied = not c["mine"] and any(m["addr"].lower() in me for m in c["msgs"])
    t.append(count, STYLE["thread"] if replied else who)  # coloured when you're in the conversation
    t.append(" + " if c["attach"] else "   ", "dim")
    date = (f"{out['left']}s" if out["state"] == "wait" else "now") if out else store.short_when(c["when"])
    t.append(fit(c["subject"] or "(no subject)", max(4, width - w - 13)), "bold" if c["unread"] else "")
    t.append(" " + date.rjust(6), "dim")
    return t


def conv_blocks(conv, acct, raws, opened, width, full=False, cursor=None):
    """A conversation as Text blocks (a title, then one per mail), plus its links and attachments, numbered
    together in order. Collapsed mails are one line; open ones show the body with quotes dimmed (W1, W5)."""
    width = max(30, width)
    names = list(dict.fromkeys("me" if m["addr"].lower() in acct.me else m["name"] or m["addr"]
                               for m in conv["msgs"]))
    n = len(conv["msgs"])
    head = Text()
    head.append((conv["subject"] or "(no subject)") + "\n", "bold")
    head.append(f"{', '.join(names)} · {n} mail{'s' if n != 1 else ''}", "dim")
    blocks, links, parts = [head], [], []
    for i, m in enumerate(conv["msgs"]):
        mine = m["addr"].lower() in acct.me
        who = "me" if mine else m["name"] or m["addr"]
        if full and not mine and m["name"]:
            who += f" <{m['addr']}>"
        if mine and m["to"]:
            who += " → " + (m["to"][0][0] or m["to"][0][1])
        when = store.long_when(store.when_dt(m["when"])) if m["when"] else ""
        msg = raws.get(loc(acct, m))
        bar = Text("▌ ", STYLE["me"]) if mine else Text("  ")  # your own mails carry a bar down the left
        t = Text()
        t.append("─" * width + "\n", "dim")
        line = Text(("▾ " if i in opened else "▸ ") + who, STYLE["me"] if mine else "bold" if m["unread"] else "")
        line.truncate(max(10, width - len(when) - 1), overflow="ellipsis")
        line.append(" " * max(1, width - line.cell_len - len(when)) + when, "dim")
        if i == cursor:
            line.stylize("reverse")
        t.append_text(line)
        if i not in opened:
            snippet = next((s for s in readable(body_text(msg)).splitlines() if s.strip() and not s.startswith(">")),
                           "") if msg else ""
            if snippet:
                t.append("\n").append_text(bar).append(fit(snippet.strip(), width - 2).rstrip(), "dim")
            blocks.append(t)
            continue
        if m["to"]:
            t.append("\n").append_text(bar).append("to " + ", ".join(nm or a for nm, a in m["to"]), "dim")
        if msg is None:
            t.append("\n\n  Loading…", "dim")
            blocks.append(t)
            continue
        ps, ls = attachments(msg), links_of(msg)
        images = sum(inline(p) for p in msg.walk() if not p.is_multipart())
        ps = [p for p in ps if not inline(p)]
        html = msg.get_body(("html",)) is not None
        if html and (images or len(ls) > 3 or msg.get_body(("plain",)) is None):
            note = "Formatted mail" + (f" with {images} image{'s' if images != 1 else ''}" if images else "")
            t.append("\n").append_text(bar).append(note + " · v opens it in the browser", STYLE["quote"])
        t.append("\n\n")
        for ln in readable(body_text(msg)).rstrip().splitlines():
            t.append_text(bar).append(ln + "\n", STYLE["quote"] if ln.lstrip().startswith(">") else "")
        if ps:
            t.append("\n  Attachments ", "bold")
            t.append("   ".join(f"[{len(parts) + k}] {p.get_filename()} · {human(len(p.get_payload(decode=True) or b''))}"
                                for k, p in enumerate(ps, 1)))
            parts += ps
        if ls:
            t.append("\n  Links       ", "bold")
            t.append(f"{len(ls)} · o picks one", "dim")
            links += ls
        blocks.append(t)
    return blocks, links, parts




def send_now(o):
    """Send an outbox entry, then move the draft it came from (if any) to Trash. Raises on failure."""
    acct, msg = o["acct"], o["msg"]
    acct.call("send", msg, [a.addr_spec for h in ("To", "Cc", "Bcc") if msg[h] for a in msg[h].addresses])
    if o["st"]["draft"]:
        try:
            acct.call("move", o["st"]["draft"][0], [o["st"]["draft"][1]], acct.target("Trash"))
        except CAUGHT:
            pass  # sent is what matters; the stale draft just stays in Drafts

