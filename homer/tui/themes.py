"""Homer's own themes, and the Textual ones it leaves out as near copies of others."""

from textual.theme import Theme

DROPPED = ("tokyo-night", "catppuccin-frappe", "ansi-dark", "atom-one-light")


def chrome(v):
    """Borders, header bar, mail frames and texture for a theme that doesn't set its own: the look homer has always
    had. A theme's `variables` win over these. CSS variables can't refer to other variables, so these are worked out
    from the theme's resolved colours."""
    return {"homer-rule": "solid", "homer-rule-color": v["panel-lighten-2"], "homer-box": "round",
            "homer-card": "none", "homer-card-left": "none", "homer-card-pad": "0", "homer-card-bg": "transparent",
            "homer-hatch": "none",
            "homer-bar": v["panel"], "homer-bar-text": v["foreground"], "homer-bar-muted": v["text-muted"]}


# hatch fills the empty part of the preview (not the gaps between words), so it reads as the page the mails sit on
THEMES = [
    # an old green-screen terminal: soft phosphor green, faint scanlines
    Theme("phosphor", primary="#46c86a", secondary="#2b7a3f", accent="#a3e635", foreground="#5ee083",
          background="#050a06", surface="#08120a", panel="#0c1f10", success="#46c86a", warning="#d4e157",
          error="#ef5350", dark=True,
          variables={"homer-rule": "heavy", "homer-rule-color": "#1d5c2e", "homer-box": "double",
                     "homer-card-left": "thick #2f9e4f", "homer-card-pad": "0 1",
                     "homer-hatch": "horizontal #46c86a 12%",
                     "homer-bar": "#0e2414", "homer-bar-text": "#7dff9b", "homer-bar-muted": "#3f8f55"}),
    # a holographic blueprint: a grid behind see-through glass cards, pale cyan lines
    Theme("blueprint", primary="#4fc3f7", secondary="#1e88e5", accent="#e0f7ff", foreground="#bfe9ff",
          background="#04101f", surface="#071a33", panel="#0a2342", success="#64ffda", warning="#ffd54f",
          error="#ff5252", dark=True,
          variables={"homer-rule": "double", "homer-rule-color": "#4fc3f7 35%", "homer-box": "double",
                     "homer-card": "round #4fc3f7 55%", "homer-card-left": "round #4fc3f7 55%",
                     "homer-card-pad": "0 1", "homer-card-bg": "#4fc3f7 7%", "homer-hatch": '"┼" #4fc3f7 10%',
                     "homer-bar": "#4fc3f7 18%", "homer-bar-text": "#e0f7ff", "homer-bar-muted": "#7fb8d8"}),
    # an 80s sunset: deep violet layers, neon pink and cyan, a faint pink grid under the mails
    Theme("synthwave", primary="#ff2a6d", secondary="#7a04eb", accent="#05d9e8", foreground="#f5e9ff",
          background="#0d0221", surface="#1b0b3a", panel="#261447", success="#01ffc3", warning="#f9c80e",
          error="#ff3864", dark=True,
          variables={"homer-rule": "heavy", "homer-rule-color": "#7a04eb", "homer-box": "heavy",
                     "homer-card-left": "tall #05d9e8 80%", "homer-card-pad": "0 1",
                     "homer-card-bg": "#7a04eb 12%", "homer-hatch": '"┼" #ff2a6d 9%',
                     "homer-bar": "#ff2a6d", "homer-bar-text": "#0d0221", "homer-bar-muted": "#5c0a2e"}),
    # a notebook: ruled lines and a red margin
    Theme("paper", primary="#2b4c7e", secondary="#5b5446", accent="#c0392b", foreground="#1c1b19",
          background="#f4efe1", surface="#ebe4d1", panel="#e2d9c0", success="#2f6f3e", warning="#b7791f",
          error="#c0392b", dark=False,
          variables={"homer-rule": "dashed", "homer-rule-color": "#b8ad8f", "homer-box": "solid",
                     "homer-card-left": "solid #c0392b 60%", "homer-card-pad": "0 1",
                     "homer-hatch": "horizontal #7fa7c9 35%"}),
]
