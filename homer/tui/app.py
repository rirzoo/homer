"""The app: accounts, theme and colour roles, and the window it starts on."""

import re
import shutil

from rich.errors import StyleSyntaxError
from rich.style import Style
from textual.app import App
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static

from .. import store
from ..cli import BACKENDS
from . import kitty
from .base import ROLES, STYLE
from .widgets import Header
from .mainscreen import MainScreen
from .reader import ReaderScreen
from .look import LookScreen
from .themes import DROPPED, THEMES, chrome


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
    Header { height: 1; background: $homer-bar; color: $homer-bar-text; }
    #hleft { width: 1fr; padding: 0 1; text-style: bold; }
    #hright { width: auto; padding: 0 1; color: $homer-bar-muted; }
    Status { height: 1; padding: 0 1; }
    Status.error { color: $error; }
    OptionList { border: none; padding: 0; background: $background; }
    #list, #folders, ConvView { scrollbar-gutter: stable; scrollbar-size-vertical: 1; }
    OptionList:focus { border: none; }
    #body { height: 1fr; }
    #folders { width: 26; display: none; border-right: $homer-rule $homer-rule-color; }
    MainScreen.panes #folders { display: block; }
    #listcol { width: 1fr; }
    #list { height: 1fr; }
    #searchrow { height: 1; display: none; }
    #searchrow.on { display: block; }
    #search { width: 1fr; height: 1; border: none; padding: 0 1; }
    #scope { width: auto; }
    #preview { width: 1fr; border-left: $homer-rule $homer-rule-color; padding: 0 1; }
    MainScreen.narrow #preview { display: none; }
    MainScreen.narrow.panes #listcol { display: none; }
    MainScreen.narrow.panes #folders { width: 1fr; }
    ConvView { hatch: $homer-hatch; }
    ConvView .mail { margin-bottom: 1; border: $homer-card; border-left: $homer-card-left; padding: $homer-card-pad;
                     background: $homer-card-bg; }
    #reader { padding: 0 1; }
    #form { height: auto; border-bottom: $homer-rule $homer-rule-color; }
    .field { height: 1; }
    .label { width: 10; padding: 0 1; color: $text-muted; }
    .field Input { width: 1fr; height: 1; border: none; padding: 0; }
    .field Input.bad { color: $error; text-style: underline; }
    FromField { width: 1fr; }
    FromField:focus { background: $boost; }
    #suggest { display: none; height: auto; max-height: 8; width: 60; margin-left: 10; border: $homer-box $panel-lighten-2; }
    #suggest.on { display: block; }
    #attachments { height: auto; padding: 0 1; border-top: $homer-rule $homer-rule-color; }
    #bodyscroll { height: 1fr; padding: 0 1; }
    Box { align: center middle; background: $background 60%; }
    .box { width: 64; height: auto; max-height: 80%; padding: 1 2; border: $homer-box $accent; background: $surface; }
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
    #lookleft { width: 44; border-right: $homer-rule $homer-rule-color; }
    .looktitle { padding: 1 1 0 1; color: $text-muted; }
    #themes { height: 1fr; }
    #roles, #backbox, #backs { height: auto; }
    #sample { width: 1fr; }
    #samplebody { height: 1fr; }
    #samplelist { width: 3fr; }
    #sampleconv { width: 2fr; border-left: $homer-rule $homer-rule-color; padding: 0 1; }
    """

    def __init__(self, cfg, name):
        super().__init__()
        self.scroll_sensitivity_y = 1.0  # a line per wheel step; Textual's 2 races through a mail
        for t in THEMES:
            self.register_theme(t)
        for n in DROPPED:
            self.unregister_theme(n)
        maps, self.state = store.load_maps(), store.load_state()
        names = self.state.get("names", {})
        self.accounts = {n: store.Account(n, a, BACKENDS[a.get("provider", "gmail")], maps, names.get(n))
                         for n, a in cfg.get("accounts", {}).items() if a.get("provider", "gmail") in BACKENDS}
        self.first, self.outbox, self.look, self.config_note = name, [], cfg.get("tui", {}), ""
        self.dressed = None  # the kitty options last sent, so moving through ctrl+t only calls kitty on a change

    imaged = False  # kitty is showing a background image behind homer

    def get_css_variables(self):
        v = super().get_css_variables()
        v = {**chrome(v), **v}
        if self.imaged:
            v["homer-hatch"] = "none"  # the image is the texture; a hatch over it reads as a dark mesh
        return v

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
        if self.look.get("window") == "kitty" and not kitty.inside() and not shutil.which("kitty"):
            bad.append('window = "kitty" (kitty isn\'t installed)')
        self.config_note = "! [tui] in your config: ignored " + ", ".join(bad) if bad else ""

    def background(self, theme):
        """The background picked for a theme with ctrl+t; its first image until you pick, "none" for none."""
        return self.state.get("backgrounds", {}).get(theme, next(iter(kitty.images(theme)), None))

    def dress(self, pick=None):
        """In homer's kitty window, give kitty the theme's font, background image and see-through colours."""
        if not kitty.inside():
            return
        options = kitty.look(self.theme, pick or self.background(self.theme), self.get_css_variables())
        if options != self.dressed:
            self.dressed = options
            kitty.dress(options)
            if self.imaged != ("background_image=none" not in options):
                self.imaged = not self.imaged
                self.refresh_css(animate=False)

    def on_mount(self):
        self.paint_roles()
        self.dress()
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
                theme, colors, backgrounds = result
                base = {**ROLES, **self.look.get("colors", {})}
                self.state["theme"] = theme
                self.state["colors"] = {r: v for r, v in colors.items() if v != base.get(r)}
                self.state["backgrounds"] = backgrounds
                store.save_state(self.state)
            self.paint_roles()
            self.dress()
            self.repaint_all()

        self.push_screen(LookScreen(self.theme, self.colors(), self.state.get("backgrounds", {})), done)

    def action_safe_quit(self):
        main = next((s for s in self.screen_stack if isinstance(s, MainScreen)), None)
        main.action_quit() if main else self.exit()


def main(cfg, name):
    if kitty.wanted(cfg) and kitty.open_window():
        return  # homer carries on in its kitty window; this terminal gets its prompt back
    Homer(cfg, name).run()

