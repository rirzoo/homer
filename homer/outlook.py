"""Microsoft 365 / Outlook (e.g. institute mail) via Microsoft Graph, OAuth2 device-code login."""
import base64
import copy
import hashlib
import http.client
import json
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from .common import die, http_json, post_form, secret_get, secret_set

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
    return post_form(url, {"client_id": acct["client_id"], **data})


def _scope(acct):
    return acct.get("scope", DEFAULT_SCOPE)


_tokens = {}  # account name (mail scope) or (name, scope) -> access token


def _token(name, acct, scope):
    """Access token for scope from the stored refresh token: (token, None), or (None, Microsoft's error text)."""
    rt = secret_get(name, "refresh_token") or die(f"not logged in; run: homer login -a {name}")
    tok = _oauth(acct, "token", {"grant_type": "refresh_token", "refresh_token": rt, "scope": scope})
    if "access_token" not in tok:
        return None, tok.get("error_description", tok)
    if tok.get("refresh_token"):
        secret_set(name, "refresh_token", tok["refresh_token"])
    return tok["access_token"], None


def access_token(name, acct):
    if name not in _tokens:
        tok, err = _token(name, acct, _scope(acct))
        _tokens[name] = tok or die(f"{err}\nrun: homer login -a {name}")
    return _tokens[name]


def login(name, acct, rules=False):
    scope = _scope(acct)
    if rules and ".default" not in scope:  # .default can't be mixed with named scopes; rules fall back without it
        scope += " " + RULES_SCOPE
    dc = _oauth(acct, "devicecode", {"scope": scope})
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
    _tokens.clear()
    _rules_apis.pop(name, None)
    _graph(name, acct, "/me/mailFolders/inbox?$select=id")  # prove the token can read mail
    if rules:
        rules_list(name, acct)  # and that it can read rules


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


# Graph message ids are ~150 chars, so `list` shows an 8-character hash of each and remembers the ids, the newest
# KEEP_IDS of them per account. A hash stays the same for the same mail, unlike a row number; a moved mail gets a new id.
KEEP_IDS = 2000


def _short(mid):
    return hashlib.sha1(mid.encode()).hexdigest()[:8]


def _known(name):
    try:
        ids = json.loads((ID_CACHE / f"{name}.json").read_text())
    except FileNotFoundError:
        return {}
    return ids if isinstance(ids, dict) else {}  # older Homer kept a list of row numbers


def _remember(name, mids):
    ids = _known(name)
    for mid in mids:
        ids.pop(_short(mid), None)
        ids[_short(mid)] = mid  # newest last, so the trim drops the oldest
    ID_CACHE.mkdir(parents=True, exist_ok=True)
    (ID_CACHE / f"{name}.json").write_text(json.dumps(dict(list(ids.items())[-KEEP_IDS:])))


