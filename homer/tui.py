"""homer TUI, built on Textual: a list and a preview, folders on f. Every window and the reason for it is in
FRAME.md (windows W1-W21, decisions D1-D21); the workflow and limits are in TUI.md. Data lives in store.py."""
import base64
import email
import email.policy
import os
import re
import shlex
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, getaddresses
from pathlib import Path

from rich.cells import cell_len, set_cell_size
from rich.errors import StyleSyntaxError
from rich.style import Style
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Input, OptionList, SelectionList, Static
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from . import store
from .cli import BACKENDS
from .common import attachments, body_text, parse_query, part_text, save_attachments

DOWNLOADS = Path("~/Downloads").expanduser()
DRAFTS = Path("~/.cache/homer/drafts").expanduser()  # what you write stays here until it's sent or saved (D14)
VIEW = Path("~/.cache/homer/view").expanduser()  # mail and attachments handed to the browser or an app
SEND_DELAY, READ_AFTER, SYNC_EVERY, WIDE, PAGE = 10, 2.0, 120, 100, 50
CAUGHT = (Exception, SystemExit)  # backends die() with SystemExit; a worker must never take the app down
ADDR = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL = re.compile(r"https?://[^\s<>\"'\]]+")
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
          s send   b edit body   a attach   X remove attachment
          d save draft   q close (keeps a draft)   D discard
Anywhere  ctrl+s send""",
    "folders": """\
Move      j k  ↑ ↓
Open      Enter on a folder opens it; Enter on another account expands it
Manage    n new folder   E edit what feeds it   D delete (homer only)
          R rename a folder of yours, or the account the cursor is on
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


# --- shared widgets ---------------------------------------------------------------------------------

class ConvView(VerticalScroll):
    """A conversation, one Static per mail, so n/p can scroll a mail to the top."""

    async def show(self, blocks, focus=None):
        await self.remove_children()
        await self.mount_all(Static(b, classes="mail") for b in blocks)
        if focus is not None and focus + 1 < len(self.children):
            self.scroll_to_widget(self.children[focus + 1], top=True, animate=False)

    def render_blocks(self, blocks, focus=None):
        self.run_worker(self.show(blocks, focus), group="render", exclusive=True)


class NoWrap(OptionList):
    """OptionList wraps from the last row to the first; a mail list stops at its ends."""

    def action_cursor_down(self):
        if self.highlighted is None or self.highlighted < self.option_count - 1:
            super().action_cursor_down()

    def action_cursor_up(self):
        if self.highlighted:
            super().action_cursor_up()


class MailList(NoWrap):
    BINDINGS = [Binding("j", "cursor_down", show=False), Binding("k", "cursor_up", show=False),
                Binding("g", "first", show=False), Binding("G", "last", show=False)]


class FolderPane(NoWrap):
    BINDINGS = [Binding("j", "cursor_down", show=False), Binding("k", "cursor_up", show=False),
                Binding("enter", "select", "open"), Binding("n", "screen.new_folder", "new folder"),
                Binding("E", "screen.edit_folder", "edit mapping"), Binding("R", "screen.rename_folder", "rename"),
                Binding("D", "screen.delete_folder", "delete folder"), Binding("f", "screen.folders", "close"),
                Binding("escape", "screen.folders", "close", show=False)]


class SearchBar(Input):
    BINDINGS = [Binding("tab", "screen.search_scope", "this folder / all", priority=True),
                Binding("escape", "screen.search_cancel", "cancel")]


class Status(Static):
    def say(self, text, error=False):
        self.set_class(error, "error")
        self.update(Text(text))


class Header(Horizontal):
    def compose(self):
        yield Static(id="hleft")
        yield Static(id="hright")

    def set(self, left, right=""):
        self.query_one("#hleft", Static).update(Text(left))
        self.query_one("#hright", Static).update(Text(right))


# --- main window (W1, W2, W3, W10, W12, W13, W18, W19, W21) ------------------------------------------

