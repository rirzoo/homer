"""Compose (W7, W8, W9)."""

import os
import shlex
import subprocess
from datetime import datetime
from email.utils import getaddresses
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from .. import store
from ..common import attachments, body_text
from . import base
from .base import ADDR, HELP, addresses, bad_addresses, err, human, open_external
from .widgets import Header, Status
from .modals import HelpScreen, MovePicker, NameModal, PathModal, TemplatePicker


STARTER = "Dear {first},\n\n{body}\n\nBest regards,\n"  # a new template, before you make it yours


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
        Binding("s", "send_key", "send"), Binding("b", "body", "edit body"), Binding("t", "template", "template"),
        Binding("a", "attach", "attach"),
        Binding("X", "unattach", "remove attachment"), Binding("o", "open", "open attachment"),
        Binding("d", "close('draft')", "save draft"),
        Binding("q", "close('close')", "close"), Binding("D", "close('discard')", "discard"),
        Binding("question_mark", "help", show=False),
    ]
    FIELDS = ("to", "cc", "bcc", "subject")
    # single keys: only outside the fields
    COMMANDS = {"send_key", "body", "template", "attach", "unattach", "open", "close"}
    AUTO_FOCUS = ""  # on_mount picks the field; after a popup, stay in command mode instead of jumping to From

    def __init__(self, st):
        super().__init__()
        self.st, self.no_subject_ok = st, False
        base.DRAFTS.mkdir(parents=True, exist_ok=True)
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
        if self.st["kind"] in ("reply", "all") and self.st.pop("fresh", False):
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
        self.query_one(Status).say("s send  b edit body  t template  a attach  o open attachment  X remove one  "
                                   "d save draft  q close  D discard  ·  Tab back to the fields")

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

    def edit(self, path, line=None):
        """Your editor on a file, at a line when the editor takes +N."""
        editor = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi")
        if line and Path(editor[0]).name in ("vi", "vim", "nvim", "nano", "emacs", "micro"):
            editor.append(f"+{line}")
        with self.app.suspend():
            subprocess.run([*editor, str(path)])

    def action_body(self):
        path = self.st["path"]
        path.write_text(self.st["text"])
        self.edit(path, self.st.pop("cursor", None))  # at a template's {body}, if one was just put in
        self.st["text"] = path.read_text()
        self.st["saved"] = f"text kept {datetime.now():%H:%M}"
        self.paint()
        self.action_commands()

    def action_template(self):
        """Wrap what you've written in a template from ~/.config/homer/templates and open the editor; or write a
        new template, or change one, in the editor."""
        def picked(pick):
            if pick == TemplatePicker.NEW:
                self.app.push_screen(NameModal("New template", "Saved in " + str(store.TEMPLATES), taken), start)
            elif isinstance(pick, tuple):
                self.edit(store.TEMPLATES / f"{pick[1]}.txt")
                self.action_template()  # back to the list, to use it or pick another
            elif pick:
                use(pick)

        def taken(name):
            if not name or "/" in name or name.startswith("."):
                return "A name like formal or short"
            return f"{name} exists; ctrl+e in the list edits it" if name in store.templates() else None

        def start(name):
            if name:
                path = store.TEMPLATES / f"{name}.txt"
                store.TEMPLATES.mkdir(parents=True, exist_ok=True)
                path.write_text(STARTER)
                self.edit(path, STARTER.count("\n", 0, STARTER.index("{body}")) + 1)
                use(name)

        def use(name):
            self.st["text"], self.st["cursor"] = store.apply_template(store.template(name), self.st["text"])
            self.paint()
            self.action_body()
        self.app.push_screen(TemplatePicker(store.templates()), picked)

    def action_attach(self):
        def add(paths):
            if paths:
                self.st["attach"] += paths
                where = Path(paths[-1]).parent  # the next attach starts there, written with ~ like you'd type it
                rel = where.relative_to(Path.home()) if where.is_relative_to(Path.home()) else None
                self.app.attach_dir = "~/" + "".join(f"{p}/" for p in rel.parts) if rel else f"{where}/".replace("//", "/")
                self.paint()
        self.app.push_screen(PathModal(self.app.attach_dir), add)

    def action_open(self):
        """Open an attachment in its app, to check it's the right file."""
        if len(self.st["attach"]) == 1:
            return open_external(self.st["attach"][0])
        names = {Path(p).name: p for p in self.st["attach"]}
        if names:
            self.app.push_screen(MovePicker("Open attachment", list(names)), lambda n: n and open_external(names[n]))

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
                                    addresses(st["bcc"]), st["subject"].strip(),
                                    st["text"].replace("{first}", store.first_name(st["to"])), st["attach"],
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

