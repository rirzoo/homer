"""homer TUI: browse and manage mail yourself. Opening a message marks it read (the CLI never does)."""
import curses
import email
import email.policy
import imaplib
import json
import os
import shlex
import smtplib
import subprocess
import tempfile
from email.utils import formataddr, getaddresses
from pathlib import Path

from . import outlook
from .cli import BACKENDS
from .common import attachments, body_text, build_message, build_reply, die, get_account, save_attachments

# die() raises SystemExit; the rest are what cli.main catches
ERRORS = (SystemExit, imaplib.IMAP4.error, smtplib.SMTPException, OSError, subprocess.CalledProcessError)
DOWNLOADS = Path("~/Downloads").expanduser()
COMPOSE = "To: \nCc: \nBcc: \nSubject: \nAttach: \n\n"
REPLY = "Attach: \n\n"  # recipients and subject come from the original
LIST_KEYS = "Enter open  c compose  r reply  d delete  f folder  a account  / search  ? help  q quit"
MSG_KEYS = "j/k space scroll  s save attachments  r reply  R reply all  m unread  d delete  M move  q back"
HELP = """\
Message list
  j / k, arrows      move            PgUp / PgDn   page
  Enter              open (marks it read)
  c                  compose a new mail in $EDITOR
  r / R              reply / reply to all
  m                  toggle read / unread
  d                  delete (move to trash)
  M                  move to a folder
  f                  switch folder
  a                  switch account
  /                  search (Gmail: FROM "bob" SINCE 1-Oct-2026, Outlook: from:bob subject:exam)
  g                  refresh           +   load 50 older messages
  q                  quit

Message view
  j / k, space / b   scroll
  s                  save attachments to ~/Downloads
  r R m d M          as in the list
  q                  back to the list

Composing
  Your $EDITOR opens with To/Cc/Bcc/Subject/Attach lines, a blank line, then the body.
  Separate addresses and attachment paths with commas. Save and quit, then:
  y send   d save as draft   e edit again   q discard
  An empty body discards the mail. If sending fails the draft file is kept and its path shown."""


def err(e):
    return str(e).removeprefix("homer: ")


def parse_draft(text):
    """Split an edited draft into header fields and body."""
    head, _, body = text.partition("\n\n")
    raw = dict.fromkeys(("to", "cc", "bcc", "subject", "attach"), "")
    for line in head.splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() in raw:
            raw[key.strip().lower()] = value.strip()
    d = {k: [formataddr(p) for p in getaddresses([raw[k]]) if p[1]] for k in ("to", "cc", "bcc")}
    d["attach"] = [str(Path(f.strip()).expanduser()) for f in raw["attach"].split(",") if f.strip()]
    d["subject"], d["body"] = raw["subject"], body.strip("\n")
    return d