class MainScreen(Screen):
    BINDINGS = [
        Binding("c", "compose", "new"),
        Binding("r", "reply('reply')", "reply"),
        Binding("R", "reply('all')", "all"),
        Binding("F", "reply('forward')", "forward"),
        Binding("e", "move('Archive')", "archive"),
        Binding("d", "move('Trash')", "delete"),
        Binding("m", "toggle_read", "read/unread", show=False),
        Binding("M", "pick_move", "move", show=False),
        Binding("x", "select", "select", show=False),
        Binding("u", "undo", "undo", show=False),
        Binding("space", "scroll(1)", "scroll", show=False),
        Binding("b", "scroll(-1)", show=False),
        Binding("slash", "search", "search"),
        Binding("f", "folders", "folders"),
        Binding("T", "single", show=False),
        Binding("v", "browser", show=False),
        Binding("plus", "more", show=False),
        Binding("ctrl+r", "sync", show=False),
        Binding("escape", "clear", show=False),
        Binding("question_mark", "help", "help"),
        Binding("q", "quit", "quit", show=False),
    ]

    def __init__(self, acct):
        super().__init__()
        self.acct, self.folder = acct, "Mailbox"
        self.msgs, self.convs, self.selected = [], [], set()
        self.query_text, self.query_dict, self.everywhere, self.single, self.n = "", None, False, False, PAGE
        self.token = 0  # bumped on every switch, so a late sync result for another view is dropped
        self.syncing, self.health, self.synced_at, self.loaded_once = False, "", None, False
        self.raws, self.undo = {}, None
        self.known = set()  # every mail seen in this view, so undo and moves never count as new mail
        self.pane_acct = acct.name
        self.preview_timer = self.read_timer = None

    def compose(self) -> ComposeResult:
        yield Header(id="header")
        with Horizontal(id="body"):
            yield FolderPane(id="folders")
            with Vertical(id="listcol"):
                with Horizontal(id="searchrow"):
                    yield SearchBar(id="search", placeholder="from:kavitha has:attachment exam")
                    yield Static(id="scope")
                yield MailList(id="list")
            yield ConvView(id="preview")
        yield Status(id="status")
        yield Footer()

    @property
    def outbox(self):
        return self.app.outbox

    def on_mount(self):
        self.query_one("#list").focus()
        self.set_interval(1, self.tick)
        self.set_interval(SYNC_EVERY, self.action_sync)
        self.show_cached()
        if self.app.config_note:
            self.say(self.app.config_note, True)
        if any(a.special is None for a in self.app.accounts.values()):
            self.first_run()
        else:
            self.action_sync()

    def on_resize(self, event):
        self.set_class(self.size.width < WIDE, "narrow")
        self.call_after_refresh(self.relayout)

    # --- status, header --------------------------------------------------------------------------

    def say(self, text, error=False):
        self.query_one(Status).say(text, error)

    def paint_header(self):
        left = f"homer · {self.acct.label} · {self.folder}"
        if self.query_text:
            left += f' · "{self.query_text}"' + (" in all folders" if self.everywhere else "")
        right = []
        if self.selected:
            right.append(f"{len(self.selected)} selected")
        unread = sum(c["unread"] for c in self.convs)
        if self.query_text:
            right.append(f"{sum(1 for c in self.convs if c['msgs'])} results")
        elif unread:
            right.append(f"{unread} unread")
        busy = sum(o["state"] in ("wait", "sending") for o in self.outbox)
        if busy:
            right.append(f"{busy} sending")
        if self.health:
            right.append(self.health)
        elif self.syncing:
            right.append("syncing…")
        elif self.synced_at:
            mins = int((time.time() - self.synced_at) // 60)
            right.append("synced just now" if mins == 0 else f"synced {mins}m ago")
        self.query_one(Header).set(left, " · ".join(right))

    # --- loading -----------------------------------------------------------------------------------

    def show_cached(self):
        self.msgs = [] if self.query_dict else self.acct.cached(self.folder)
        self.rebuild()

    @work(thread=True, group="setup")
    def first_run(self):
        for a in self.app.accounts.values():
            if a.special is None:
                try:
                    a.load_special()
                except CAUGHT as e:
                    self.app.call_from_thread(self.say, f"! {a.label}: {err(e)}", True)
        self.app.call_from_thread(self.action_sync)

    def action_sync(self):
        if self.syncing:
            return
        self.syncing = True
        self.paint_header()
        self.load(self.acct, self.folder, self.n, self.query_dict, self.everywhere, self.token)

    @work(thread=True, group="sync")
    def load(self, acct, folder, n, query, everywhere, token):
        try:
            if acct.special is None:
                acct.load_special()
            msgs = acct.fetch(folder, n, query, everywhere)
            if not query:
                acct.store(folder, msgs)
        except CAUGHT as e:
            self.app.call_from_thread(self.loaded, token, None, e)
        else:
            self.app.call_from_thread(self.loaded, token, msgs, None)

    def loaded(self, token, msgs, error):
        self.syncing = False
        if token != self.token:
            self.action_sync()  # the view changed while this ran; fetch what's on screen now
            return
        if error is not None:
            text = err(error)
            if "login" in text or "AUTHENTICATIONFAILED" in text or "logged in" in text:
                self.health = f"{self.acct.label}: sign in again (homer login -a {self.acct.name})"
                self.say("! " + self.health, True)
            else:
                self.health = "offline" if isinstance(error, OSError) or "request failed" in text else "sync failed"
                self.say(f"! {text}. Showing cached mail" if self.health == "offline" else f"! {text}", True)
            self.loaded_once = True  # so an empty list says "Nothing in", not "Loading" forever
            self.rebuild()
            return
        fresh = [m for m in msgs if m["msgid"] not in self.known and m["unread"]]
        before, self.known = self.known, self.known | {m["msgid"] for m in msgs}
        self.health, self.synced_at = "", time.time()
        if self.loaded_once and before and fresh and not self.query_dict:
            self.say(f"{len(fresh)} new in {self.folder}")
        self.msgs, self.loaded_once = msgs, True
        if self.query_dict:
            self.say("")  # the header carries the result count
        for o in [o for o in self.outbox if o["state"] == "sent" and o.get("done")]:
            self.outbox.remove(o)  # the real sent mail is in the listing now
        self.rebuild()

    def switch(self, acct=None, folder=None):
        self.acct = acct or self.acct
        self.folder = folder or self.folder
        self.query_text, self.query_dict, self.everywhere = "", None, False
        self.selected.clear()
        self.token += 1
        self.health, self.synced_at, self.loaded_once, self.n = "", None, False, PAGE
        self.known = set()
        self.show_cached()
        self.query_one("#list", MailList).highlighted = 0 if self.convs else None
        self.action_sync()

    # --- the list ------------------------------------------------------------------------------------

    def current(self):
        lst = self.query_one("#list", MailList)
        if lst.highlighted is None or lst.highlighted >= len(self.convs):
            return None
        return self.convs[lst.highlighted]

    def targets(self):
        sel = [c for c in self.convs if c["key"] in self.selected and c["msgs"]]
        cur = self.current()
        return sel or ([cur] if cur and cur["msgs"] else [])

    def with_outbox(self, convs):
        """Mail being sent shows as its own row, or on its conversation's row for a reply (W10)."""
        if self.folder != "Mailbox" or self.query_dict:
            return convs
        top = []
        for o in self.outbox:
            if o["acct"] is not self.acct:
                continue
            hit = next((c for c in convs if c["key"] == o["st"].get("conv")), None)
            if hit:
                convs.remove(hit)
                top.append({**hit, "out": o})
            else:
                msg = o["msg"]
                to = msg["To"].addresses[0] if msg["To"] and msg["To"].addresses else None
                top.append({"key": "out:" + o["id"], "msgs": [], "unread": False, "when": datetime.now(timezone.utc),
                            "subject": msg["Subject"] or "", "attach": bool(o["st"]["attach"]), "mine": True,
                            "who": "To " + ((to.display_name or to.addr_spec) if to else "(no one)"), "out": o})
        return top[::-1] + convs

    def row(self, c, width):
        return list_row(c, width, self.acct.me, c["key"] in self.selected)

    def list_width(self):
        return self.query_one("#list", MailList).scrollable_content_region.width or 80

    def rebuild(self):
        lst = self.query_one("#list", MailList)
        old = self.current()
        old_key, old_index = (old["key"] if old else None), (lst.highlighted or 0)
        self.convs = self.with_outbox(store.conversations(self.msgs, self.acct.me, self.single))
        keys = {c["key"] for c in self.convs}
        self.selected &= keys
        width = self.list_width()
        if self.convs:
            lst.set_options([Option(self.row(c, width), id=c["key"]) for c in self.convs])
            index = next((i for i, c in enumerate(self.convs) if c["key"] == old_key), min(old_index, len(self.convs) - 1))
            lst.highlighted = index
        else:
            empty = ("No results." if self.query_dict else f"Loading {self.folder}…" if self.syncing
                     and not self.loaded_once else f"Nothing in {self.folder}.")
            lst.set_options([Option(Text("   " + empty, "dim"), disabled=True)])
            self.query_one("#preview", ConvView).render_blocks([])
        self.paint_header()

    def repaint(self, key):
        c = next((c for c in self.convs if c["key"] == key), None)
        if c:
            self.query_one("#list", MailList).replace_option_prompt(key, self.row(c, self.list_width()))

    @on(OptionList.OptionHighlighted, "#list")
    def highlighted(self):
        for t in (self.preview_timer, self.read_timer):
            if t:
                t.stop()
        self.preview_timer = self.set_timer(0.12, self.preview)
        c = self.current()
        if c and c["unread"] and not self.has_class("narrow"):
            self.read_timer = self.set_timer(READ_AFTER, lambda key=c["key"]: self.seen(key))  # D18

    def seen(self, key):
        c = self.current()
        if c and c["key"] == key and c["unread"]:
            self.set_read([c], True, quiet=True)

    def preview(self):
        view = self.query_one("#preview", ConvView)
        c = self.current()
        if c is None or self.has_class("narrow"):
            view.render_blocks([])
            return
        if not c["msgs"]:  # mail still in the outbox
            o = c["out"]
            t = Text()
            t.append((c["subject"] or "(no subject)") + "\n", "bold")
            t.append(f"me → {c['who'][3:]} · " + {"wait": "waiting to send", "sending": "sending",
                                                   "sent": "sent", "failed": "not sent"}[o["state"]] + "\n\n", "dim")
            t.append(o["st"]["text"])
            view.render_blocks([t])
            return
        last = len(c["msgs"]) - 1
        width = view.scrollable_content_region.width or 50
        view.render_blocks(conv_blocks(c, self.acct, self.raws, {last}, width)[0])
        m = c["msgs"][last]
        if loc(self.acct, m) not in self.raws:
            self.fetch_preview(self.acct, m, c["key"])

    @work(thread=True, group="preview", exclusive=True)
    def fetch_preview(self, acct, m, key):
        try:
            msg = parse(acct.call("fetch_raw", m["folder"], m["id"]))
        except CAUGHT as e:
            self.app.call_from_thread(self.say, f"! couldn't load the mail: {err(e)}", True)
            return
        self.app.call_from_thread(self.got_preview, loc(acct, m), msg, key)

    def got_preview(self, where, msg, key):
        self.raws[where] = msg
        c = self.current()
        if c and c["key"] == key:
            self.preview()

    def fetch_all(self, acct, msgs, then):
        """Load every mail of a conversation, then call then() on the UI thread."""
        missing = [m for m in msgs if loc(acct, m) not in self.raws]
        if not missing:
            then()
            return
        self._fetch_all(acct, missing, then)

    @work(thread=True, group="fetch")
    def _fetch_all(self, acct, missing, then):
        try:
            got = {loc(acct, m): parse(acct.call("fetch_raw", m["folder"], m["id"])) for m in missing}
        except CAUGHT as e:
            self.app.call_from_thread(self.say, f"! couldn't load the mail: {err(e)}", True)
            return
        self.app.call_from_thread(lambda: (self.raws.update(got), then()))

    # --- opening ---------------------------------------------------------------------------------------

    @on(OptionList.OptionSelected, "#list")
    def opened(self):
        c = self.current()
        if c is None:
            return
        out = c.get("out")
        if out and out["state"] == "failed":
            self.outbox.remove(out)
            self.rebuild()
            self.compose_screen(out["st"])
            return
        if not c["msgs"]:
            return
        if self.folder == "Drafts":
            m = c["msgs"][-1]
            self.fetch_all(self.acct, [m], lambda: self.open_draft(m))
            return
        if c["unread"]:
            self.set_read([c], True, quiet=True)
        self.app.push_screen(ReaderScreen(self, c), self.reader_done)

    def reader_done(self, key):
        actions = {"r": "reply('reply')", "R": "reply('all')", "F": "reply('forward')", "e": "move('Archive')",
                   "d": "move('Trash')", "m": "toggle_read", "M": "pick_move"}
        if key in actions:
            self.run_action(actions[key])

    # --- moving, marking, undo (D15, D16) ----------------------------------------------------------

    def action_move(self, dest):
        convs = self.targets()
        if not convs:
            return
        if dest == self.folder and not self.everywhere:
            self.say(f"Already in {dest}" + (". homer doesn't delete mail for good (TUI.md)" if dest == "Trash" else ""))
            return
        try:
            target = self.acct.target(dest)
        except ValueError as e:
            self.say(f"! {e}", True)
            return
        skip = set(self.acct.folders().get(dest, []))
        jobs = {}
        for c in convs:
            for m in c["msgs"]:
                for f, i in m["locs"]:
                    if f not in skip:
                        jobs.setdefault(f, []).append(i)
        if not jobs:
            self.say(f"Nothing to move: these results are already in {dest}'s folders. "
                     "Search the folder itself instead of all folders")
            return
        verb = {"Archive": "Archived", "Trash": "Deleted"}.get(dest, "Moved")
        label = f'"{convs[0]["subject"]}"' if len(convs) == 1 else f"{len(convs)} conversations"
        backup = list(self.msgs)
        gone = {(f, i) for f, ids in jobs.items() for i in ids}
        self.msgs = [m for m in self.msgs if (m["folder"], m["id"]) not in gone]
        self.selected.clear()
        self.rebuild()
        self.say(f"{verb} {label}" + ("" if verb != "Moved" else f" to {dest}") + " · u undo")
        self.run_moves(self.acct, jobs, target, verb, label, backup, self.token)

    @work(thread=True, group="act")
    def run_moves(self, acct, jobs, target, verb, label, backup, token):
        done = []
        try:
            for src, ids in jobs.items():
                new = acct.call("move", src, ids, target) or []
                done.append((target, [i for i in new if i], src))
        except CAUGHT as e:
            for dst, ids, src in done:  # put back what did move
                try:
                    acct.call("move", dst, ids, src)
                except CAUGHT:
                    pass
            self.app.call_from_thread(self.move_failed, verb, label, e, backup, token)
            return
        self.app.call_from_thread(setattr, self, "undo", ("move", acct, done, label))

    def move_failed(self, verb, label, e, backup, token):
        if token == self.token:
            self.msgs = backup
            self.rebuild()
        self.say(f"! Couldn't {verb.lower().rstrip('d')} {label}: {err(e)}. Put back.", True)

    def patch(self, locs, **changes):
        locs = {tuple(x) for x in locs}
        for m in self.msgs:
            if (m["folder"], m["id"]) in locs:
                m.update(changes)

    def set_read(self, convs, read, quiet=False):
        jobs, locs = {}, []
        for c in convs:
            for m in c["msgs"]:
                if m["unread"] == read:
                    locs += m["locs"]
                    for f, i in m["locs"]:
                        jobs.setdefault(f, []).append(i)
        if not jobs:
            return
        self.patch(locs, unread=not read)
        self.rebuild()
        if not quiet:
            label = f'"{convs[0]["subject"]}"' if len(convs) == 1 else f"{len(convs)} conversations"
            self.say(f"Marked {label} {'read' if read else 'unread'} · u undo")
            self.undo = ("mark", self.acct, jobs, read, locs)
        self.run_mark(self.acct, jobs, read, locs)

    @work(thread=True, group="act")
    def run_mark(self, acct, jobs, read, locs):
        try:
            for f, ids in jobs.items():
                acct.call("mark", f, ids, read)
        except CAUGHT as e:
            self.app.call_from_thread(self.mark_failed, locs, read, e)

    def mark_failed(self, locs, read, e):
        self.patch(locs, unread=read)
        self.rebuild()
        self.say(f"! Couldn't mark it {'read' if read else 'unread'}: {err(e)}. Put back.", True)

    def action_toggle_read(self):
        convs = self.targets()
        if convs:
            self.set_read(convs, any(c["unread"] for c in convs))
            self.selected.clear()

    def action_undo(self):
        u, self.undo = self.undo, None
        if u is None:
            self.say("Nothing to undo")
        elif u[0] == "send":
            o = u[1]
            if o["state"] != "wait":
                self.say(f'Too late, "{o["msg"]["Subject"]}" is already {o["state"]}')
                return
            self.outbox.remove(o)
            self.rebuild()
            self.compose_screen(o["st"])
            self.say("Sending cancelled")
        elif u[0] == "discard":
            self.compose_screen(u[1])
            if u[2]:
                self.run_undo_moves(u[2][0], u[2][1], "Draft")
        elif u[0] == "mark":
            _, acct, jobs, read, locs = u
            if acct is self.acct:
                self.patch(locs, unread=read)
                self.rebuild()
            self.run_mark(acct, {f: ids for f, ids in jobs.items()}, not read, locs)
            self.say("Undone")
        elif u[0] == "move":
            self.run_undo_moves(u[1], u[2], u[3])

    @work(thread=True, group="act")
    def run_undo_moves(self, acct, done, label):
        try:
            for dst, ids, src in done:
                if ids:
                    acct.call("move", dst, ids, src)
        except CAUGHT as e:
            self.app.call_from_thread(self.say, f"! Couldn't undo: {err(e)}", True)
            return
        self.app.call_from_thread(self.say, f"Put back {label}")
        self.app.call_from_thread(self.action_sync)

    def action_pick_move(self):
        convs = self.targets()
        if not convs:
            return
        choices = [f for f in self.acct.folders() if f not in (self.folder, "Drafts")]
        label = f'"{convs[0]["subject"]}"' if len(convs) == 1 else f"{len(convs)} conversations"
        self.app.push_screen(MovePicker(f"Move {label} to", choices),
                             lambda dest: dest and self.action_move(dest))

    def action_select(self):
        c = self.current()
        if c is None or not c["msgs"]:
            return
        self.selected ^= {c["key"]}
        self.repaint(c["key"])
        self.query_one("#list", MailList).action_cursor_down()
        self.paint_header()

    def action_clear(self):
        if self.selected:
            self.selected.clear()
            self.rebuild()
        elif self.query_dict:
            self.query_text, self.query_dict, self.everywhere = "", None, False
            self.token += 1
            self.show_cached()
            self.action_sync()

    def action_single(self):
        self.single = not self.single
        self.rebuild()
        self.say("One row per mail" if self.single else "One row per conversation")

    def action_more(self):
        self.n += PAGE
        self.say(f"Loading {PAGE} older…")
        self.action_sync()

    def action_scroll(self, direction):
        view = self.query_one("#preview", ConvView)
        view.scroll_page_down() if direction > 0 else view.scroll_page_up()

    def action_browser(self):
        c = self.current()
        msg = self.raws.get(loc(self.acct, c["msgs"][-1])) if c and c["msgs"] else None
        if msg is None:
            self.say("Nothing to open yet")
            return
        open_in_browser(msg)
        self.say("Opened in the browser")

    LIST_ONLY = {"compose", "reply", "move", "toggle_read", "pick_move", "select", "undo", "scroll", "search",
                 "folders", "single", "more", "sync", "clear", "browser"}

    def check_action(self, action, parameters):
        """The list's keys are off while the folder pane or the search bar has the focus; the pane's own
        keys (n E R D f) call screen actions too and must stay on."""
        if action in self.LIST_ONLY and isinstance(self.focused, (FolderPane, Input)):
            return False
        return True

    # --- search (W13, D19) ---------------------------------------------------------------------------

    def action_search(self):
        row = self.query_one("#searchrow")
        row.add_class("on")
        bar = self.query_one("#search", SearchBar)
        bar.value = self.query_text
        self.paint_scope()
        bar.focus()
        self.say("from: to: subject: is:unread is:read has:attachment before:2026-10-01 after:2026-09-01 words")

    def paint_scope(self):
        self.query_one("#scope", Static).update(
            Text(" all folders · Tab: this folder " if self.everywhere else " this folder · Tab: all ", "dim"))

    def action_search_scope(self):
        self.everywhere = not self.everywhere
        self.paint_scope()

    def action_search_cancel(self):
        self.query_one("#searchrow").remove_class("on")
        self.query_one("#list").focus()
        self.say("")

    @on(Input.Submitted, "#search")
    def run_search(self, event):
        text = event.value.strip()
        if not text:
            self.action_search_cancel()
            self.everywhere = False
            self.action_clear()
            return
        try:
            q = parse_query(text)
        except ValueError as e:
            self.say(f"! {e}", True)
            return
        self.action_search_cancel()
        self.query_text, self.query_dict = text, q
        self.token += 1
        self.msgs = []
        self.selected.clear()
        self.rebuild()
        self.say("Searching…")
        self.action_sync()

    # --- folders pane (W2, W15-W17) -------------------------------------------------------------------

    def action_folders(self):
        pane = self.query_one("#folders", FolderPane)
        if self.has_class("panes"):
            self.remove_class("panes")
            self.query_one("#list").focus()
            self.call_after_refresh(self.relayout)
            return
        self.pane_acct = self.acct.name
        self.add_class("panes")
        self.fill_pane()
        pane.focus()
        self.call_after_refresh(self.relayout)
        self.refresh_others()

    def relayout(self):
        """Rows and the preview are laid out for a width; redo them when the pane changes it."""
        self.rebuild()
        self.preview()

    def unread_in(self, acct, folder):
        return sum(c["unread"] for c in store.conversations(acct.cached(folder), acct.me))

    def fill_pane(self, keep=None):
        pane = self.query_one("#folders", FolderPane)
        opts, at = [], 0
        for name, a in self.app.accounts.items():
            open_ = name == self.pane_acct
            count = self.unread_in(a, "Mailbox")
            label = Text(("▾ " if open_ else "▸ ") + a.label, "bold")
            if count and not open_:
                label.append(f"  {count}", STYLE["unread"])
            opts.append(Option(label, id=f"a\0{name}"))
            if not open_:
                continue
            for f in a.folders():
                if f == (a.custom() or [None])[0]:
                    opts.append(Option(Text("  ─ yours ─", "dim"), disabled=True))
                count = self.unread_in(a, f) if f not in ("Drafts", "Trash") else 0
                t = Text("  " + f)
                if count:
                    t.append(f"  {count}", STYLE["unread"])
                opts.append(Option(t, id=f"f\0{name}\0{f}"))
                if name == self.acct.name and f == (keep or self.folder):
                    at = len(opts) - 1
        opts.append(Option(Text("+ new folder", "dim"), id="new"))
        pane.set_options(opts)
        pane.highlighted = at

    @work(thread=True, group="others", exclusive=True)
    def refresh_others(self):
        for a in self.app.accounts.values():
            if a is not self.acct:
                try:
                    if a.special is None:
                        a.load_special()
                    a.store("Mailbox", a.fetch("Mailbox", PAGE))
                except CAUGHT:
                    pass  # counts stay as last cached
        self.app.call_from_thread(lambda: self.has_class("panes") and self.fill_pane())

    def pane_pick(self):
        pane = self.query_one("#folders", FolderPane)
        opt = pane.highlighted_option
        return opt.id.split("\0") if opt and opt.id else []

    @on(OptionList.OptionSelected, "#folders")
    def pane_selected(self):
        pick = self.pane_pick()
        if pick and pick[0] == "a":
            self.pane_acct = pick[1]
            self.fill_pane()
        elif pick and pick[0] == "f":
            self.remove_class("panes")
            self.query_one("#list").focus()
            self.switch(self.app.accounts[pick[1]], pick[2])
            self.call_after_refresh(self.relayout)
        elif pick == ["new"]:
            self.action_new_folder()

    def pane_folder(self):
        pick = self.pane_pick()
        return (self.app.accounts[pick[1]], pick[2]) if pick and pick[0] == "f" else (None, None)

    def action_new_folder(self):
        acct = self.app.accounts[self.pane_acct]

        def check(name):
            if not name:
                return "Type a name"
            if name in acct.folders():
                return f"{acct.label} already has a folder called {name}"
            return None

        def named(name):
            if name:
                self.app.push_screen(MapScreen(acct, name, new=True), lambda ok: ok and self.mapped(acct, name))

        self.app.push_screen(NameModal(f"New folder in {acct.label}", "Enter next: choose what feeds it", check), named)

    def mapped(self, acct, folder):
        if folder in acct.maps.get(acct.name, {}) and not acct.folders()[folder]:
            self.say(f"Saved. {folder} shows nothing until a folder feeds it (E)")
        elif folder in store.BASE and folder not in acct.maps.get(acct.name, {}):
            self.say(f"{folder} is back to homer's default")
        else:
            self.say(f"Saved what feeds {folder}")
        self.fill_pane(keep=folder)
        if acct is self.acct and folder == self.folder:
            self.action_sync()

    def action_edit_folder(self):
        acct, folder = self.pane_folder()
        if folder in store.EDITABLE or acct and folder in acct.custom():
            self.app.push_screen(MapScreen(acct, folder), lambda ok: ok and self.mapped(acct, folder))
        elif folder:
            self.say(f"{folder} is found by homer and can't be remapped")

    def action_rename_folder(self):
        pick = self.pane_pick()
        if pick and pick[0] == "a":
            self.rename_account(self.app.accounts[pick[1]])
            return
        acct, folder = self.pane_folder()
        if not folder or folder not in acct.custom():
            self.say("Only accounts and your own folders can be renamed")
            return

        def check(name):
            return "Type a name" if not name else f"{name} already exists" if name in acct.folders() else None

        def done(name):
            if name:
                acct.rename(folder, name)
                if acct is self.acct and self.folder == folder:
                    self.folder = name
                    self.paint_header()
                self.fill_pane(keep=name)
                self.say(f"Renamed {folder} to {name}")

        self.app.push_screen(NameModal(f"Rename {folder}", "Enter rename", check, folder), done)

    def rename_account(self, acct):
        """Only the name homer shows changes; the config key stays what -a and homer login take."""
        def check(name):
            taken = {a.label for a in self.app.accounts.values() if a is not acct}
            return f"Another account is called {name}" if name in taken else None

        def done(name):
            if name is None:
                return
            old, acct.label = acct.label, name or acct.name
            self.app.state["names"] = {a.name: a.label for a in self.app.accounts.values() if a.label != a.name}
            store.save_state(self.app.state)
            self.fill_pane()
            self.paint_header()
            self.say(f"Renamed {old} to {acct.label}")

        self.app.push_screen(NameModal(f"Rename the account {acct.label}",
                                       f"Enter rename (empty: back to {acct.name})", check, acct.label), done)

    def action_delete_folder(self):
        acct, folder = self.pane_folder()
        if not folder or folder not in acct.custom():
            self.say("Only your own folders can be deleted")
            return
        n = len(acct.cached(folder))
        srcs = " and ".join(acct.folders()[folder])
        text = (f"The folder is removed from homer only. Its {n} mails stay on the server, "
                f"and {srcs} feed Archive again.")

        def done(ok):
            if ok:
                acct.delete(folder)
                if acct is self.acct and self.folder == folder:
                    self.switch(folder="Mailbox")
                self.fill_pane()
                self.say(f"Deleted the folder {folder}. Its mail is still on the server")

        self.app.push_screen(ConfirmModal(f'Delete folder "{folder}"?', text), done)

    # --- writing (W7-W11) -------------------------------------------------------------------------------

    def compose_screen(self, st):
        self.app.push_screen(ComposeScreen(st), self.compose_done)

    def new_state(self, acct, kind, **kw):
        st = {"acct": acct, "kind": kind, "orig": None, "to": "", "cc": "", "bcc": "", "subject": "", "attach": [],
              "text": "", "conv": None, "draft": None, "headers": {}, "path": DRAFTS / f"{uuid.uuid4().hex}.txt"}
        st.update(kw)
        return st

    def action_compose(self):
        self.compose_screen(self.new_state(self.acct, "new"))

    def action_reply(self, kind):
        c = self.current()
        if c is None or not c["msgs"]:
            return
        m = c["msgs"][-1]
        self.fetch_all(self.acct, [m], lambda: self.start_reply(kind, c, self.raws[loc(self.acct, m)]))

    def start_reply(self, kind, c, orig):
        subject = orig["Subject"] or ""
        if kind == "forward":
            st = self.new_state(self.acct, kind, orig=orig, conv=c["key"],
                                subject=subject if subject.lower().startswith(("fwd:", "fw:")) else f"Fwd: {subject}")
        else:
            to, cc = store.reply_recipients(self.acct.acct, orig, kind == "all")
            st = self.new_state(self.acct, kind, orig=orig, conv=c["key"], to=", ".join(to), cc=", ".join(cc),
                                subject=subject if subject.lower().startswith("re:") else f"Re: {subject}")
        self.compose_screen(st)

    def open_draft(self, m):
        msg = self.raws[loc(self.acct, m)]
        html = msg.get_body(("plain",)) is None and msg.get_body(("html",)) is not None
        st = self.new_state(self.acct, "new", to=msg["To"] or "", cc=msg["Cc"] or "", bcc=msg["Bcc"] or "",
                            subject=msg["Subject"] or "", text=store.strip_footer(body_text(msg)),
                            draft=(m["folder"], m["id"]),
                            headers={h: msg[h] for h in ("In-Reply-To", "References") if msg[h]})
        self.compose_screen(st)
        if html:
            self.say("This draft was HTML; it's shown as text and will be saved as text")

    def compose_done(self, result):
        act, st, msg = result["action"], result["state"], result.get("msg")
        subject = st["subject"] or "(no subject)"
        if act == "send":
            to = msg["To"].addresses[0] if msg["To"] and msg["To"].addresses else None
            o = {"id": uuid.uuid4().hex, "acct": st["acct"], "msg": msg, "st": st, "state": "wait",
                 "left": SEND_DELAY, "to": (to.display_name or to.addr_spec) if to else "", "subject": subject}
            self.outbox.append(o)
            self.undo = ("send", o)
            if self.folder != "Mailbox" or st["acct"] is not self.acct:
                self.switch(st["acct"], "Mailbox")
            self.rebuild()
            self.query_one("#list", MailList).highlighted = 0
            self.say(f'Sending "{subject}" to {o["to"]} in {SEND_DELAY}s · u undo')
        elif act in ("draft", "close"):
            self.save_draft(st, msg)
        elif act == "discard":
            st["path"].unlink(missing_ok=True)
            if st["draft"]:
                self.discard_draft(st)
            else:
                self.undo = ("discard", st, None)
                self.say(f'Discarded "{subject}" · u undo')
        else:
            st["path"].unlink(missing_ok=True)
            self.say("Nothing written, nothing saved")

    @work(thread=True, group="act")
    def save_draft(self, st, msg):
        acct = st["acct"]
        try:
            acct.call("save_draft", msg)
            if st["draft"]:
                acct.call("move", st["draft"][0], [st["draft"][1]], acct.target("Trash"))
        except CAUGHT as e:
            self.app.call_from_thread(self.say, f"! Couldn't save to Drafts: {err(e)}. Your text is in {st['path']}",
                                      True)
            return
        st["path"].unlink(missing_ok=True)
        self.app.call_from_thread(self.say, f'Saved "{st["subject"] or "(no subject)"}" to Drafts')
        if self.folder == "Drafts":
            self.app.call_from_thread(self.action_sync)

    @work(thread=True, group="act")
    def discard_draft(self, st):
        acct = st["acct"]
        try:
            trash = acct.target("Trash")
            new = acct.call("move", st["draft"][0], [st["draft"][1]], trash) or []
        except CAUGHT as e:
            self.app.call_from_thread(self.say, f"! Couldn't discard the draft: {err(e)}", True)
            return
        done = [(trash, [i for i in new if i], st["draft"][0])]
        st["draft"] = None  # undo reopens the text; the old draft comes back from Trash
        self.app.call_from_thread(setattr, self, "undo", ("discard", st, (acct, done)))
        self.app.call_from_thread(self.say, f'Discarded "{st["subject"] or "(no subject)"}" · u undo')
        self.app.call_from_thread(self.action_sync)

    # --- the outbox (W10, D12, D13) ------------------------------------------------------------------

    def tick(self):
        for o in list(self.outbox):
            if o["state"] != "wait":
                continue
            o["left"] -= 1
            if o["left"] <= 0:
                o["state"] = "sending"
                self.say(f'Sending "{o["subject"]}" to {o["to"]}…')
                self.send(o)
            elif self.undo and self.undo[0] == "send" and self.undo[1] is o:
                self.say(f'Sending "{o["subject"]}" to {o["to"]} in {o["left"]}s · u undo')
            self.repaint_out(o)
        if self.synced_at and not self.syncing:
            self.paint_header()

    def repaint_out(self, o):
        key = next((c["key"] for c in self.convs if c.get("out") is o), None)
        if key:
            self.convs = [{**c, "out": o} if c["key"] == key else c for c in self.convs]
            self.repaint(key)
            self.paint_header()

    @work(thread=True, group="send")
    def send(self, o):
        try:
            send_now(o)
        except CAUGHT as e:
            o["state"], o["err"] = "failed", err(e)
            self.app.call_from_thread(self.sent, o)
            return
        o["state"] = "sent"
        self.app.call_from_thread(self.sent, o)

    def sent(self, o):
        self.repaint_out(o)
        if o["state"] == "failed":
            self.say(f'! Not sent: "{o["subject"]}" to {o["to"]}. {o["err"]}. Enter on it to fix', True)
            return
        o["st"]["path"].unlink(missing_ok=True)
        self.say(f'✓ Sent "{o["subject"]}" to {o["to"]}')
        o["done"] = True
        self.set_timer(4, self.action_sync)  # the next listing has the real sent mail

    def action_quit(self):
        waiting = [o for o in self.outbox if o["state"] in ("wait", "sending", "failed")]
        if not waiting:
            self.app.exit()
            return
        for o in waiting:
            if o["state"] == "wait":
                o["state"] = "quitting"  # tick leaves it alone; QuitScreen sends it
        self.app.push_screen(QuitScreen(waiting))

    def action_help(self):
        which = "folders" if isinstance(self.focused, FolderPane) else "list"
        self.app.push_screen(HelpScreen(f"Keys · {which}", HELP[which]))


def send_now(o):
    """Send an outbox entry, then move the draft it came from (if any) to Trash. Raises on failure."""
    acct, msg = o["acct"], o["msg"]
    acct.call("send", msg, [a.addr_spec for h in ("To", "Cc", "Bcc") if msg[h] for a in msg[h].addresses])
    if o["st"]["draft"]:
        try:
            acct.call("move", o["st"]["draft"][0], [o["st"]["draft"][1]], acct.target("Trash"))
        except CAUGHT:
            pass  # sent is what matters; the stale draft just stays in Drafts


# --- conversation, full width (W5, W6) ---------------------------------------------------------------

class ReaderScreen(Screen):
    BINDINGS = [
        Binding("j,down", "scroll(1)", show=False), Binding("k,up", "scroll(-1)", show=False),
        Binding("space,pagedown", "page(1)", show=False), Binding("b,pageup", "page(-1)", show=False),
        Binding("g", "top", show=False), Binding("G", "bottom", show=False),
        Binding("n", "step(1)", "next mail", show=False), Binding("p", "step(-1)", show=False),
        Binding("enter", "toggle", show=False),
        Binding("r", "back('r')", "reply"), Binding("R", "back('R')", "all"), Binding("F", "back('F')", "forward"),
        Binding("e", "back('e')", "archive"), Binding("d", "back('d')", show=False),
        Binding("m", "back('m')", show=False), Binding("M", "back('M')", show=False),
        Binding("s", "save", "save"), Binding("o", "links", "open link"), Binding("v", "browser", "browser"),
        Binding("question_mark", "help", "help"), Binding("q,escape", "back('')", "back"),
    ]

    def __init__(self, main, conv):
        super().__init__()
        self.main, self.conv, self.acct = main, conv, main.acct
        last = len(conv["msgs"]) - 1
        self.cur = last
        self.opened = {last} | {i for i, m in enumerate(conv["msgs"]) if m["unread"]}
        self.links, self.parts = [], []

    def compose(self) -> ComposeResult:
        yield Header()
        yield ConvView(id="reader")
        yield Status()
        yield Footer()

    def on_mount(self):
        n = len(self.conv["msgs"])
        self.query_one(Header).set(f"homer · {self.acct.label} · {self.main.folder} › {self.conv['subject']}",
                                   f"{n} mail{'s' if n != 1 else ''}")
        self.paint(scroll=True)
        self.main.fetch_all(self.acct, self.conv["msgs"], lambda: self.is_attached and self.paint(scroll=True))
        self.query_one("#reader").focus()

    def paint(self, scroll=False):
        view = self.query_one("#reader", ConvView)
        width = (view.scrollable_content_region.width or self.size.width) - 2
        blocks, self.links, self.parts = conv_blocks(self.conv, self.acct, self.main.raws, self.opened, width,
                                                     full=True, cursor=self.cur)
        view.render_blocks(blocks, self.cur if scroll else None)

    def action_scroll(self, d):
        self.query_one("#reader").scroll_relative(y=d, animate=False)

    def action_page(self, d):
        view = self.query_one("#reader")
        view.scroll_page_down() if d > 0 else view.scroll_page_up()

    def action_top(self):
        self.query_one("#reader").scroll_home(animate=False)

    def action_bottom(self):
        self.query_one("#reader").scroll_end(animate=False)

    def action_step(self, d):
        self.cur = max(0, min(len(self.conv["msgs"]) - 1, self.cur + d))
        self.paint(scroll=True)

    def action_toggle(self):
        self.opened ^= {self.cur}
        self.paint(scroll=True)

    def action_back(self, key):
        self.dismiss(key)

    def action_save(self):
        if not self.parts:
            self.query_one(Status).say("No attachments")
            return

        def saved(paths):
            if paths:
                self.query_one(Status).say("Saved " + ", ".join(p.name for p in paths) + f" to {DOWNLOADS}")

        if len(self.parts) == 1:
            saved(save_attachments(self.parts, DOWNLOADS))
        else:
            self.app.push_screen(AttachPicker(self.parts), saved)

    def action_links(self):
        if not self.links:
            self.query_one(Status).say("No links")
        elif len(self.links) == 1:
            open_external(self.links[0])
            self.query_one(Status).say(f"Opened {self.links[0]}")
        else:
            self.app.push_screen(LinkPicker(self.links), lambda u: u and self.query_one(Status).say(u))

    def action_browser(self):
        msg = self.main.raws.get(loc(self.acct, self.conv["msgs"][self.cur]))
        if msg is not None:
            open_in_browser(msg)
            self.query_one(Status).say("Opened in the browser")

    def action_help(self):
        self.app.push_screen(HelpScreen("Keys · conversation", HELP["reader"]))


# --- compose (W7, W8, W9) ----------------------------------------------------------------------------

class FromField(Static, can_focus=True):
    """The sending account: ← → or space cycles through accounts on a new mail."""
    BINDINGS = [Binding("left", "cycle(-1)", show=False), Binding("right,space", "cycle(1)", show=False)]

    def action_cycle(self, d):
        self.screen.cycle_from(d)


class ComposeScreen(Screen):
    BINDINGS = [
        Binding("ctrl+s", "send", "send", priority=True),
        Binding("tab", "field(1)", "next field", show=False),
        Binding("shift+tab", "field(-1)", show=False),
        Binding("down", "arrow(1)", show=False), Binding("up", "arrow(-1)", show=False),
        Binding("escape", "commands", "commands"),
        Binding("s", "send_key", "send"), Binding("b", "body", "edit body"), Binding("a", "attach", "attach"),
        Binding("X", "unattach", "remove attachment"), Binding("d", "close('draft')", "save draft"),
        Binding("q", "close('close')", "close"), Binding("D", "close('discard')", "discard"),
        Binding("question_mark", "help", show=False),
    ]
    FIELDS = ("to", "cc", "bcc", "subject")
    COMMANDS = {"send_key", "body", "attach", "unattach", "close"}  # single keys: only outside the fields

    def __init__(self, st):
        super().__init__()
        self.st, self.no_subject_ok = st, False
        DRAFTS.mkdir(parents=True, exist_ok=True)
        if not st["path"].exists():
            st["path"].write_text(st["text"])

    def compose(self) -> ComposeResult:
        st = self.st
        yield Header()
        with Vertical(id="form"):
            with Horizontal(classes="field"):
                yield Static("From", classes="label")
                yield FromField(id="from")
            for f in self.FIELDS:
                with Horizontal(classes="field"):
                    yield Static(f.title(), classes="label")
                    yield Input(st[f], id=f)
            yield OptionList(id="suggest")
            yield Static(id="attachments")
        with VerticalScroll(id="bodyscroll"):
            yield Static(id="body")
        yield Status()
        yield Footer()

    def on_mount(self):
        self.query_one("#from").can_focus = self.st["kind"] == "new"
        self.paint()
        if self.st["kind"] in ("reply", "all") and not self.st["text"]:
            self.call_after_refresh(self.action_body)  # recipients are known, so straight to writing (W9)
        else:
            self.query_one("#to").focus()

    # --- painting ------------------------------------------------------------------------------------

    def paint(self):
        st = self.st
        title = {"new": "new mail", "reply": "Reply", "all": "Reply all", "forward": "Forward"}[st["kind"]]
        if st["kind"] != "new":
            title += " · " + (st["subject"] or "")
        self.query_one(Header).set(f"homer · Compose · {title}", st.get("saved", ""))
        a = st["acct"]
        fixed = st["kind"] != "new"
        self.query_one("#from", FromField).update(
            Text(f"{a.label} · {a.email}") + Text("   (fixed)" if fixed else "   ← → to change", "dim"))
        att = Text("Attach   ", "dim")
        if st["attach"]:
            total = 0
            for i, p in enumerate(st["attach"], 1):
                size = Path(p).stat().st_size if Path(p).is_file() else 0
                total += size
                att.append(f"[{i}] {Path(p).name} · {human(size)}   ", "" if size else "red")
            att.append(f"total {human(total)}", "dim")
        else:
            att.append("(none)   a to add", "dim")
        self.query_one("#attachments", Static).update(att)
        body = Text()
        lines = st["text"].rstrip().splitlines()
        for ln in lines or [""]:
            body.append("┆ ", "dim")
            body.append(ln + "\n")
        if not lines:
            body.append("┆ ", "dim")
            body.append("(empty: press b, or Enter in Subject, to write in your editor)\n", "dim")
        body.append("┆\n┆ ", "dim")
        body.append("🔒 Sent with homer", "bold")
        body.append("   (always added, locked)\n", "dim")
        orig = st["orig"]
        if orig is not None:
            n = len(body_text(orig).splitlines())
            body.append("┆\n┆ ", "dim")
            if st["kind"] == "forward":
                k = len(attachments(orig))
                body.append(f"▸ Forwarded message from {orig['From']} ({n} lines"
                            + (f", {k} attachments)" if k else ")"), "dim")
            else:
                body.append(f"▸ On {orig['Date']}, {orig['From']} wrote: ({n} lines, quoted)", "dim")
        self.query_one("#body", Static).update(body)

    def cycle_from(self, d):
        if self.st["kind"] != "new":
            return
        accts = list(self.app.accounts.values())
        self.st["acct"] = accts[(accts.index(self.st["acct"]) + d) % len(accts)]
        self.paint()

    # --- modes and fields ------------------------------------------------------------------------------

    def check_action(self, action, parameters):
        if action in self.COMMANDS:
            return not isinstance(self.focused, (Input, FromField))
        return True

    def action_send_key(self):
        self.action_send()

    def on_descendant_focus(self):
        self.refresh_bindings()

    def on_descendant_blur(self):
        self.refresh_bindings()

    def action_commands(self):
        self.hide_suggest()
        self.set_focus(None)
        self.refresh_bindings()
        self.query_one(Status).say("s send  b edit body  a attach  X remove attachment  d save draft  q close  D discard"
                                   "  ·  Tab back to the fields")

    def action_field(self, d):
        sug = self.query_one("#suggest", OptionList)
        if d > 0 and sug.has_class("on") and sug.highlighted is not None:
            self.accept_suggestion()
            return
        self.hide_suggest()
        self.focus_next() if d > 0 else self.focus_previous()

    def action_arrow(self, d):
        sug = self.query_one("#suggest", OptionList)
        if sug.has_class("on"):
            sug.action_cursor_down() if d > 0 else sug.action_cursor_up()
        elif isinstance(self.focused, (Input, FromField)):
            self.focus_next() if d > 0 else self.focus_previous()

    @on(Input.Changed)
    def changed(self, event):
        f = event.input.id
        self.st[f] = event.value
        event.input.remove_class("bad")
        if f == "subject":
            self.no_subject_ok = False
        if f not in ("to", "cc", "bcc"):
            return
        token = event.value.rsplit(",", 1)[-1].strip().lower()
        sug = self.query_one("#suggest", OptionList)
        done = ADDR.match(getaddresses([token])[0][1]) if token else False  # a full address needs no help
        hits = [c for c in self.st["acct"].contacts() if token in c.lower()][:6] if token and not done else []
        if hits and not (len(hits) == 1 and hits[0].lower() == token):
            sug.set_options([Option(h) for h in hits])
            sug.highlighted = 0
            sug.add_class("on")
        else:
            self.hide_suggest()

    def hide_suggest(self):
        self.query_one("#suggest", OptionList).remove_class("on")

    def accept_suggestion(self):
        sug = self.query_one("#suggest", OptionList)
        field = self.focused
        if not isinstance(field, Input) or sug.highlighted is None:
            return
        pick = str(sug.get_option_at_index(sug.highlighted).prompt)
        head = field.value.rsplit(",", 1)[0] + ", " if "," in field.value else ""
        field.value = head + pick + ", "
        field.cursor_position = len(field.value)
        self.hide_suggest()

    @on(Input.Submitted)
    def submitted(self, event):
        if self.query_one("#suggest").has_class("on"):
            self.accept_suggestion()
        elif event.input.id == "subject":
            self.action_body()
        else:
            self.focus_next()

    # --- body and attachments ------------------------------------------------------------------------

    def action_body(self):
        path = self.st["path"]
        path.write_text(self.st["text"])
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        with self.app.suspend():
            subprocess.run([*shlex.split(editor), str(path)])
        self.st["text"] = path.read_text()
        self.st["saved"] = f"text kept {datetime.now():%H:%M}"
        self.paint()
        self.action_commands()

    def action_attach(self):
        def add(path):
            if path:
                self.st["attach"].append(path)
                self.paint()
        self.app.push_screen(PathModal(), add)

    def action_unattach(self):
        if not self.st["attach"]:
            return
        if len(self.st["attach"]) == 1:
            self.st["attach"].clear()
            self.paint()
            return

        def drop(name):
            if name:
                self.st["attach"] = [p for p in self.st["attach"] if Path(p).name != name]
                self.paint()
        self.app.push_screen(MovePicker("Remove attachment", [Path(p).name for p in self.st["attach"]]), drop)

    # --- leaving -------------------------------------------------------------------------------------

    def build(self):
        st = self.st
        orig = st["orig"]
        kind = st["kind"]
        return store.build_outgoing(st["acct"].acct, kind, orig, addresses(st["to"]), addresses(st["cc"]),
                                    addresses(st["bcc"]), st["subject"].strip(), st["text"], st["attach"],
                                    st["headers"])

    def problem(self):
        st = self.st
        for f in ("to", "cc", "bcc"):
            bad = bad_addresses(st[f])
            if bad:
                self.query_one(f"#{f}").add_class("bad")
                return f"#{f}", f"{', '.join(bad)} doesn't look like an address"
        if not (addresses(st["to"]) or addresses(st["cc"]) or addresses(st["bcc"])):
            return "#to", "Who is it to? Add at least one recipient"
        missing = [Path(p).name for p in st["attach"] if not Path(p).is_file()]
        if missing:
            return None, f"Attachment not found: {', '.join(missing)}"
        if not st["text"].strip() and st["kind"] != "forward":
            return None, "The body is empty. Press b to write it"
        return None, None

    def action_send(self):
        where, problem = self.problem()
        if not problem and not self.st["subject"].strip() and not self.no_subject_ok:
            self.no_subject_ok = True
            self.action_commands()
            self.query_one(Status).say("No subject. s send anyway   Tab back to the fields", True)
            return
        if problem:
            self.query_one(Status).say("! " + problem, True)
            if where:
                self.query_one(where).focus()
            return
        msg = self.build()
        if self.st["acct"].acct.get("provider") == "outlook" and len(msg.as_bytes()) * 4 / 3 > 4 * 1024 * 1024:
            self.query_one(Status).say("! Over Outlook's 4 MB limit, attachments included", True)
            return
        self.dismiss({"action": "send", "state": self.st, "msg": msg})

    def action_close(self, how):
        st = self.st
        empty = not (st["text"].strip() or st["to"].strip() or st["subject"].strip() or st["attach"])
        if how == "discard":
            self.dismiss({"action": "discard", "state": st})
        elif empty and not st["draft"]:
            self.dismiss({"action": "empty", "state": st})
        else:
            try:
                msg = self.build()
            except OSError as e:
                self.query_one(Status).say(f"! {err(e)}", True)
                return
            self.dismiss({"action": how, "state": st, "msg": msg})

    def action_help(self):
        self.app.push_screen(HelpScreen("Keys · compose", HELP["compose"]))


# --- small windows ------------------------------------------------------------------------------------

class Box(ModalScreen):
    """A box over the current window (W6, W14, W15, W17, W20)."""


class MovePicker(Box):
    """Pick from a list; typing filters it (W14). Also used to pick an attachment to remove."""
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel"), Binding("down", "move(1)", show=False),
                Binding("up", "move(-1)", show=False)]

    def __init__(self, title, choices):
        super().__init__()
        self.title_text, self.choices = title, choices

    def compose(self):
        with Vertical(classes="box"):
            yield Static(Text(self.title_text, "bold"))
            yield Input(placeholder="type to filter", id="filter")
            yield OptionList(*self.choices, id="choices")
            yield Static(Text("Enter choose  Esc cancel", "dim"))

    def on_mount(self):
        self.query_one("#choices", OptionList).highlighted = 0 if self.choices else None
        self.query_one("#filter").focus()

    @on(Input.Changed)
    def filter(self, event):
        lst = self.query_one("#choices", OptionList)
        lst.set_options([c for c in self.choices if event.value.lower() in c.lower()])
        lst.highlighted = 0 if lst.option_count else None

    def action_move(self, d):
        lst = self.query_one("#choices", OptionList)
        lst.action_cursor_down() if d > 0 else lst.action_cursor_up()

    @on(Input.Submitted)
    def pick(self):
        lst = self.query_one("#choices", OptionList)
        if lst.highlighted is not None:
            self.dismiss(str(lst.get_option_at_index(lst.highlighted).prompt))

    @on(OptionList.OptionSelected)
    def chosen(self, event):
        self.dismiss(str(event.option.prompt))


