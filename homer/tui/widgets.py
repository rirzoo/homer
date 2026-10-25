"""Shared widgets."""

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Input, OptionList, Static


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
                Binding("D", "screen.delete_folder", "delete folder"),
                Binding("N", "screen.notify_folder", "notifications"), Binding("f", "screen.folders", "close"),
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

