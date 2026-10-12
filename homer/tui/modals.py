"""Small windows."""

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