class AttachPicker(Box):
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel"), Binding("a", "all", "all"),
                Binding("enter", "save", "save", priority=True), Binding("O", "open", "open")]

    def __init__(self, parts):
        super().__init__()
        self.parts = parts

    def compose(self):
        with Vertical(classes="box"):
            yield Static(Text("Save attachments", "bold"))
            yield SelectionList(*[Selection(f"{p.get_filename()}  ·  {human(len(p.get_payload(decode=True) or b''))}", i)
                                  for i, p in enumerate(self.parts)], id="parts")
            yield Static(Text(f"to {DOWNLOADS}\nspace pick  a all  Enter save  O open  Esc cancel", "dim"))

    def action_all(self):
        self.query_one(SelectionList).select_all()

    def action_save(self):
        lst = self.query_one(SelectionList)
        chosen = lst.selected or ([lst.highlighted] if lst.highlighted is not None else [])
        self.dismiss(save_attachments([self.parts[i] for i in chosen], DOWNLOADS))

    def action_open(self):
        lst = self.query_one(SelectionList)
        if lst.highlighted is not None:
            VIEW.mkdir(parents=True, exist_ok=True)
            open_external(save_attachments([self.parts[lst.highlighted]], VIEW)[0])


class LinkPicker(Box):
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel"), Binding("y", "copy", "copy")] + \
               [Binding(str(i), f"number({i})", show=False) for i in range(1, 10)]

    def __init__(self, links):
        super().__init__()
        self.links = links

    def compose(self):
        with Vertical(classes="box"):
            yield Static(Text("Open link", "bold"))
            yield OptionList(*[f"{i}  {u}" for i, u in enumerate(self.links, 1)], id="links")
            yield Static(Text("Enter or number: open in browser  y copy  Esc cancel", "dim"))

    def open_link(self, i):
        open_external(self.links[i])
        self.dismiss(f"Opened {self.links[i]}")

    @on(OptionList.OptionSelected)
    def chosen(self, event):
        self.open_link(event.option_index)

    def action_number(self, i):
        if i <= len(self.links):
            self.open_link(i - 1)

    def action_copy(self):
        lst = self.query_one(OptionList)
        if lst.highlighted is not None:
            self.app.copy_to_clipboard(self.links[lst.highlighted])
            self.dismiss(f"Copied {self.links[lst.highlighted]}")


