"""Small windows."""

import fnmatch
import os
import time
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, SelectionList, Static
from textual.widgets.selection_list import Selection

from ..common import save_attachments
from .base import CAUGHT, DOWNLOADS, VIEW, err, human, open_external, send_now


class Box(ModalScreen):
    """A box over the current window (W6, W14, W15, W17, W20, W23)."""


class MovePicker(Box):
    """Pick from a list; typing filters it (W14). Also used to pick an attachment to remove."""
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel"), Binding("down", "move(1)", show=False),
                Binding("up", "move(-1)", show=False)]

    def __init__(self, title, choices, hint="Enter choose  Esc cancel"):
        super().__init__()
        self.title_text, self.choices, self.hint = title, choices, hint

    def compose(self):
        with Vertical(classes="box"):
            yield Static(Text(self.title_text, "bold"))
            yield Input(placeholder="type to filter", id="filter")
            yield OptionList(*self.choices, id="choices")
            yield Static(Text(self.hint, "dim"))

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


class TemplatePicker(MovePicker):
    """Pick a template to use; the last row starts a new one and ctrl+e opens the highlighted one to edit (typing
    filters, so plain letters can't be keys here). Dismisses with the name, NEW, or ("edit", name)."""
    NEW = "+ new template"
    BINDINGS = [Binding("ctrl+e", "edit", "edit", priority=True)]  # over the filter's end-of-line

    def __init__(self, names):
        super().__init__("Template", [*names, self.NEW], "Enter use  ctrl+e edit  Esc cancel")

    def action_edit(self):
        lst = self.query_one("#choices", OptionList)
        name = str(lst.get_option_at_index(lst.highlighted).prompt) if lst.highlighted is not None else self.NEW
        if name != self.NEW:
            self.dismiss(("edit", name))


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


def path_matches(entries, stem):
    """The (name, is_dir) entries matching stem, ignoring case: a glob when it has * ? or [, otherwise every
    name containing it, the ones starting with it first. Dotfiles only when stem starts with a dot."""
    s = stem.lower()
    pool = [e for e in entries if stem.startswith(".") or not e[0].startswith(".")]
    if any(c in s for c in "*?["):
        return [e for e in pool if fnmatch.fnmatchcase(e[0].lower(), s)]
    return ([e for e in pool if e[0].lower().startswith(s)]
            + [e for e in pool if s in e[0].lower() and not e[0].lower().startswith(s)])


class PathModal(Box):
    """Attach files (W23): the folder in the path is listed under it and filtered as you type."""
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel"), Binding("tab", "fill", "complete", priority=True),
                Binding("ctrl+space", "mark", "mark", priority=True),
                Binding("down", "move(1)", show=False), Binding("up", "move(-1)", show=False)]
    LIMIT = 50  # rows listed; the hint counts the rest
    KEYS = "tab complete  ↑↓ pick  ctrl+space mark  enter attach  esc cancel"

    def __init__(self, start="~/"):
        super().__init__()
        self.start, self.marked, self.dirs, self.shown, self.listed = start, [], {}, [], None  # dirs: read once

    def compose(self):
        with Vertical(classes="box wide"):
            yield Static(Text("Attach files", "bold"))
            yield Input(self.start, id="path")
            yield OptionList(id="choices")
            yield Static(id="hint")

    def on_mount(self):
        box = self.query_one(Input)
        box.focus()
        box.cursor_position = len(box.value)
        self.relist()

    def split(self):
        """What's typed -> (its folder part as typed, that folder on disk, the name being typed)."""
        typed = self.query_one(Input).value
        typed += "/" if typed == "~" else ""
        stem = os.path.basename(typed)
        return typed[:len(typed) - len(stem)], os.path.expanduser(os.path.dirname(typed)) or ".", stem

    def entries(self, where):
        if where not in self.dirs:
            with os.scandir(where) as it:
                self.dirs[where] = sorted(((e.name, e.is_dir()) for e in it), key=lambda e: e[0].lower())
        return self.dirs[where]

    @on(Input.Changed)
    def relist(self):
        shown_as, where, stem = self.split()
        lst, hint = self.query_one("#choices", OptionList), self.query_one("#hint", Static)
        try:
            hits = path_matches(self.entries(where), stem)
        except OSError:
            hits = []
            hint.update(Text(f"Can't read {shown_as or '.'}", "red"))
        else:
            more = f"  +{len(hits) - self.LIMIT} more" if len(hits) > self.LIMIT else ""
            marked = f"  {len(self.marked)} marked" if self.marked else ""
            hint.update(Text(self.KEYS + more + marked, "dim") if hits
                        else Text(f'No match for "{stem}" in {shown_as or "."}', "red"))
        self.shown = [(os.path.join(where, n), d) for n, d in hits[:self.LIMIT]]
        self.listed = self.query_one(Input).value
        lst.set_options([self.row(p, d) for p, d in self.shown])
        lst.highlighted = 0 if self.shown else None

    def row(self, path, is_dir):
        name = os.path.basename(path) + ("/" if is_dir else "")
        t = Text("✓ " if path in self.marked else "  ", "bold")
        t.append(name if len(name) <= 60 else name[:59] + "…")
        if not is_dir:
            try:
                t.append(" " * max(1, 62 - len(t)) + human(os.stat(path).st_size).rjust(9), "dim")
            except OSError:
                pass
        return t

    def picked(self):
        if self.query_one(Input).value != self.listed:  # keys typed faster than Input.Changed arrives
            self.relist()
        at = self.query_one("#choices", OptionList).highlighted
        return self.shown[at] if at is not None else None

    def action_move(self, d):
        lst = self.query_one("#choices", OptionList)
        lst.action_cursor_down() if d > 0 else lst.action_cursor_up()

    def action_fill(self):
        """The highlighted entry into the path; a folder then lists its own contents."""
        hit = self.picked()
        if hit:
            box = self.query_one(Input)
            box.value = self.split()[0] + os.path.basename(hit[0]) + ("/" if hit[1] else "")
            box.cursor_position = len(box.value)

    def action_mark(self):
        hit, lst = self.picked(), self.query_one("#choices", OptionList)
        if hit and not hit[1]:
            self.marked.remove(hit[0]) if hit[0] in self.marked else self.marked.append(hit[0])
            at = lst.highlighted
            self.relist()
            lst.highlighted = at

    def take(self, hit):
        """A folder opens; a file is attached, or with files marked, the marked ones are."""
        if hit and hit[1]:
            self.action_fill()
        elif self.marked or hit:
            self.dismiss([os.path.abspath(p) for p in self.marked or [hit[0]]])

    @on(Input.Submitted)
    def done(self):
        typed = Path(self.query_one(Input).value.strip()).expanduser()
        if typed.is_file() and not self.marked:
            self.dismiss([str(typed.absolute())])
        else:
            self.take(None if self.marked else self.picked())

    @on(OptionList.OptionSelected)
    def clicked(self, event):
        self.query_one(Input).focus()
        self.take(self.shown[event.option_index])


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

