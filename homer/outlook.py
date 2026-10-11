"""Microsoft 365 / Outlook (e.g. institute mail) via Microsoft Graph, OAuth2 device-code login."""
import base64
import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .common import die, secret_get, secret_set

GRAPH_HOST, GRAPH = "graph.microsoft.com", "/v1.0"
DEFAULT_SCOPE = ("https://graph.microsoft.com/Mail.ReadWrite "
                 "https://graph.microsoft.com/Mail.Send offline_access")
WELL_KNOWN = {"inbox", "sentitems", "drafts", "deleteditems", "junkemail", "archive", "outbox"}
ID_CACHE = Path("~/.cache/homer").expanduser()
KEEP = 60  # seconds a Graph connection is reused; an older one is replaced rather than risk a stale socket
_conns = {}  # account name -> (keep-alive HTTPSConnection, last used), saves a TLS handshake per call
_locks = {}  # account name -> Lock; one request at a time per account, so threads can share a connection
_folder_ids = {}  # (account name, folder) -> Graph folder id


# --- OAuth2 -----------------------------------------------------------------

def _oauth(acct, endpoint, data):
    url = f"https://login.microsoftonline.com/{acct.get('tenant', 'organizations')}/oauth2/v2.0/{endpoint}"
    body = urllib.parse.urlencode({"client_id": acct["client_id"], **data}).encode()
    try:
        with urllib.request.urlopen(url, body) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def _scope(acct):
    return acct.get("scope", DEFAULT_SCOPE)


_tokens = {}


def access_token(name, acct):
    if name in _tokens:
        return _tokens[name]
    rt = secret_get(name, "refresh_token") or die(f"not logged in; run: homer login -a {name}")
    tok = _oauth(acct, "token", {"grant_type": "refresh_token", "refresh_token": rt, "scope": _scope(acct)})
    if "access_token" not in tok:
        die(f"{tok.get('error_description', tok)}\nrun: homer login -a {name}")
    if tok.get("refresh_token"):
        secret_set(name, "refresh_token", tok["refresh_token"])
    _tokens[name] = tok["access_token"]
    return _tokens[name]


def login(name, acct):
    dc = _oauth(acct, "devicecode", {"scope": _scope(acct)})
    if "device_code" not in dc:
        die(dc.get("error_description", dc))
    print(dc["message"], file=sys.stderr, flush=True)
    interval = dc.get("interval", 5)
    while True:
        time.sleep(interval)
        tok = _oauth(acct, "token", {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                     "device_code": dc["device_code"]})
        if "access_token" in tok:
            break
        if tok.get("error") == "slow_down":
            interval += 5
        elif tok.get("error") != "authorization_pending":
            die(tok.get("error_description", tok))
    secret_set(name, "refresh_token", tok["refresh_token"])
    _graph(name, acct, "/me/mailFolders/inbox?$select=id")  # prove the token can read mail


# --- Graph helpers ----------------------------------------------------------

def _graph(name, acct, path, data=None, ctype="application/json", method=None):
    if isinstance(data, dict):
        data = json.dumps(data).encode()
    method = method or ("GET" if data is None else "POST")
    with _locks.setdefault(name, threading.Lock()):
        for _ in range(3):
            c, used = _conns.pop(name, (None, 0))
            if c and time.monotonic() - used > KEEP:
                c.close()
                c = None
            reused = c is not None
            c = c or http.client.HTTPSConnection(GRAPH_HOST, timeout=60)
            try:
                c.request(method, GRAPH + path, data, {"Authorization": "Bearer " + access_token(name, acct),
                                                       "Content-Type": ctype})
                r = c.getresponse()
                body = r.read()
            except (http.client.HTTPException, OSError) as e:
                c.close()
                if reused and method != "POST":  # stale keep-alive socket; GET/PATCH are safe to resend, POST isn't
                    continue
                die(f"graph request failed: {e}")
            _conns[name] = (c, time.monotonic())
            if r.status == 401 and _tokens.pop(name, None):  # access token expired (they last ~1h): refresh, retry
                continue
            break
    if r.status >= 400:
        die(f"graph {r.status}: {body.decode(errors='replace')[:400]}")
    return body