class NameModal(Box):
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, title, hint, check, value=""):
        super().__init__()
        self.title_text, self.hint, self.check, self.value = title, hint, check, value

    def compose(self):
        with Vertical(classes="box"):
            yield Static(Text(self.title_text, "bold"))
            yield Input(self.value, placeholder="Name")
            yield Static(Text(self.hint + "  Esc cancel", "dim"), id="hint")

    @on(Input.Submitted)
    def done(self, event):
        name = event.value.strip()
        problem = self.check(name)
        if problem:
            self.query_one("#hint", Static).update(Text(problem, "red"))
        else:
            self.dismiss(name)


class PathModal(Box):
    """Attach a file; Tab completes the path."""
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel"), Binding("tab", "complete", "complete", priority=True)]

    def compose(self):
        with Vertical(classes="box"):
            yield Static(Text("Attach a file", "bold"))
            yield Input(placeholder="~/Downloads/notes.pdf")
            yield Static(Text("Tab complete  Enter attach  Esc cancel", "dim"), id="hint")

    def action_complete(self):
        box = self.query_one(Input)
        typed = box.value + "/" if box.value == "~" else box.value
        folder, stem = os.path.split(typed)  # typed ends in stem, so completing is just appending
        where = os.path.expanduser(folder) or "."
        try:
            hits = sorted(n for n in os.listdir(where) if n.startswith(stem) and (stem[:1] == "." or n[:1] != "."))
        except OSError:
            hits = []
        if not hits:
            return
        done = typed[:len(typed) - len(stem)] + os.path.commonprefix(hits)
        if len(hits) == 1 and os.path.isdir(os.path.join(where, hits[0])):
            done += "/"
        box.value = done
        box.cursor_position = len(done)
        if len(hits) > 1:
            self.query_one("#hint", Static).update(Text("  ".join(hits[:8]), "dim"))

    @on(Input.Submitted)
    def done(self, event):
        p = Path(event.value.strip()).expanduser()
        if p.is_file():
            self.dismiss(str(p))
        else:
            self.query_one("#hint", Static).update(Text(f"No file at {p}", "red"))