class App:
    def __init__(self, scr, cfg, name):
        self.scr, self.cfg, self.status, self.n = scr, cfg, "", 50
        self.switch(name)

    # --- drawing -------------------------------------------------------------

    def put(self, y, text, attr=0):
        w = self.scr.getmaxyx()[1]
        self.scr.addnstr(y, 0, text.ljust(w), w - 1, attr)  # w-1: curses errors on the bottom-right cell

    def frame(self, left, right, hint):
        h, w = self.scr.getmaxyx()
        self.put(0, f" {left}".ljust(w - len(right) - 2) + right, curses.A_REVERSE)
        self.put(h - 1, self.status or hint, curses.A_BOLD if self.status else curses.A_DIM)

    def say(self, text):
        self.put(self.scr.getmaxyx()[0] - 1, text, curses.A_BOLD)
        self.scr.refresh()

    def menu(self, left, right, items, cur, top, hint, bold=()):
        """Draw a scrolling list with the cursor row reversed; returns the new top row."""
        h = self.scr.getmaxyx()[0]
        rows = h - 2
        top = min(max(top, cur - rows + 1), cur) if items else 0
        self.scr.erase()
        self.frame(left, right, hint)
        for y, i in enumerate(range(top, min(top + rows, len(items))), 1):
            self.put(y, items[i], (curses.A_REVERSE if i == cur else 0) | (curses.A_BOLD if i in bold else 0))
        self.scr.refresh()
        return top

    def nav(self, k, cur, n):
        page = self.scr.getmaxyx()[0] - 2
        step = {"j": 1, "KEY_DOWN": 1, "k": -1, "KEY_UP": -1, "KEY_NPAGE": page, "KEY_PPAGE": -page}.get(k)
        return None if step is None else max(0, min(n - 1, cur + step))

    # --- prompts -------------------------------------------------------------

    def key(self, prompt):
        self.say(prompt)
        return self.scr.getkey()

    def ask(self, prompt):
        h, w = self.scr.getmaxyx()
        self.say(prompt)
        curses.echo()
        curses.curs_set(1)
        try:
            return self.scr.getstr(h - 1, len(prompt), w - len(prompt) - 1).decode(errors="replace").strip()
        finally:
            curses.noecho()
            curses.curs_set(0)

    def pick(self, title, options):
        cur = top = 0
        while True:
            top = self.menu(title, "", options, cur, top, "Enter choose  q cancel")
            k = self.scr.getkey()
            if k in ("\n", "KEY_ENTER"):
                return options[cur] if options else None
            if k in ("q", "\x1b"):
                return None
            n = self.nav(k, cur, len(options))
            cur = cur if n is None else n

    def page(self, title, lines, hint):
        """Scrollable text; returns the first key that isn't a scroll key."""
        top = 0
        while True:
            h, w = self.scr.getmaxyx()
            flat = [line.expandtabs() for line in lines]
            wrapped = [line[i:i + w - 1] for line in flat for i in range(0, max(len(line), 1), w - 1)]
            top = max(0, min(top, len(wrapped) - (h - 2)))
            self.scr.erase()
            self.frame(title, f"{min(top + h - 2, len(wrapped))}/{len(wrapped)}", hint)
            for y, line in enumerate(wrapped[top:top + h - 2], 1):
                self.put(y, line)
            self.scr.refresh()
            k = self.scr.getkey()
            step = {"j": 1, "KEY_DOWN": 1, "k": -1, "KEY_UP": -1, " ": h - 2, "KEY_NPAGE": h - 2,
                    "b": 2 - h, "KEY_PPAGE": 2 - h}.get(k)
            if step is not None:
                top += step
            elif k != "KEY_RESIZE":
                return k

    # --- server --------------------------------------------------------------

    def call(self, fn, *args):
        self.say("working…")  # ponytail: blocking calls freeze the UI ~1s; a worker thread fixes it
        return fn(self.name, self.acct, *args)

    def switch(self, name):
        name, acct = get_account(self.cfg, name)
        provider = acct.get("provider", "gmail")
        self.backend = BACKENDS.get(provider) or die(f"account {name!r}: unknown provider {provider!r}")
        self.name, self.acct, self.folder, self.search, self.trash = name, acct, "INBOX", None, None
        self.rows, self.cur, self.top = [], 0, 0

    def reload(self):
        rows = [list(r) for r in self.call(self.backend.list_messages, self.folder, self.n, False, self.search)]
        if self.backend is outlook:  # keep full ids so an agent's `homer list` can't renumber our rows
            ids = json.loads((outlook.ID_CACHE / f"{self.name}.json").read_text())
            rows = [[i, *r[1:]] for i, r in zip(ids, rows)]  # ponytail: tiny race between list and this read
        self.rows, self.cur = rows, min(self.cur, max(len(rows) - 1, 0))

    def fetch(self, row):
        raw = self.call(self.backend.fetch_raw, self.folder, row[0])
        return email.message_from_bytes(raw, policy=email.policy.default)

    def trash_folder(self):
        if not self.trash:
            names = ["deleteditems"] if self.backend is outlook else self.call(self.backend.folders)
            self.trash = next((f for f in ("deleteditems", "[Gmail]/Trash", "[Gmail]/Bin", "Trash") if f in names),
                              None) or die("no trash folder found; use M to move it instead")
        return self.trash

    # --- actions -------------------------------------------------------------

    def act(self, k, msg=None):
        """Actions shared by the list and the message view. Returns True if the row went away."""
        row = self.rows[self.cur]
        if k in "rR":
            self.compose(msg or self.fetch(row), k == "R")
        elif k == "m":
            self.call(self.backend.mark, self.folder, [row[0]], row[1])
            row[1] = not row[1]
            self.status = "marked " + ("unread" if row[1] else "read")
        elif k in "dM":
            if k == "d":
                dest = self.trash_folder()
                if self.key(f"delete '{row[4][:40]}'? y/n") != "y":
                    return False
            else:
                dest = self.pick("move to folder", self.call(self.backend.folders))
                if not dest:
                    return False
            self.call(self.backend.move, self.folder, [row[0]], dest)
            del self.rows[self.cur]
            self.cur = min(self.cur, max(len(self.rows) - 1, 0))
            self.status = f"moved to {dest}"
            return True
        return False

    def open(self):
        row = self.rows[self.cur]
        msg = self.fetch(row)
        if row[1]:
            self.call(self.backend.mark, self.folder, [row[0]], True)
            row[1] = False
        lines = [f"{h}: {msg[h]}" for h in ("From", "To", "Cc", "Date", "Subject") if msg[h]]
        lines += [""] + body_text(msg).splitlines()
        parts = attachments(msg)
        if parts:
            lines += ["", "Attachments:"] + [f"  [{i}] {p.get_filename()} ({p.get_content_type()})"
                                             for i, p in enumerate(parts, 1)]
        while True:
            k = self.page(msg["Subject"] or "(no subject)", lines, MSG_KEYS)
            self.status = ""
            if k == "s" and parts:
                self.status = "saved " + ", ".join(p.name for p in save_attachments(parts, DOWNLOADS))
            elif k in ("r", "R", "m", "d", "M"):
                if self.act(k, msg) or k in "rR":
                    return
            elif k not in ("s", "KEY_RESIZE"):
                return

    def edit(self, path):
        curses.def_prog_mode()
        curses.endwin()
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        subprocess.run([*shlex.split(editor), path])
        curses.reset_prog_mode()
        self.scr.refresh()

    def compose(self, orig=None, reply_all=False):
        fd, path = tempfile.mkstemp(prefix="homer-", suffix=".txt")
        os.write(fd, (REPLY if orig else COMPOSE).encode())
        os.close(fd)
        while True:
            self.edit(path)
            d = parse_draft(Path(path).read_text())
            if not d["body"].strip():
                os.unlink(path)
                self.status = "empty body, discarded"
                return
            missing = [f for f in d["attach"] if not Path(f).is_file()]
            problem = (f"attachment not found: {', '.join(missing)}" if missing
                       else "no To: address" if not orig and not d["to"] else "")
            if problem:
                choice = self.key(f"{problem}   e edit  q discard")
            else:
                msg = (build_reply(self.acct, orig, d["body"], reply_all, d["attach"]) if orig else
                       build_message(self.acct, d["to"], d["cc"], d["bcc"], d["subject"], d["body"], d["attach"]))
                choice = self.key(f"to {msg['To']}: y send  d draft  e edit  q discard")
            if choice == "q":
                os.unlink(path)
                self.status = "discarded"
                return
            if choice in ("y", "d") and not problem:
                break
        try:
            if choice == "d":
                self.call(self.backend.save_draft, msg)
                self.status = "draft saved"
            else:
                self.call(self.backend.send, msg, [a.addr_spec for h in ("To", "Cc", "Bcc") if msg[h]
                                                   for a in msg[h].addresses])
                self.status = f"sent to {msg['To']}"
        except ERRORS as e:
            self.status = f"{err(e)} (draft kept at {path})"
            return
        os.unlink(path)

    # --- main loop -----------------------------------------------------------

    def handle(self, k):
        n = self.nav(k, self.cur, len(self.rows))
        if n is not None:
            self.cur = n
        elif k in ("\n", "KEY_ENTER") and self.rows:
            self.open()
        elif k == "g":
            self.reload()
        elif k == "+":
            self.n += 50
            self.reload()
        elif k == "c":
            self.compose()
        elif k == "/":
            self.search = self.ask("search: ") or None
            self.cur = 0
            self.reload()
        elif k == "f":
            folder = self.pick("folder", self.call(self.backend.folders))
            if folder:
                self.folder, self.search, self.cur = folder, None, 0
                self.reload()
        elif k == "a":
            name = self.pick("account", list(self.cfg.get("accounts", {})))
            if name:
                self.switch(name)
                self.reload()
        elif k == "?":
            self.page("help", HELP.splitlines(), "any key: back")
        elif k in ("r", "R", "m", "d", "M") and self.rows:
            self.act(k)

    def loop(self):
        curses.curs_set(0)
        k = "g"  # first pass loads the inbox
        while k != "q":
            try:
                self.handle(k)
            except ERRORS as e:
                self.status = err(e)
            items = [f"{'*' if unread else ' '} {when.astimezone().strftime('%m-%d %H:%M') if when else '':11}  "
                     f"{who[:24]:24}  {subject}" for _, unread, when, who, subject in self.rows]
            where = self.folder + (f"  search: {self.search}" if self.search else "")
            self.top = self.menu(f"{self.name}  {where}", f"{len(self.rows)} msgs", items, self.cur, self.top,
                                 LIST_KEYS, {i for i, r in enumerate(self.rows) if r[1]})
            k = self.scr.getkey()
            self.status = ""


def main(cfg, name):
    os.environ.setdefault("ESCDELAY", "25")  # Esc cancels without a 1s wait
    try:
        curses.wrapper(lambda scr: App(scr, cfg, name).loop())
    except KeyboardInterrupt:
        pass
