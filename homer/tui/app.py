"""The app: accounts, theme and colour roles, and the window it starts on."""

import re

from rich.errors import StyleSyntaxError
from rich.style import Style
from textual.app import App
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static

from .. import store
from ..cli import BACKENDS
from .base import ROLES, STYLE
from .widgets import Header
from .mainscreen import MainScreen
from .reader import ReaderScreen
from .look import LookScreen


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
        self.attach_dir = "~/"  # where the attach box starts: the folder of the last file attached

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