def _folder_id(name, acct, folder):
    if folder.lower() in WELL_KNOWN:
        return folder.lower()
    if (name, folder) not in _folder_ids:
        q = urllib.parse.urlencode({"$filter": f"displayName eq '{folder.replace(chr(39), chr(39) * 2)}'"})
        hits = json.loads(_graph(name, acct, f"/me/mailFolders?{q}"))["value"]
        _folder_ids[name, folder] = hits[0]["id"] if hits else die(f"no folder named {folder!r}")
    return _folder_ids[name, folder]


# Graph message ids are ~150 chars, so `list` numbers rows 1..n and remembers the ids.
# ponytail: one cache per account, last `list` wins; pass the full id to reach older ones.
def _resolve(name, ref):
    if not ref.isdigit():
        return ref
    try:
        return json.loads((ID_CACHE / f"{name}.json").read_text())[int(ref) - 1]
    except (FileNotFoundError, IndexError):
        die(f"no message #{ref} in the last listing; run: homer list -a {name}")


def _msg_path(name, ref):
    return "/me/messages/" + urllib.parse.quote(_resolve(name, ref))


def _upload_mime(name, acct, path, msg):
    raw = base64.b64encode(msg.as_bytes(policy=msg.policy.clone(linesep="\r\n")))  # MIME wants CRLF; Exchange garbles soft breaks without it
    # ponytail: MIME upload caps the request at 4 MB; bigger needs a draft + upload session
    if len(raw) > 4 * 1024 * 1024:
        die("message too large for Graph MIME upload (4 MB limit, attachments included)")
    _graph(name, acct, path, raw, "text/plain")


# --- backend interface (same functions as gmail.py) -------------------------

def folders(name, acct):
    return [f["displayName"] for f in json.loads(_graph(name, acct, "/me/mailFolders?$top=100"))["value"]]


def list_messages(name, acct, folder, n, unread, search, ids=False):
    """ids=True returns full Graph ids as refs and leaves the row-number cache alone (for the TUI)."""
    q = {"$top": n, "$select": "id,subject,from,receivedDateTime,isRead"}
    if search:
        q["$search"] = f'"{search}"'  # KQL, e.g. from:prof subject:exam; Graph forbids $orderby/$filter with it
    else:
        q["$orderby"] = "receivedDateTime desc"
        if unread:  # Graph wants the $orderby field inside $filter too
            q["$filter"] = "receivedDateTime ge 1900-01-01T00:00:00Z and isRead eq false"
    path = f"/me/mailFolders/{_folder_id(name, acct, folder)}/messages?{urllib.parse.urlencode(q)}"
    msgs = json.loads(_graph(name, acct, path))["value"]
    if search and unread:
        msgs = [m for m in msgs if not m["isRead"]]
    if not ids:
        ID_CACHE.mkdir(parents=True, exist_ok=True)
        (ID_CACHE / f"{name}.json").write_text(json.dumps([m["id"] for m in msgs]))
    rows = []
    for i, m in enumerate(msgs, 1):
        addr = (m.get("from") or {}).get("emailAddress", {})
        rows.append((m["id"] if ids else str(i), not m["isRead"], datetime.fromisoformat(m["receivedDateTime"]),
                     addr.get("name") or addr.get("address") or "", m.get("subject") or ""))
    return rows


def fetch_raw(name, acct, folder, ref):
    return _graph(name, acct, _msg_path(name, ref) + "/$value")  # GET does not mark it read


def send(name, acct, msg, rcpts):
    _upload_mime(name, acct, "/me/sendMail", msg)