class ConfirmModal(Box):
    BINDINGS = [Binding("y", "dismiss(True)", "yes"), Binding("escape", "dismiss(False)", "cancel")]

    def __init__(self, title, text):
        super().__init__()
        self.title_text, self.text = title, text

    def compose(self):
        with Vertical(classes="box"):
            yield Static(Text(self.title_text, "bold"))
            yield Static(self.text)
            yield Static(Text("y delete  Esc cancel", "dim"))


class HelpScreen(Box):
    BINDINGS = [Binding("escape,question_mark,q", "dismiss(None)", "close")]

    def __init__(self, title, text):
        super().__init__()
        self.title_text, self.text = title, text

    def compose(self):
        with Vertical(classes="box wide"):
            yield Static(Text(self.title_text, "bold"))
            yield Static(self.text)
            yield Static(Text("Esc or ? close", "dim"))


class QuitScreen(Box):
    """Mail still counting down is sent before homer exits (W10)."""

    def __init__(self, outs):
        super().__init__()
        self.outs = outs

    def compose(self):
        n = len(self.outs)
        with Vertical(classes="box"):
            yield Static(Text(f"Sending {n} mail{'s' if n != 1 else ''} before quitting…", "bold"))

    def on_mount(self):
        self.flush()

    @work(thread=True)
    def flush(self):
        notes = []
        for o in self.outs:
            while o["state"] == "sending":  # already on its way in another worker
                time.sleep(0.2)
            try:
                if o["state"] in ("quitting", "failed"):
                    send_now(o)
                    o["state"] = "sent"
            except CAUGHT as e:
                try:
                    o["acct"].call("save_draft", o["msg"])
                    notes.append(f'homer: not sent: "{o["subject"]}" ({err(e)}); it is in Drafts')
                except CAUGHT:
                    notes.append(f'homer: not sent: "{o["subject"]}" ({err(e)}); your text is in {o["st"]["path"]}')
            if o["state"] == "sent":
                o["st"]["path"].unlink(missing_ok=True)
        self.app.call_from_thread(self.app.exit, None, 0, "\n".join(notes) or None)


