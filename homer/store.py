"""Data side of the TUI, kept free of Textual so it can be tested: homer folders, conversations, cache,
contacts and the mail homer sends. Design notes are in docs/TUI.md (folder model) and docs/FRAME.md (D-numbers)."""
import json
import os
import re
import time
import tomllib
from collections import Counter
from datetime import datetime, timezone
from email.utils import formataddr
from html import escape
from pathlib import Path

from .common import CONFIG, build_forward, build_message, build_reply

HOMER_URL = "https://github.com/rirzoo/homer"
FOOTER_TEXT = f"Sent with homer ({HOMER_URL})"
FOOTER_HTML = f'<p>Sent with <a href="{HOMER_URL}">homer</a></p>'
FOLDERS_FILE = CONFIG.parent / "folders.toml"
STATE_FILE = CONFIG.parent / "tui.toml"  # choices made inside the TUI: the theme and account names
CACHE = Path("~/.cache/homer/tui").expanduser()
SYNC_EVERY, PAGE = 120, 50  # seconds between syncs (the TUI's and the background service's); mails per listing
BASE = ("Mailbox", "Drafts", "Archive", "Trash")
EDITABLE = ("Mailbox", "Archive")  # Drafts and Trash are found by homer (D4, W2)
# Gmail shows special folders to IMAP as labels with these names (X-GM-LABELS)
GMAIL_LABEL = {r"\Inbox": r"\Inbox", r"\Sent": r"\Sent", r"\Drafts": r"\Draft", r"\Important": r"\Important",
               r"\Flagged": r"\Starred"}
_URL = re.compile(r"https?://[^\s<>\"']+")


# --- outgoing mail: plain text in, HTML + plain out, always with the footer (D10, D11) ---------------

def _link(m):
    url = m.group(0).rstrip(".,;:!?)")
    return f'<a href="{url}">{url}</a>{m.group(0)[len(url):]}'


def body_html(text):
    """Blank-line paragraphs become <p>, bare URLs become links; nothing else is formatting."""
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    return "\n".join("<p>" + _URL.sub(_link, escape(p, quote=False)).replace("\n", "<br>\n") + "</p>" for p in paras)


def with_footer(text):
    """Your text -> (html, plain), both ending in "Sent with homer"; the plain copy spells out the link."""
    text = text.rstrip()
    return (body_html(text) + "\n" if text else "") + FOOTER_HTML, (text + "\n\n" if text else "") + FOOTER_TEXT


def strip_footer(text):
    """Drop homer's own footer from a reopened draft, so saving it again doesn't add a second one."""
    return re.sub(r"\s*Sent with homer(?: \(" + re.escape(HOMER_URL) + r"\))?\s*$", "", text.rstrip())


def build_outgoing(acct, kind, orig, to, cc, bcc, subject, text, attach, headers=None):
    """kind: new, reply, all (reply all) or forward. The form's recipients and subject win over the defaults."""
    html, plain = with_footer(text)
    if kind in ("reply", "all"):
        msg = build_reply(acct, orig, html, kind == "all", attach, html=True, plain=plain)
    elif kind == "forward":
        msg = build_forward(acct, orig, to, cc, bcc, html, attach, html=True, plain=plain)
    else:
        msg = build_message(acct, to, cc, bcc, subject, html, attach, html=True, plain=plain)
    for h, v in (("To", to), ("Cc", cc), ("Bcc", bcc)):
        del msg[h]
        if v:
            msg[h] = ", ".join(v)
    if subject:
        msg.replace_header("Subject", subject)
    for h, v in (headers or {}).items():  # threading headers kept from a reopened reply draft
        if v and not msg[h]:
            msg[h] = v
    return msg


def reply_recipients(acct, orig, everyone):
    """The To and Cc a reply would get, for filling in the form."""
    msg = build_reply(acct, orig, "", everyone)
    return [[str(a) for a in msg[h].addresses] if msg[h] else [] for h in ("To", "Cc")]


# --- homer folders (docs/TUI.md, "The folder model") ---------------------------------------------------

def load_maps():
    try:
        return tomllib.loads(FOLDERS_FILE.read_text())
    except FileNotFoundError:
        return {}