def save_draft(name, acct, msg):
    _upload_mime(name, acct, "/me/messages", msg)  # POSTing MIME to /me/messages creates a draft


def mkdir(name, acct, folder):
    _graph(name, acct, "/me/mailFolders", {"displayName": folder})  # ponytail: top-level only


def move(name, acct, folder, refs, dest):
    """Returns the messages' new ids; Graph gives a moved message a new id."""
    dest_id = _folder_id(name, acct, dest)
    return [json.loads(_graph(name, acct, _msg_path(name, ref) + "/move", {"destinationId": dest_id}))["id"]
            for ref in refs]


def mark(name, acct, folder, refs, read):
    for ref in refs:
        _graph(name, acct, _msg_path(name, ref), {"isRead": read}, method="PATCH")


# --- TUI extras ---------------------------------------------------------------

SPECIAL = {r"\Inbox": "inbox", r"\Sent": "sentitems", r"\All": "archive", r"\Drafts": "drafts",
           r"\Trash": "deleteditems", r"\Junk": "junkemail"}  # same keys as gmail.special_folders
_special = {}


def special_folders(name, acct):
    """Display names of the well-known folders, keyed like gmail.special_folders; Archive stands in for All Mail."""
    if name not in _special:
        out = {}
        for flag, wk in SPECIAL.items():
            try:
                out[flag] = json.loads(_graph(name, acct, f"/me/mailFolders/{wk}?$select=displayName"))["displayName"]
            except SystemExit:  # a mailbox without that folder (Archive is optional)
                pass
        _special[name] = out
    return _special[name]


def _kql(q):
    """homer search -> Outlook's KQL for $search. is:unread/read isn't searchable; summaries filters it."""
    quote = lambda v: '"' + v.replace('"', "") + '"' if " " in v else v.replace('"', "")
    out = [f"{k}:{quote(v)}" for k in ("from", "to", "subject") for v in q[k]]
    if q["attachment"]:
        out.append("hasAttachments:true")
    if q["before"]:
        out.append(f"received<{q['before']:%Y-%m-%d}")
    if q["after"]:
        out.append(f"received>={q['after']:%Y-%m-%d}")
    return " ".join(out + [quote(w) for w in q["words"]])


def summaries(name, acct, folder, n, query=None):
    """Newest n messages in folder as dicts for the TUI (folder None: every folder). query: parse_query() output."""
    q = {"$top": n, "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,isRead,"
                               "conversationId,hasAttachments,internetMessageId"}
    kql = _kql(query) if query else ""
    if kql:
        q["$search"] = '"' + kql.replace('"', '\\"') + '"'  # results come newest first
    else:
        q["$orderby"] = "receivedDateTime desc"
        if query and query["unread"] is not None:
            q["$filter"] = f"receivedDateTime ge 1900-01-01T00:00:00Z and isRead eq {str(not query['unread']).lower()}"
    base = f"/me/mailFolders/{_folder_id(name, acct, folder)}/messages" if folder else "/me/messages"
    msgs = json.loads(_graph(name, acct, f"{base}?{urllib.parse.urlencode(q)}"))["value"]
    if query and query["unread"] is not None:
        msgs = [m for m in msgs if m["isRead"] != query["unread"]]
    rows = []
    for m in msgs:
        frm = (m.get("from") or {}).get("emailAddress", {})
        rows.append({"id": m["id"], "folder": folder, "unread": not m["isRead"], "when": m["receivedDateTime"],
                     "name": frm.get("name", ""), "addr": frm.get("address", ""),
                     "to": [[r["emailAddress"].get("name", ""), r["emailAddress"].get("address", "")]
                            for r in m.get("toRecipients", []) + m.get("ccRecipients", [])],
                     "subject": m.get("subject") or "", "msgid": m.get("internetMessageId", ""),
                     "thread": m.get("conversationId", ""), "attach": m.get("hasAttachments", False), "labels": []})
    return rows
