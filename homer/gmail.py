"""Gmail over IMAP/SMTP with an app password. Works for any IMAP/SMTP server via `imap`/`smtp` config keys."""
import email
import email.policy
import functools
import getpass
import imaplib
import re
import smtplib
import sys
import threading
import time

from .common import die, secret_get, secret_set

IDLE = 600  # seconds; a connection unused this long is replaced, since NATs and Gmail drop idle ones silently
_sessions = {}  # account name -> _Session, reused across calls so the TUI pays connect+login once
_locks = {}  # account name -> Lock; imaplib isn't thread-safe, so one call at a time per account


def _password(name):
    return secret_get(name, "password") or die(f"no password stored; run: homer login -a {name}")


def _connect(name, acct):
    m = imaplib.IMAP4_SSL(acct.get("imap", "imap.gmail.com"), acct.get("imap_port", 993), timeout=30)
    m.login(acct["email"], _password(name))
    return m


class _Session:
    def __init__(self, m):
        self.m, self.folder, self.used = m, None, time.monotonic()


def _drop(name):
    s = _sessions.pop(name, None)
    if s:
        try:
            s.m.shutdown()  # no LOGOUT round trip; the socket may already be dead
        except OSError:
            pass


def _session(fn):
    """Run fn(session, ...) on the account's cached connection, reconnecting once if it was dropped.
    Only for calls that are safe to repeat: a retry may resend a command the server already ran."""
    @functools.wraps(fn)
    def run(name, acct, *args, **kw):
        with _locks.setdefault(name, threading.Lock()):
            s = _sessions.get(name)
            fresh = s is None or time.monotonic() - s.used > IDLE
            if fresh:
                _drop(name)
                s = _sessions[name] = _Session(_connect(name, acct))
            try:
                return fn(s, *args, **kw)
            except (imaplib.IMAP4.abort, OSError):
                _drop(name)
                if fresh:
                    raise
                s = _sessions[name] = _Session(_connect(name, acct))
                return fn(s, *args, **kw)
            finally:
                s.used = time.monotonic()
    return run


def _select(s, folder, refresh=False):
    """Select folder unless it already is; returns the message count when it selects.
    Read-write, so mark/move need no second SELECT; every fetch uses BODY.PEEK, so \\Seen never flips."""
    if s.folder == folder and not refresh:
        return None
    s.folder = None  # a failed SELECT leaves nothing selected
    typ, data = s.m.select(f'"{folder}"')
    if typ != "OK":
        die(f"cannot open folder {folder!r}: {data[0].decode(errors='replace')}")
    s.folder = folder
    return int(data[0])


def _ok(res, what):
    if res[0] != "OK":
        die(f"{what} failed: {res[1][0].decode(errors='replace') if res[1] and res[1][0] else res}")


def _uids(refs):
    bad = [r for r in refs if not r.isdigit()]
    if bad:
        die(f"IMAP message ids are numeric UIDs, got {', '.join(bad)}")
    return ",".join(refs)


def _folder_name(line):
    s = line.decode(errors="replace")
    hit = re.match(r'\((.*?)\) (?:"[^"]*"|NIL) (.*)', s)
    return hit.group(2).strip('"') if hit else s


# --- backend interface (same functions as outlook.py) -----------------------

def login(name, acct):
    if not sys.stdin.isatty():
        die("password prompt needs a real terminal; run this in a normal terminal window")
    secret_set(name, "password", getpass.getpass(f"app password for {acct['email']}: "))
    _drop(name)  # a cached session would still hold the old password's login
    _connect(name, acct).logout()


@_session
def folders(s):
    return [_folder_name(line) for line in s.m.list()[1]]


@_session
def list_messages(s, folder, n, unread, search, ids=False):  # ids: only Outlook needs it
    total = _select(s, folder, refresh=True)  # fresh SELECT, so new mail shows up
    query = " ".join(filter(None, ["UNSEEN" if unread else "", search]))
    items = "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
    if query:
        typ, data = s.m.uid("SEARCH", None, query)
        if typ != "OK":
            die(f"search failed: {data}")
        uids = sorted(data[0].split(), key=int)[-n:]
        if not uids:
            return []
        typ, data = s.m.uid("FETCH", b",".join(uids), items)
    else:  # the newest n are the last n sequence numbers: no SEARCH round trip
        if not total:
            return []
        typ, data = s.m.fetch(f"{max(1, total - n + 1)}:{total}", items)
    rows = []
    for i, item in enumerate(data):
        if not isinstance(item, tuple):
            continue
        # some servers put UID/FLAGS after the literal, in the next element
        tail = data[i + 1] if i + 1 < len(data) and isinstance(data[i + 1], bytes) else b""
        meta = item[0] + tail
        uid = int(re.search(rb"UID (\d+)", meta).group(1))
        h = email.message_from_bytes(item[1], policy=email.policy.default)
        frm = h["from"]
        who = (frm.addresses[0].display_name or frm.addresses[0].addr_spec) if frm and frm.addresses else ""
        rows.append((uid, b"\\Seen" not in meta, h["date"].datetime if h["date"] is not None else None,
                     who, h["subject"] or ""))
    return [(str(r[0]), *r[1:]) for r in sorted(rows, key=lambda r: r[0], reverse=True)]


@_session
def fetch_raw(s, folder, ref):
    _select(s, folder)
    typ, data = s.m.uid("FETCH", _uids([ref]), "(BODY.PEEK[])")
    return next((i[1] for i in data if isinstance(i, tuple)), None) or die(f"no message with UID {ref}")


def send(name, acct, msg, rcpts):
    with smtplib.SMTP(acct.get("smtp", "smtp.gmail.com"), acct.get("smtp_port", 587)) as s:
        s.starttls()
        s.login(acct["email"], _password(name))
        s.send_message(msg, to_addrs=rcpts)


# save_draft and mkdir use their own connection: they're rare, and a retry could make a second draft or folder

def save_draft(name, acct, msg):
    with _connect(name, acct) as m:
        drafts = next((_folder_name(line) for line in m.list()[1]
                       if b"\\Drafts" in line.split(b")")[0]), "Drafts")  # RFC 6154 special-use flag
        _ok(m.append(f'"{drafts}"', r"(\Draft \Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes()),
            "save draft")


def mkdir(name, acct, folder):
    with _connect(name, acct) as m:
        _ok(m.create(f'"{folder}"'), "create folder")


@_session
def move(s, folder, refs, dest):
    _select(s, folder)
    _ok(s.m.uid("MOVE", _uids(refs), f'"{dest}"'), "move")


@_session
def mark(s, folder, refs, read):
    _select(s, folder)
    _ok(s.m.uid("STORE", _uids(refs), "+FLAGS" if read else "-FLAGS", r"(\Seen)"), "mark")
