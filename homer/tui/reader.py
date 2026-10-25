"""Conversation, full width (W5, W6)."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer

from ..common import save_attachments
from .base import DOWNLOADS, HELP, conv_blocks, loc, open_external, open_in_browser
from .widgets import ConvView, Header, Status
from .modals import AttachPicker, HelpScreen, LinkPicker


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
        view.scroll_page_down(animate=False) if d > 0 else view.scroll_page_up(animate=False)

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

