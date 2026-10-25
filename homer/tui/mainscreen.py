"""Main window (W1, W2, W3, W10, W12, W13, W18, W19, W21)."""

import time
import uuid
from datetime import datetime, timezone

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from .. import store
from ..common import body_text, parse_query
from . import base
from .base import (
    CAUGHT, HELP, PAGE, READ_AFTER, STYLE, SYNC_EVERY, WIDE, conv_blocks, err, list_row, loc, open_in_browser,
    parse, send_now,
)
from .widgets import ConvView, FolderPane, Header, MailList, SearchBar, Status
from .reader import ReaderScreen
from .compose import ComposeScreen
from .modals import ConfirmModal, HelpScreen, MovePicker, NameModal, QuitScreen
from .folders import MapScreen


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
        Binding("J", "scroll_line(1)", show=False),
        Binding("K", "scroll_line(-1)", show=False),
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
        width = view.text_width(50)
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
            self.say(f"Already in {dest}" + (". Homer doesn't delete mail for good" if dest == "Trash" else ""))
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
        """Half a screen at a time and no animation, so the line you were reading stays in view."""
        view = self.query_one("#preview", ConvView)
        view.scroll_relative(y=direction * max(1, view.size.height // 2), animate=False)

    def action_scroll_line(self, direction):
        self.query_one("#preview", ConvView).scroll_relative(y=direction, animate=False)

    def action_browser(self):
        c = self.current()
        msg = self.raws.get(loc(self.acct, c["msgs"][-1])) if c and c["msgs"] else None
        if msg is None:
            self.say("Nothing to open yet")
            return
        open_in_browser(msg)
        self.say("Opened in the browser")

    LIST_ONLY = {"compose", "reply", "move", "toggle_read", "pick_move", "select", "undo", "scroll", "scroll_line", "search",
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
              "text": "", "conv": None, "draft": None, "headers": {}, "path": base.DRAFTS / f"{uuid.uuid4().hex}.txt",
              "fresh": True}
        name = acct.acct.get("template" if kind == "new" else "reply_template")
        tpl = store.template(name) if name else ""
        if tpl and "text" not in kw:  # the account's default template; a reopened draft keeps its own text
            st["text"], st["cursor"] = store.apply_template(tpl, "")
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
                 "left": base.SEND_DELAY, "to": (to.display_name or to.addr_spec) if to else "", "subject": subject}
            self.outbox.append(o)
            self.undo = ("send", o)
            if self.folder != "Mailbox" or st["acct"] is not self.acct:
                self.switch(st["acct"], "Mailbox")
            self.rebuild()
            self.query_one("#list", MailList).highlighted = 0
            self.say(f'Sending "{subject}" to {o["to"]} in {base.SEND_DELAY}s · u undo')
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

