"""Gmail over IMAP/SMTP with an app password. Works for any IMAP/SMTP server via `imap`/`smtp` config keys."""
import base64
import copy
import email
import email.policy
import functools
import getpass
import hashlib
import http.server
import imaplib
import re
import secrets
import smtplib
import sys
import threading
import time
import urllib.parse
import webbrowser

from .common import die, http_json, post_form, secret_get, secret_set

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

def login(name, acct, rules=False):
    if rules:
        return _google_login(name, acct)
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


# --- rules: Gmail filters, through the Gmail API (IMAP can't see filters) -------

GOOGLE_SCOPE = "https://www.googleapis.com/auth/gmail.settings.basic https://www.googleapis.com/auth/gmail.labels"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_FIELDS = ("from", "to", "subject")
_gtokens = {}


def _google_client(name, acct):
    if acct.get("imap", "imap.gmail.com") != "imap.gmail.com":
        die("rules work on Gmail and Microsoft 365 only; other IMAP servers keep their filters out of reach")
    if not (acct.get("client_id") and acct.get("client_secret")):
        die(f"Gmail filters need a Google OAuth client: add client_id and client_secret to [accounts.{name}] "
            "(see README: Rules and filters)")
    return acct["client_id"], acct["client_secret"]


def _google_login(name, acct):
    """OAuth in the browser with a one-shot loopback server; Google's device flow doesn't allow Gmail scopes."""
    cid, secret = _google_client(name, acct)
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(16)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    got = {}

    class Catch(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got.update(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and go back to the terminal.")

        def log_message(self, *args):
            pass

    with http.server.HTTPServer(("127.0.0.1", 0), Catch) as srv:
        redirect = f"http://127.0.0.1:{srv.server_port}"
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
            "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": GOOGLE_SCOPE,
            "access_type": "offline", "prompt": "consent", "login_hint": acct["email"], "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256"})
        print(f"Sign in to let Homer manage the filters of {acct['email']}:\n{url}", file=sys.stderr, flush=True)
        webbrowser.open(url)
        while "code" not in got and "error" not in got:  # skips the browser's /favicon.ico request
            srv.handle_request()
    if "code" not in got or got.get("state") != state:
        die(f"Google sign-in failed: {got.get('error', 'state mismatch')}")
    tok = post_form(GOOGLE_TOKEN, {"code": got["code"], "client_id": cid, "client_secret": secret,
                                   "redirect_uri": redirect, "grant_type": "authorization_code",
                                   "code_verifier": verifier})
    if "refresh_token" not in tok:
        die(f"Google sign-in failed: {tok.get('error_description', tok)}")
    secret_set(name, "refresh_token", tok["refresh_token"])
    _gtokens[name] = tok["access_token"]
    rules_list(name, acct)  # prove the token can read filters


def _api(name, acct, path, data=None, method=None):
    if name not in _gtokens:
        cid, secret = _google_client(name, acct)
        rt = secret_get(name, "refresh_token") or die(f"Gmail filters aren't linked yet; run: homer login -a {name} --rules")
        tok = post_form(GOOGLE_TOKEN, {"grant_type": "refresh_token", "refresh_token": rt,
                                       "client_id": cid, "client_secret": secret})
        _gtokens[name] = tok.get("access_token") or die(
            f"{tok.get('error_description', tok)}\nrun: homer login -a {name} --rules")
    return http_json(GMAIL_API + path, _gtokens[name], data, method)


def _label_ids(name, acct):
    return {lab["name"]: lab["id"] for lab in _api(name, acct, "/labels")["labels"]}


def _rule(f, label_name=lambda lid: lid):
    """Gmail filter -> Homer's rule dict (same shape as outlook._rule). Gmail filters have no names."""
    c, a = f.get("criteria", {}), f.get("action", {})
    add, rm = a.get("addLabelIds", []), a.get("removeLabelIds", [])
    labels = [lid for lid in add if lid.startswith("Label_")]  # user labels; system ones are upper case
    move = labels[0] if labels and "INBOX" in rm else None  # label + skip the inbox is Gmail's move
    return {"id": f["id"], "name": "", "enabled": True,
            **{k: c[k].split(" OR ") if c.get(k) else [] for k in _FIELDS},
            "has": c["query"].split(" OR ") if c.get("query") else [],
            "move": move and label_name(move), "label": [label_name(lid) for lid in labels if lid != move],
            "archive": "INBOX" in rm and not move, "read": "UNREAD" in rm, "delete": "TRASH" in add,
            "forward": [a["forward"]] if a.get("forward") else [],
            "other": any(v for k, v in c.items() if k not in (*_FIELDS, "query"))
            or bool(set(add) - set(labels) - {"TRASH"} or set(rm) - {"INBOX", "UNREAD"})}


def _native(changes, base=None, label_id=lambda name: name):
    """Apply Homer's rule changes (None = leave alone) to a Gmail filter, keeping everything Homer doesn't model."""
    f = copy.deepcopy(base or {})
    f.pop("id", None)
    c, a = f.setdefault("criteria", {}), f.setdefault("action", {})
    for k, field in (*((k, k) for k in _FIELDS), ("has", "query")):
        if changes.get(k) is not None:
            c[field] = " OR ".join(changes[k])
    add, rm = set(a.get("addLabelIds", [])), set(a.get("removeLabelIds", []))
    if changes.get("move"):
        add = {lid for lid in add if not lid.startswith("Label_")} | {label_id(changes["move"])}  # replaces the old folder
        rm.add("INBOX")
    if changes.get("archive"):
        rm.add("INBOX")
    for k, ids, lid in (("read", rm, "UNREAD"), ("delete", add, "TRASH")):
        if changes.get(k) is not None:
            (ids.add if changes[k] else ids.discard)(lid)
    if changes.get("forward") is not None:
        if len(changes["forward"]) > 1:
            die("a Gmail filter can forward to one address only")
        a["forward"] = changes["forward"][0] if changes["forward"] else None
    a["addLabelIds"], a["removeLabelIds"] = sorted(add), sorted(rm)
    f["criteria"] = {k: v for k, v in c.items() if v}
    f["action"] = {k: v for k, v in a.items() if v}
    return f


def _label_id(name, acct, label):
    return _label_ids(name, acct).get(label) or die(f"no Gmail label named {label!r}; make it with: homer mkdir -a {name} {label!r}")


def rules_list(name, acct):
    names = {lid: lname for lname, lid in _label_ids(name, acct).items()}
    return [_rule(f, lambda lid: names.get(lid, lid)) for f in _api(name, acct, "/settings/filters").get("filter", [])]


def add_rule(name, acct, changes):
    return _api(name, acct, "/settings/filters", _native(changes, label_id=lambda lab: _label_id(name, acct, lab)))["id"]


def edit_rule(name, acct, rid, changes):
    """The Gmail API can't update a filter, so this adds the edited copy, then deletes the old one. Returns the new id."""
    path = "/settings/filters/" + urllib.parse.quote(rid)
    new = _native(changes, _api(name, acct, path), lambda lab: _label_id(name, acct, lab))
    nid = _api(name, acct, "/settings/filters", new)["id"]
    _api(name, acct, path, method="DELETE")
    return nid


def rm_rule(name, acct, rid):
    _api(name, acct, "/settings/filters/" + urllib.parse.quote(rid), method="DELETE")