def save_maps(maps):
    lines = ["# Written by homer's TUI: the server folders that feed each homer folder.",
             "# The first one listed is where moves into that folder go.", ""]
    for name, folders in maps.items():
        lines.append(f"[{json.dumps(name)}]")  # JSON strings are valid TOML basic strings
        lines += [f"{json.dumps(k, ensure_ascii=False)} = {json.dumps(v, ensure_ascii=False)}"
                  for k, v in folders.items()]
        lines.append("")
    FOLDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    FOLDERS_FILE.write_text("\n".join(lines))


def load_state():
    try:
        return tomllib.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        return {}


def save_state(state):
    """theme = "nord", then [colors] (role -> style), [names] (account config key -> name shown) and [notify]
    (account config key -> the homer folders whose new mail the background service announces)."""
    q = lambda v: json.dumps(v, ensure_ascii=False)  # JSON strings and lists of them are valid TOML
    lines = ["# Written by homer's TUI: the theme and colours (ctrl+t), account names (R in the folder pane)",
             "# and the folders that notify you of new mail (N in the folder pane)."]
    if state.get("theme"):
        lines.append(f"theme = {q(state['theme'])}")
    for table in ("colors", "names", "notify"):
        if state.get(table):
            lines += ["", f"[{table}]"] + [f"{q(k)} = {q(v)}" for k, v in state[table].items()]
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text("\n".join(lines) + "\n")


def when_dt(when):
    if not when:
        return datetime.min.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(when)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Account:
    """One configured account: its backend, homer folders and on-disk cache."""

    def __init__(self, name, acct, backend, maps, label=None):
        self.name, self.acct, self.backend, self.maps = name, acct, backend, maps
        self.label = label or name
        self.email = acct["email"]
        self.me = {self.email.lower()}
        self.dir = CACHE / name
        try:
            self.special = json.loads((self.dir / "special.json").read_text())
        except (FileNotFoundError, ValueError):
            self.special = None  # load_special() fills it from the server

    def call(self, fn, *args):
        return getattr(self.backend, fn)(self.name, self.acct, *args)

    def load_special(self):
        self.special = self.call("special_folders")
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "special.json").write_text(json.dumps(self.special))

    def folders(self):
        """Homer folder -> server folders feeding it, the move target first. Base four, then yours in order."""
        sp, own = self.special or {}, self.maps.get(self.name, {})
        out = {"Mailbox": own.get("Mailbox") or [f for f in (sp.get(r"\Inbox"), sp.get(r"\Sent")) if f],
               "Drafts": [sp[r"\Drafts"]] if r"\Drafts" in sp else [],
               "Archive": own.get("Archive") or ([sp[r"\All"]] if r"\All" in sp else []),
               "Trash": [sp[r"\Trash"]] if r"\Trash" in sp else []}
        out.update((k, v) for k, v in own.items() if k not in BASE)
        return out

    def custom(self):
        return [k for k in self.folders() if k not in BASE]

    def target(self, folder):
        srcs = self.folders().get(folder)
        if not srcs:
            raise ValueError(f"{folder} has no server folder to move into yet")
        return srcs[0]

    def set_sources(self, folder, sources):
        own = self.maps.setdefault(self.name, {})
        if sources or folder not in BASE:
            own[folder] = list(sources)
        else:
            own.pop(folder, None)  # nothing ticked: Mailbox and Archive go back to homer's default
        save_maps(self.maps)

    def rename(self, old, new):
        own = self.maps[self.name]
        self.maps[self.name] = {(new if k == old else k): v for k, v in own.items()}
        save_maps(self.maps)
        if self._cache(old).exists():
            self._cache(old).rename(self._cache(new))

    def delete(self, folder):
        del self.maps[self.name][folder]
        save_maps(self.maps)
        self._cache(folder).unlink(missing_ok=True)

    def labels_of(self, sources):
        """The Gmail labels that the given server folders stand for (All Mail, Trash and Spam aren't labels)."""
        flag = {v: k for k, v in (self.special or {}).items()}
        return {GMAIL_LABEL[flag[f]] if f in flag else f for f in sources if f not in flag or flag[f] in GMAIL_LABEL}

    def fetch(self, folder, n, query=None, everywhere=False):
        """Message dicts for a homer folder: every source merged. Archive is the catch-all (D5): it leaves out
        conversations that Mailbox or your own folders show, by Gmail label or by the cached listings."""
        if everywhere:
            return self.call("summaries", None, n, query)
        folders = self.folders()
        msgs = [m for src in folders[folder] for m in self.call("summaries", src, n, query)]
        if folder == "Archive":
            others = [k for k in folders if k not in ("Archive", "Trash")]
            hide = self.labels_of([s for k in others for s in folders[k]])
            threads = {m["thread"] for m in msgs if hide & set(m["labels"])}
            threads |= {m["thread"] for k in others if k != "Drafts" for m in self.cached(k)}
            msgs = [m for m in msgs if m["thread"] not in threads]
        return msgs

    def _cache(self, folder):
        return self.dir / (re.sub(r"[^\w.-]", "_", folder) + ".json")

    def cached(self, folder):
        try:
            return json.loads(self._cache(folder).read_text())
        except (FileNotFoundError, ValueError):
            return []

    def store(self, folder, msgs):
        """Written whole and then renamed, since the TUI and the background service both write these."""
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._cache(folder).with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(msgs))
        tmp.replace(self._cache(folder))

    def saved_at(self, folder):
        """When this folder's listing was last saved, by the TUI or the background service (None: never)."""
        try:
            return self._cache(folder).stat().st_mtime
        except FileNotFoundError:
            return None

    def fresh(self, folder):
        at = self.saved_at(folder)
        return at is not None and time.time() - at < SYNC_EVERY

    def contacts(self):
        """Addresses from your mail on this account, the ones you see most first (W8)."""
        count, names = Counter(), {}
        for f in self.dir.glob("*.json"):
            if f.name == "special.json":
                continue
            try:
                msgs = json.loads(f.read_text())
            except ValueError:
                continue
            for m in msgs:
                for name, addr in [[m["name"], m["addr"]], *m["to"]]:
                    if addr and addr.lower() not in self.me:
                        count[addr.lower()] += 1
                        if name or addr.lower() not in names:
                            names[addr.lower()] = formataddr((name, addr))
        return [names[a] for a, _ in count.most_common()]