# --- theme and colours (ctrl+t) ------------------------------------------------------------------------

PALETTE = ("primary", "secondary", "accent", "success", "warning", "error", "foreground")  # every theme has these
MODS = ("bold", "dim", "italic", "underline")
ROLE_SAMPLE = {"unread": "● Kavitha Das", "sent": "→ To Amit", "thread": "(3)", "me": "▌ me → Kavitha",
               "quote": "> quoted text", "selected": "■"}
SAMPLE_ME = "arjun@example.com"


def split_style(style):
    words = style.split()
    return [w for w in words if w in MODS], next((w for w in words if w not in MODS), "")


def sample_mail():
    """A made-up mailbox for the theme screen, drawn by the same code as the real list and preview."""
    now = datetime.now(timezone.utc)

    def mail(i, addr, name, thread, mins, subject, body, unread=False, to=("Kavitha Das", "kavitha@example.com")):
        m = EmailMessage()
        m.set_content(body)
        return {"id": str(i), "folder": "INBOX", "unread": unread, "name": name, "addr": addr, "to": [list(to)],
                "when": (now - timedelta(minutes=mins)).isoformat(), "subject": subject, "msgid": f"<{i}@s>",
                "thread": thread, "attach": i == 1, "labels": []}, m

    rows = [mail(1, "kavitha@example.com", "Kavitha Das", "t1", 900, "Trip schedule",
                 "Hi Arjun,\n\nThe schedule is out. We leave on the 19th.", to=("", SAMPLE_ME)),
            mail(2, SAMPLE_ME, "", "t1", 600, "Re: Trip schedule",
                 "Sounds good, see you then.\n\n> The schedule is out. We leave on the 19th."),
            mail(3, "kavitha@example.com", "Kavitha Das", "t1", 30, "Re: Trip schedule",
                 "Great, booking the hotel now.", unread=True, to=("", SAMPLE_ME)),
            mail(4, SAMPLE_ME, "", "t2", 3000, "Re: Rent", "Paid, thanks!", to=("Amit", "amit@example.com")),
            mail(5, "rahul@example.com", "Rahul", "t3", 6000, "Books due Friday", "Two books are due.",
                 to=("", SAMPLE_ME))]
    acct = type("Sample", (), {"name": "sample", "me": {SAMPLE_ME}})
    return store.conversations([m for m, _ in rows], acct.me), {loc(acct, m): raw for m, raw in rows}, acct


