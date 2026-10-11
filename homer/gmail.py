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


def _uid_set(text):
    """Expand an IMAP UID set like 5:7,9 into ['5', '6', '7', '9']."""
    out = []
    for part in text.split(","):
        lo, _, hi = part.partition(":")
        lo, hi = sorted((int(lo), int(hi or lo)))
        out += [str(u) for u in range(lo, hi + 1)]
    return out


@_session
def move(s, folder, refs, dest):
    """Returns the messages' UIDs in dest (from the server's COPYUID), None where it didn't say.
    Gmail: moving out of All Mail only adds the label, so undoing an archive is a plain move back."""
    _select(s, folder)
    s.m.response("COPYUID")  # drop a stale one
    _ok(s.m.uid("MOVE", _uids(refs), f'"{dest}"'), "move")
    pairs = {}
    for line in filter(None, s.m.response("COPYUID")[1]):
        _, src, dst = line.decode().split()
        pairs.update(zip(_uid_set(src), _uid_set(dst)))
    return [pairs.get(r) for r in refs]


@_session
def mark(s, folder, refs, read):
    _select(s, folder)
    _ok(s.m.uid("STORE", _uids(refs), "+FLAGS" if read else "-FLAGS", r"(\Seen)"), "mark")


# --- TUI extras ---------------------------------------------------------------

SPECIAL = (r"\Sent", r"\All", r"\Drafts", r"\Trash", r"\Junk", r"\Important", r"\Flagged")


@_session
def special_folders(s):
    r"""Special-use folders (RFC 6154) by flag, e.g. {'\Sent': '[Gmail]/Sent Mail'}, plus \Inbox."""
    out = {r"\Inbox": "INBOX"}
    for line in s.m.list()[1]:
        flags = line.split(b")")[0].decode(errors="replace").lstrip("(").split()
        for f in SPECIAL:
            if f in flags:
                out.setdefault(f, _folder_name(line))
    return out


def _quote(v):
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _gmail_raw(q):
    """homer search -> Gmail's own search syntax (what X-GM-RAW takes)."""
    out = [f"{k}:{_quote(v)}" for k in ("from", "to", "subject") for v in q[k]]
    if q["unread"] is not None:
        out.append("is:unread" if q["unread"] else "is:read")
    if q["attachment"]:
        out.append("has:attachment")
    out += [f"{k}:{q[k]:%Y/%m/%d}" for k in ("before", "after") if q[k]]
    return " ".join(out + [_quote(w) for w in q["words"]])


def _imap_search(q):
    """homer search -> plain IMAP SEARCH keys, for servers without Gmail's extensions (ASCII only, no has:)."""
    out = [f"{k.upper()} {_quote(v)}" for k in ("from", "to", "subject") for v in q[k]]
    if q["unread"] is not None:
        out.append("UNSEEN" if q["unread"] else "SEEN")
    if q["before"]:
        out.append(f"BEFORE {q['before']:%d-%b-%Y}")
    if q["after"]:
        out.append(f"SINCE {q['after']:%d-%b-%Y}")
    return " ".join(out + [f"TEXT {_quote(w)}" for w in q["words"]]) or "ALL"


def _labels(meta):
    hit = re.search(rb"X-GM-LABELS \((.*?)\)", meta)
    if not hit:
        return []
    return [q.decode().replace("\\\\", "\\") if q else bare.decode()
            for q, bare in re.findall(rb'"((?:[^"\\]|\\.)*)"|(\S+)', hit.group(1))]


def _summary(h, meta, folder):
    frm = h["from"].addresses[0] if h["from"] is not None and h["from"].addresses else None
    msgid = (h["message-id"] or "").strip()
    refs = (h["references"] or "").split() or (h["in-reply-to"] or "").split()
    thrid = re.search(rb"X-GM-THRID (\d+)", meta)
    when = h["date"].datetime if h["date"] is not None else None
    return {"id": re.search(rb"UID (\d+)", meta).group(1).decode(), "folder": folder,
            "unread": b"\\Seen" not in meta, "when": when.isoformat() if when else None,
            "name": frm.display_name if frm else "", "addr": frm.addr_spec if frm else "",
            "to": [[a.display_name, a.addr_spec] for x in (h["to"], h["cc"]) if x is not None for a in x.addresses],
            "subject": h["subject"] or "", "msgid": msgid,
            "thread": thrid.group(1).decode() if thrid else (refs[0] if refs else msgid),
            "attach": False, "labels": _labels(meta)}


@_session
def summaries(s, folder, n, query=None):
    """Newest n messages in folder as dicts for the TUI (folder None: All Mail). query: parse_query() output."""
    gm = "X-GM-EXT-1" in s.m.capabilities
    if folder is None:
        folder = next((_folder_name(line) for line in s.m.list()[1] if rb"\All" in line.split(b")")[0]), "INBOX")
    total = _select(s, folder, refresh=True)
    items = "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES)]"
    items += " X-GM-THRID X-GM-LABELS)" if gm else ")"
    if query:
        if gm:
            s.m.literal = _gmail_raw(query).encode()  # sent as a literal, so non-ASCII words work
            typ, data = s.m.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
        else:
            typ, data = s.m.uid("SEARCH", None, _imap_search(query))
        if typ != "OK":
            die(f"search failed: {data}")
        uids = sorted(data[0].split(), key=int)[-n:]
        if not uids:
            return []
        typ, data = s.m.uid("FETCH", b",".join(uids), items)
    else:
        if not total:
            return []
        typ, data = s.m.fetch(f"{max(1, total - n + 1)}:{total}", items)
    rows = []
    for i, item in enumerate(data):
        if not isinstance(item, tuple):
            continue
        tail = data[i + 1] if i + 1 < len(data) and isinstance(data[i + 1], bytes) else b""
        rows.append(_summary(email.message_from_bytes(item[1], policy=email.policy.default), item[0] + tail, folder))
    if gm and rows:  # one more round trip for the attachment flag; cheaper than parsing BODYSTRUCTURE
        span = f"{min(int(r['id']) for r in rows)}:{max(int(r['id']) for r in rows)}"
        typ, data = s.m.uid("SEARCH", None, "UID", span, "X-GM-RAW", '"has:attachment"')
        withatt = set(data[0].decode().split()) if typ == "OK" else set()
        for r in rows:
            r["attach"] = r["id"] in withatt
    return sorted(rows, key=lambda r: int(r["id"]), reverse=True)