# --- conversations (D3) -------------------------------------------------------------------------

def conversations(msgs, me, single=False):
    """Group message dicts into conversations, newest first. The same mail seen in two folders (mail to
    yourself sits in INBOX and Sent) becomes one message with two locations, so moves take both."""
    by_id = {}
    for m in msgs:
        k = m["msgid"] or f"{m['folder']}:{m['id']}"
        if k in by_id:
            by_id[k]["locs"].append([m["folder"], m["id"]])
            by_id[k]["unread"] = by_id[k]["unread"] or m["unread"]
        else:
            by_id[k] = {**m, "locs": [[m["folder"], m["id"]]]}
    groups = {}
    for k, m in by_id.items():
        groups.setdefault(k if single else m["thread"] or k, []).append(m)
    out = []
    for key, ms in groups.items():
        ms.sort(key=lambda m: when_dt(m["when"]))
        mine = [m["addr"].lower() in me for m in ms]
        others = [m for m, own in zip(ms, mine) if not own]
        if others:
            who = others[-1]["name"] or others[-1]["addr"]
        else:
            to = ms[-1]["to"][0] if ms[-1]["to"] else ["", ""]
            who = "To " + (to[0] or to[1] or "(no one yet)")
        out.append({"key": key, "msgs": ms, "unread": any(m["unread"] for m in ms), "when": when_dt(ms[-1]["when"]),
                    "subject": ms[0]["subject"] or ms[-1]["subject"], "attach": any(m["attach"] for m in ms),
                    "who": who, "mine": not others})
    out.sort(key=lambda c: c["when"], reverse=True)
    return out


# --- dates (docs/FRAME.md, "Dates") ------------------------------------------------------------------

def short_when(dt, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone()
    dt = dt.astimezone()
    secs = (now - dt).total_seconds()
    if secs < 60:
        return "now"
    if secs < 3600:
        return f"{int(secs // 60)}m"
    if dt.date() == now.date():
        return f"{dt:%H:%M}"
    if (now.date() - dt.date()).days < 7:
        return f"{dt:%a}"
    return f"{dt.day} {dt:%b}" if dt.year == now.year else f"{dt.day} {dt:%b %y}"


def long_when(dt):
    dt = dt.astimezone()
    return f"{dt:%a} {dt.day} {dt:%b}, {dt:%H:%M}"