class RoleList(NoWrap):
    BINDINGS = [Binding("left", "screen.shade(-1)", "colour", key_display="← →"),
                Binding("right", "screen.shade(1)", show=False),
                Binding("b", "screen.mod('bold')", "bold"), Binding("i", "screen.mod('italic')", "italic"),
                Binding("d", "screen.mod('dim')", "dim"), Binding("x", "screen.reset", "default"),
                Binding("j", "cursor_down", show=False), Binding("k", "cursor_up", show=False)]


class LookScreen(Screen):
    """Themes and colour roles beside a sample mailbox painted in them. Roles take the theme's own colours
    ($accent …), so they follow every theme. Enter keeps it all, Esc puts back what you had."""
    BINDINGS = [Binding("tab", "switch", "themes / colours", priority=True),
                Binding("enter", "save", "save", priority=True), Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, theme, colors):
        super().__init__()
        self.pick, self.draft = theme, dict(colors)
        self.convs, self.raws, self.acct = sample_mail()

    def compose(self):
        yield Header(id="lookhead")
        with Horizontal(id="look"):
            with Vertical(id="lookleft"):
                yield Static(Text("Theme", "bold"), classes="looktitle")
                yield MailList(*[Option(self.swatch(n), id=n) for n in sorted(self.app.available_themes)],
                               id="themes")
                yield Static(Text("Colours", "bold"), classes="looktitle")
                yield RoleList(id="roles")
            with Vertical(id="sample"):
                yield Header(id="samplehead")
                with Horizontal(id="samplebody"):
                    yield OptionList(id="samplelist")
                    yield ConvView(id="sampleconv")
        yield Status()
        yield Footer()

    def swatch(self, name):
        """A theme's name with its colours on its own background, readable before you move onto it."""
        v = self.app.available_themes[name].to_color_system().generate()
        t = Text(f"{name:<20}")
        for c in ("primary", "accent", "success", "warning", "error"):
            t.append(" ●", f"{v[c]} on {v['background']}".replace("ansi_", ""))
        return t.append(" ", f"on {v['background']}".replace("ansi_", ""))

    def on_mount(self):
        self.query_one("#lookhead", Header).set("homer · theme and colours")
        self.query_one("#samplehead", Header).set("homer · gmail · Mailbox", "1 unread · synced just now")
        for w in (self.query_one("#samplelist"), self.query_one("#sampleconv")):
            w.can_focus = False  # a picture of homer, not a second homer
        themes = self.query_one("#themes", MailList)
        themes.highlighted = themes.get_option_index(self.pick)
        themes.focus()
        self.call_after_refresh(self.repaint)

    @on(OptionList.OptionHighlighted, "#themes")
    def try_theme(self, event):
        self.pick = event.option.id
        self.repaint()

    def repaint(self):
        self.app.paint_roles(self.pick, self.draft)
        roles = self.query_one("#roles", RoleList)
        at = roles.highlighted or 0
        lines = []
        for role in ROLES:
            mods, colour = split_style(self.draft[role])
            t = Text(f" {role:<9}")
            t.append(ROLE_SAMPLE[role], STYLE[role])
            t.append("   " + (" ".join(mods + ([colour.lstrip("$")] if colour else [])) or "plain"), "dim")
            lines.append(Option(t, id=role))
        roles.set_options(lines)
        roles.highlighted = at
        lst = self.query_one("#samplelist", OptionList)
        width = lst.scrollable_content_region.width or 50
        lst.set_options([Option(list_row(c, width, self.acct.me, selected=c["key"] == "t3")) for c in self.convs])
        lst.highlighted = 0
        view = self.query_one("#sampleconv", ConvView)
        conv = self.convs[0]
        view.render_blocks(conv_blocks(conv, self.acct, self.raws, set(range(len(conv["msgs"]))),
                                       view.scrollable_content_region.width or 40)[0])
        self.query_one(Status).say(f"{self.pick} · Tab moves between themes and colours; colours come from "
                                   "the theme, so they change with it")

    def role(self):
        roles = self.query_one("#roles", RoleList)
        return list(ROLES)[roles.highlighted or 0]

    def action_switch(self):
        themes, roles = self.query_one("#themes"), self.query_one("#roles")
        (roles if themes.has_focus else themes).focus()

    def action_shade(self, d):
        """Step through the theme's colours (and "no colour") for the role under the cursor."""
        role = self.role()
        mods, colour = split_style(self.draft[role])
        names = list(PALETTE) + [""]
        i = names.index(colour[1:]) if colour[1:] in PALETTE else (len(names) - 1 if not colour else -1)
        new = names[(i + d) % len(names)]
        self.draft[role] = " ".join(mods + (["$" + new] if new else []))
        self.repaint()

    def action_mod(self, mod):
        role = self.role()
        mods, colour = split_style(self.draft[role])
        mods = [m for m in mods if m != mod] if mod in mods else mods + [mod]
        self.draft[role] = " ".join(mods + ([colour] if colour else []))
        self.repaint()

    def action_reset(self):
        self.draft[self.role()] = ROLES[self.role()]
        self.repaint()

    def action_save(self):
        self.dismiss((self.pick, self.draft))


