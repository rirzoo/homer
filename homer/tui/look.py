"""Theme and colours (ctrl+t)."""

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from .. import store
from . import kitty
from .base import ROLES, STYLE, conv_blocks, list_row, loc
from .widgets import ConvView, Header, MailList, NoWrap, Status


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
    """Themes, colour roles and background images beside a sample mailbox painted in them. Roles take the theme's
    own colours ($accent …), so they follow every theme. Enter keeps it all, Esc puts back what you had."""
    BINDINGS = [Binding("tab", "switch", "themes / colours / background", priority=True),
                Binding("enter", "save", "save", priority=True), Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, theme, colors, backgrounds):
        super().__init__()
        self.pick, self.draft, self.backs = theme, dict(colors), dict(backgrounds)
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
                with Vertical(id="backbox"):
                    yield Static(Text("Background", "bold"), classes="looktitle")
                    yield MailList(id="backs")
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
        self.fill_backs()
        self.repaint()

    @on(OptionList.OptionHighlighted, "#backs")
    def try_back(self, event):
        self.backs[self.pick] = event.option.id
        self.app.dress(event.option.id)

    def back(self):
        return self.backs.get(self.pick) or self.app.background(self.pick)

    def fill_backs(self):
        """The images this theme comes with, and none; themes without images hide the list."""
        names = kitty.images(self.pick)
        box, backs = self.query_one("#backbox"), self.query_one("#backs", MailList)
        box.display = bool(names)
        backs.set_options([Option(n, id=n) for n in ("none", *names)] if names else [])
        if names:
            backs.highlighted = backs.get_option_index(self.back())
        elif backs.has_focus:
            self.query_one("#themes").focus()

    def repaint(self):
        self.app.paint_roles(self.pick, self.draft)
        self.app.dress(self.back())
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
                                       view.text_width(40))[0])
        where = ("" if not kitty.images(self.pick) or kitty.inside() else
                 '; backgrounds show in the kitty window ([tui] window = "kitty")')
        self.query_one(Status).say(f"{self.pick} · Tab moves between themes, colours and background; colours come "
                                   f"from the theme, so they change with it{where}")

    def role(self):
        roles = self.query_one("#roles", RoleList)
        return list(ROLES)[roles.highlighted or 0]

    def action_switch(self):
        lists = [self.query_one(f"#{n}") for n in ("themes", "roles", "backs")]
        lists = [w for w in lists if w.display and w.option_count]
        at = next((i for i, w in enumerate(lists) if w.has_focus), -1)
        lists[(at + 1) % len(lists)].focus()

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
        self.dismiss((self.pick, self.draft, self.backs))

