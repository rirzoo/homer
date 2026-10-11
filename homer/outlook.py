"""Microsoft 365 / Outlook (e.g. institute mail) via Microsoft Graph, OAuth2 device-code login."""
import base64
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .common import die, secret_get, secret_set

GRAPH = "https://graph.microsoft.com/v1.0"
DEFAULT_SCOPE = ("https://graph.microsoft.com/Mail.ReadWrite "
                 "https://graph.microsoft.com/Mail.Send offline_access")
WELL_KNOWN = {"inbox", "sentitems", "drafts", "deleteditems", "junkemail", "archive", "outbox"}
ID_CACHE = Path("~/.cache/homer").expanduser()


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
    req = urllib.request.Request(GRAPH + path, data, {"Authorization": "Bearer " + access_token(name, acct),
                                                      "Content-Type": ctype}, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        die(f"graph {e.code}: {e.read().decode(errors='replace')[:400]}")


def _folder_id(name, acct, folder):
    if folder.lower() in WELL_KNOWN:
        return folder.lower()
    q = urllib.parse.urlencode({"$filter": f"displayName eq '{folder.replace(chr(39), chr(39) * 2)}'"})
    hits = json.loads(_graph(name, acct, f"/me/mailFolders?{q}"))["value"]
    return hits[0]["id"] if hits else die(f"no folder named {folder!r}")


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
    raw = base64.b64encode(msg.as_bytes())
    # ponytail: MIME upload caps the request at 4 MB; bigger needs a draft + upload session
    if len(raw) > 4 * 1024 * 1024:
        die("message too large for Graph MIME upload (4 MB limit, attachments included)")
    _graph(name, acct, path, raw, "text/plain")


# --- backend interface (same functions as gmail.py) -------------------------

def folders(name, acct):
    return [f["displayName"] for f in json.loads(_graph(name, acct, "/me/mailFolders?$top=100"))["value"]]


def list_messages(name, acct, folder, n, unread, search):
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
    ID_CACHE.mkdir(parents=True, exist_ok=True)
    (ID_CACHE / f"{name}.json").write_text(json.dumps([m["id"] for m in msgs]))
    rows = []
    for i, m in enumerate(msgs, 1):
        addr = (m.get("from") or {}).get("emailAddress", {})
        rows.append((str(i), not m["isRead"], datetime.fromisoformat(m["receivedDateTime"]),
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
    dest_id = _folder_id(name, acct, dest)
    for ref in refs:
        _graph(name, acct, _msg_path(name, ref) + "/move", {"destinationId": dest_id})


def mark(name, acct, folder, refs, read):
    for ref in refs:
        _graph(name, acct, _msg_path(name, ref), {"isRead": read}, method="PATCH")