# --- homer folder mapping (W16) ----------------------------------------------------------------------

class Ticks(SelectionList):
    BINDINGS = [Binding("space,enter", "select", "tick"), Binding("j", "cursor_down", show=False),
                Binding("k", "cursor_up", show=False)]


class MapScreen(Screen):
    BINDINGS = [Binding("t", "target", "make it where moves go"), Binding("s,ctrl+s", "save", "save"),
                Binding("escape", "dismiss(False)", "cancel")]

    def __init__(self, acct, folder, new=False):
        super().__init__()
        self.acct, self.folder, self.new = acct, folder, new
        self.server, self.chosen = [], list(acct.folders().get(folder, []))
        self.target = self.chosen[0] if self.chosen else None

    def compose(self):
        yield Header()
        yield Static(id="explain")
        yield Ticks(id="srcs")
        yield Static(Text(f"To create a new {self.acct.label} folder or label, use the CLI: "
                          f"homer mkdir NAME -a {self.acct.name}", "dim"), id="mkdir")
        yield Status()
        yield Footer()

    def on_mount(self):
        self.query_one(Header).set(f"homer · {self.acct.label} · folders › {self.folder}")
        self.query_one("#explain", Static).update(
            f"Mail in these {self.acct.label} folders and labels shows in {self.folder}. "
            f"Moves to {self.folder} go to the one marked ◆. Space or Enter ticks a folder, s saves.")
        self.query_one(Status).say("Loading the server's folders…")
        self.load()
        self.query_one("#srcs").focus()

    @work(thread=True)
    def load(self):
        try:
            names = self.acct.call("folders")
        except CAUGHT as e:
            self.app.call_from_thread(self.query_one(Status).say, f"! {err(e)}", True)
            return
        self.app.call_from_thread(self.fill, [n for n in names if n != "[Gmail]"])

    def fill(self, names=None):
        if names is not None:
            self.server = names
            self.query_one(Status).say("")
        lst = self.query_one("#srcs", SelectionList)
        keep = lst.highlighted
        chosen = set(lst.selected) if lst.option_count else set(self.chosen)
        owner = {s: k for k, v in self.acct.folders().items() if k != self.folder for s in v}
        free = [n for n in self.server if n not in owner]
        taken = [n for n in self.server if n in owner]
        items = []
        for n in free + taken:
            label = Text(("◆ " if n == self.target else "  ") + n)
            if n in owner:
                label.append(f"   in {owner[n]}", "dim")
            items.append(Selection(label, n, n in chosen, disabled=n in owner))
        lst.clear_options()
        lst.add_options(items)
        if keep is not None and keep < lst.option_count:
            lst.highlighted = keep

    def action_target(self):
        lst = self.query_one("#srcs", SelectionList)
        if lst.highlighted is None:
            return
        value = lst.get_option_at_index(lst.highlighted).value
        if value not in lst.selected:
            lst.select(value)
        self.target = value
        self.fill()

    def action_save(self):
        chosen = list(self.query_one("#srcs", SelectionList).selected)
        target = self.target if self.target in chosen else chosen[0] if chosen else None
        self.acct.set_sources(self.folder, [target] + [c for c in chosen if c != target] if chosen else [])
        self.dismiss(True)


class NoAccounts(Screen):
    """W4: account setup stays in the CLI."""
    BINDINGS = [Binding("q", "app.quit", "quit")]

    def compose(self):
        yield Header()
        yield Static("\n   No accounts set up yet.\n\n   Add one in your config file, then link it from the command "
                     "line:\n\n     homer login -a gmail\n\n   See README.md, \"Setup\".")
        yield Footer()


class Homer(App):
    TITLE = "homer"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("ctrl+q", "safe_quit", show=False, priority=True),
                Binding("ctrl+t", "pick_theme", show=False)]
    CSS = """
    Screen { background: $background; }
    Header { height: 1; background: $panel; }
    #hleft { width: 1fr; padding: 0 1; text-style: bold; }
    #hright { width: auto; padding: 0 1; color: $text-muted; }
    Status { height: 1; padding: 0 1; }
    Status.error { color: $error; }
    OptionList { border: none; padding: 0; background: $background; }
    #list, #folders, ConvView { scrollbar-gutter: stable; scrollbar-size-vertical: 1; }
    OptionList:focus { border: none; }
    #body { height: 1fr; }
    #folders { width: 26; display: none; border-right: solid $panel-lighten-2; }
    MainScreen.panes #folders { display: block; }
    #listcol { width: 1fr; }
    #list { height: 1fr; }
    #searchrow { height: 1; display: none; }
    #searchrow.on { display: block; }
    #search { width: 1fr; height: 1; border: none; padding: 0 1; }
    #scope { width: auto; }
    #preview { width: 1fr; border-left: solid $panel-lighten-2; padding: 0 1; }
    MainScreen.narrow #preview { display: none; }
    MainScreen.narrow.panes #listcol { display: none; }
    MainScreen.narrow.panes #folders { width: 1fr; }
    ConvView .mail { margin-bottom: 1; }
    #reader { padding: 0 1; }
    #form { height: auto; border-bottom: solid $panel-lighten-2; }
    .field { height: 1; }
    .label { width: 10; padding: 0 1; color: $text-muted; }
    .field Input { width: 1fr; height: 1; border: none; padding: 0; }
    .field Input.bad { color: $error; text-style: underline; }
    FromField { width: 1fr; }
    FromField:focus { background: $boost; }
    #suggest { display: none; height: auto; max-height: 8; width: 60; margin-left: 10; border: round $panel-lighten-2; }
    #suggest.on { display: block; }
    #attachments { height: auto; padding: 0 1; border-top: solid $panel-lighten-2; }
    #bodyscroll { height: 1fr; padding: 0 1; }
    Box { align: center middle; background: $background 60%; }
    .box { width: 64; height: auto; max-height: 80%; padding: 1 2; border: round $accent; background: $surface; }
    .box.wide { width: 84; }
    .box Input { border: tall $panel-lighten-2; }
    .box OptionList, .box SelectionList { height: auto; max-height: 14; }
    #srcs { height: 1fr; }
    #explain, #mkdir { padding: 1 1 0 1; height: auto; }
    /* a tinted cursor, not a solid $primary bar, so the colour roles stay readable on the row it's on */
    #list > .option-list--option-highlighted, #roles > .option-list--option-highlighted {
        color: $foreground; background: $primary 15%; }
    #list:focus > .option-list--option-highlighted, #roles:focus > .option-list--option-highlighted,
    #samplelist > .option-list--option-highlighted { color: $foreground; background: $primary 30%; }
    #look { height: 1fr; }
    #lookleft { width: 44; border-right: solid $panel-lighten-2; }
    .looktitle { padding: 1 1 0 1; color: $text-muted; }
    #themes { height: 1fr; }
    #roles { height: auto; }
    #sample { width: 1fr; }
    #samplebody { height: 1fr; }
    #samplelist { width: 3fr; }
    #sampleconv { width: 2fr; border-left: solid $panel-lighten-2; padding: 0 1; }
    """

    def __init__(self, cfg, name):
        super().__init__()
        maps, self.state = store.load_maps(), store.load_state()
        names = self.state.get("names", {})
        self.accounts = {n: store.Account(n, a, BACKENDS[a.get("provider", "gmail")], maps, names.get(n))
                         for n, a in cfg.get("accounts", {}).items() if a.get("provider", "gmail") in BACKENDS}
        self.first, self.outbox, self.look, self.config_note = name, [], cfg.get("tui", {}), ""

    def colors(self):
        """Role styles as written: what you set with ctrl+t, else [tui.colors] in the config, else the default."""
        return {**ROLES, **self.look.get("colors", {}), **self.state.get("colors", {})}

    def paint_roles(self, theme=None, colors=None):
        """Apply a theme and resolve the role styles against it, so $accent is that theme's accent. Bad
        entries keep the default and are reported."""
        bad = []
        theme = theme or self.state.get("theme") or self.look.get("theme")
        if theme in self.available_themes:
            self.theme = theme
        elif theme:
            bad.append(f"theme {theme}")
        css = self.get_css_variables()
        colors = colors or self.colors()
        for role, default in ROLES.items():
            for style in (colors.get(role), default):
                if not isinstance(style, str):
                    continue
                style = re.sub(r"\$([\w-]+)", lambda m: css.get(m[1], m[0]).removeprefix("ansi_"), style)
                try:
                    Style.parse(style)
                except StyleSyntaxError:
                    bad.append(f"colors.{role}")
                    continue
                STYLE[role] = style
                break
        unknown = set(self.look.get("colors", {})) - set(ROLES)
        bad += [f"colors.{r} (roles: {', '.join(ROLES)})" for r in sorted(unknown)]
        self.config_note = "! [tui] in your config: ignored " + ", ".join(bad) if bad else ""

    def on_mount(self):
        self.paint_roles()
        if self.accounts:
            self.push_screen(MainScreen(self.accounts.get(self.first) or next(iter(self.accounts.values()))))
        else:
            self.push_screen(NoAccounts())

    def repaint_all(self):
        """Redraw the rows and mails, whose colours were resolved against the old theme."""
        for s in self.screen_stack:
            if isinstance(s, MainScreen):
                s.rebuild()
                s.preview()
                if s.has_class("panes"):
                    s.fill_pane()
            elif isinstance(s, ReaderScreen):
                s.paint()

    def action_pick_theme(self):
        if isinstance(self.screen, LookScreen):
            return

        def done(result):
            if result:
                theme, colors = result
                base = {**ROLES, **self.look.get("colors", {})}
                self.state["theme"] = theme
                self.state["colors"] = {r: v for r, v in colors.items() if v != base.get(r)}
                store.save_state(self.state)
            self.paint_roles()
            self.repaint_all()

        self.push_screen(LookScreen(self.theme, self.colors()), done)

    def action_safe_quit(self):
        main = next((s for s in self.screen_stack if isinstance(s, MainScreen)), None)
        main.action_quit() if main else self.exit()


def main(cfg, name):
    Homer(cfg, name).run()