def _resolve(name, ref):
    ids = _known(name)
    if ref in ids:
        return ids[ref]
    if len(ref) <= 16:
        die(f"no message {ref} in recent listings; run: homer list -a {name}")
    return ref


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
    """ids=True returns full Graph ids as refs and leaves the id cache alone (for the TUI)."""
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
        _remember(name, [m["id"] for m in msgs])
    rows = []
    for m in msgs:
        addr = (m.get("from") or {}).get("emailAddress", {})
        rows.append((m["id"] if ids else _short(m["id"]), not m["isRead"], datetime.fromisoformat(m["receivedDateTime"]),
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


def rmdir(name, acct, folder):
    """Moves the folder, mail and all, into Deleted Items, as Outlook does (Graph's DELETE skips straight past it).
    Returns a note on where it went."""
    fid = _folder_id(name, acct, folder)
    if fid in WELL_KNOWN:
        die(f"{folder!r} is one of Outlook's own folders and can't be deleted")
    _graph(name, acct, f"/me/mailFolders/{urllib.parse.quote(fid)}/move", {"destinationId": "deleteditems"})
    _folder_ids.pop((name, folder), None)
    return "; it's in Deleted Items until you empty that"


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


# --- inbox rules ----------------------------------------------------------------

RULES_SCOPE = "https://graph.microsoft.com/MailboxSettings.ReadWrite"
_rules_apis = {}  # account name -> (rules url, token, pascal)
_OL = {"from": "senderContains", "to": "recipientContains", "subject": "subjectContains", "has": "bodyOrSubjectContains"}
_OL_ADDR = {"from": "fromAddresses", "to": "sentToAddresses"}  # exact-address forms Outlook's own rule editor makes
_OL_ACTIONS = {"moveToFolder", "copyToFolder", "markAsRead", "delete", "forwardTo", "stopProcessingRules"}


def _recase(obj, first):
    """Recase every key's first letter: Outlook REST spells Graph's messageRule fields in PascalCase."""
    if isinstance(obj, dict):
        return {first(k[:1]) + k[1:]: _recase(v, first) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_recase(v, first) for v in obj]
    return obj


def _rules_api(name, acct):
    """Graph needs MailboxSettings.ReadWrite for rules. Apps that can't get it (Microsoft's own clients only get
    their preauthorized Graph scopes) can still reach rules through Outlook REST on the same sign-in."""
    if name not in _rules_apis:
        tok, err = _token(name, acct, RULES_SCOPE)
        if tok:
            _rules_apis[name] = (f"https://{GRAPH_HOST}{GRAPH}/me/mailFolders/inbox/messageRules", tok, False)
        else:
            # ponytail: Outlook REST v2 is deprecated; if Microsoft turns it off, these accounts lose rules until
            # their app gets MailboxSettings.ReadWrite on Graph
            tok, _ = _token(name, acct, "https://outlook.office365.com/.default")
            if not tok:
                die(f"{err}\nrules need the MailboxSettings.ReadWrite permission: add it to your app, "
                    f"then run: homer login -a {name} --rules")
            _rules_apis[name] = ("https://outlook.office365.com/api/v2.0/me/MailFolders/Inbox/MessageRules", tok, True)
    return _rules_apis[name]


def _rules_call(name, acct, rid="", data=None, method=None):
    url, tok, pascal = _rules_api(name, acct)
    if rid:
        url += "/" + urllib.parse.quote(rid, safe="=")
    out = http_json(url, tok, _recase(data, str.upper) if pascal else data, method)
    return _recase(out, str.lower) if pascal else out


def _real_folder_id(name, acct, folder):
    fid = _folder_id(name, acct, folder)  # well-known names work in paths but not inside a rule
    return json.loads(_graph(name, acct, f"/me/mailFolders/{fid}?$select=id"))["id"] if fid in WELL_KNOWN else fid


def _folder_display(name, acct, fid):
    try:
        return json.loads(_graph(name, acct, f"/me/mailFolders/{urllib.parse.quote(fid)}?$select=displayName"))[
            "displayName"]
    except SystemExit:  # the folder was deleted after the rule was made
        return fid


def _rule(r, folder_name=lambda fid: fid):
    """Outlook messageRule -> Homer's rule dict (same shape as gmail._rule)."""
    c, a = r.get("conditions") or {}, r.get("actions") or {}
    out = {"id": r["id"], "name": r.get("displayName", ""), "enabled": r.get("isEnabled", True), "archive": False,
           **{k: list(c.get(f) or []) for k, f in _OL.items()}}
    for k, f in _OL_ADDR.items():
        out[k] += [x["emailAddress"]["address"] for x in c.get(f) or []]
    out["move"] = folder_name(a["moveToFolder"]) if a.get("moveToFolder") else None
    out["label"] = [folder_name(a["copyToFolder"])] if a.get("copyToFolder") else []  # a copy is Outlook's label
    out["read"], out["delete"] = bool(a.get("markAsRead")), bool(a.get("delete"))
    out["forward"] = [x["emailAddress"]["address"] for x in a.get("forwardTo") or []]
    known = set(_OL.values()) | set(_OL_ADDR.values())
    out["other"] = any(v for f, v in c.items() if f not in known) or any(v for f, v in a.items() if f not in _OL_ACTIONS)
    return out


def _native(changes, base=None, folder_id=lambda f: f):
    """Apply Homer's rule changes (None = leave alone) to a messageRule, keeping everything Homer doesn't model."""
    r = copy.deepcopy(base or {})
    c, a = r.setdefault("conditions", {}), r.setdefault("actions", {})
    if changes.get("name"):
        r["displayName"] = changes["name"]
    for k, f in _OL.items():
        if changes.get(k) is not None:
            c[f] = changes[k]
            c.pop(_OL_ADDR.get(k), None)
    dest = "archive" if changes.get("archive") else changes.get("move")
    if dest:
        a["moveToFolder"] = folder_id(dest)
    for k, f in (("read", "markAsRead"), ("delete", "delete")):
        if changes.get(k) is not None:
            a[f] = changes[k]
    if changes.get("forward") is not None:
        a["forwardTo"] = [{"emailAddress": {"address": x}} for x in changes["forward"]]
    return r


def rules_list(name, acct):
    return [_rule(r, lambda fid: _folder_display(name, acct, fid)) for r in _rules_call(name, acct)["value"]]


def add_rule(name, acct, changes):
    seq = max((r.get("sequence", 0) for r in _rules_call(name, acct)["value"]), default=0) + 1
    r = _native(changes, folder_id=lambda f: _real_folder_id(name, acct, f))
    r.setdefault("displayName", "Homer: " + ", ".join(v for k in _OL for v in changes.get(k) or [])[:60])
    return _rules_call(name, acct, data={**r, "sequence": seq, "isEnabled": True})["id"]


def edit_rule(name, acct, rid, changes):
    r = _native(changes, _rules_call(name, acct, rid), lambda f: _real_folder_id(name, acct, f))
    _rules_call(name, acct, rid, {k: r[k] for k in ("displayName", "conditions", "actions") if k in r}, "PATCH")
    return rid


def rm_rule(name, acct, rid):
    _rules_call(name, acct, rid, method="DELETE")
