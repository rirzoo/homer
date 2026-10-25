"""Homer folder mapping (W16)."""

from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, SelectionList, Static
from textual.widgets.selection_list import Selection

from .base import CAUGHT, err
from .widgets import Header, Status


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
        # only Mailbox has a fixed target (its inbox); moving into any other folder fed by several asks which (D26)
        self.target = self.chosen[0] if self.chosen and folder == "Mailbox" else None

    def check_action(self, action, parameters):
        return action != "target" or self.folder == "Mailbox"

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
            + (f"Moves to {self.folder} go to the one marked ◆. " if self.folder == "Mailbox" else
               f"When you move mail to {self.folder} and more than one is ticked, Homer asks which. ")
            + "Space or Enter ticks a folder, s saves.")
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

