"""Gmail over IMAP/SMTP with an app password. Works for any IMAP/SMTP server via `imap`/`smtp` config keys."""
import email
import email.policy
import getpass
import imaplib
import re
import smtplib
import sys
import time

from .common import die, secret_get, secret_set


def _password(name):
    return secret_get(name, "password") or die(f"no password stored; run: homer login -a {name}")


def _connect(name, acct):
    m = imaplib.IMAP4_SSL(acct.get("imap", "imap.gmail.com"), acct.get("imap_port", 993))
    m.login(acct["email"], _password(name))
    return m


def _select(m, folder, readonly=True):
    typ, data = m.select(f'"{folder}"', readonly=readonly)  # readonly: never flips \Seen
    if typ != "OK":
        die(f"cannot open folder {folder!r}: {data[0].decode(errors='replace')}")


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
    _connect(name, acct).logout()


def folders(name, acct):
    return [_folder_name(line) for line in _connect(name, acct).list()[1]]


def list_messages(name, acct, folder, n, unread, search):
    m = _connect(name, acct)
    _select(m, folder)
    typ, data = m.uid("SEARCH", None, " ".join(filter(None, ["UNSEEN" if unread else "", search])) or "ALL")
    if typ != "OK":
        die(f"search failed: {data}")
    uids = sorted(data[0].split(), key=int)[-n:]
    if not uids:
        return []
    typ, data = m.uid("FETCH", b",".join(uids), "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
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


def fetch_raw(name, acct, folder, ref):
    m = _connect(name, acct)
    _select(m, folder)
    typ, data = m.uid("FETCH", _uids([ref]), "(BODY.PEEK[])")
    return next((i[1] for i in data if isinstance(i, tuple)), None) or die(f"no message with UID {ref}")


def send(name, acct, msg, rcpts):
    with smtplib.SMTP(acct.get("smtp", "smtp.gmail.com"), acct.get("smtp_port", 587)) as s:
        s.starttls()
        s.login(acct["email"], _password(name))
        s.send_message(msg, to_addrs=rcpts)


def save_draft(name, acct, msg):
    m = _connect(name, acct)
    drafts = next((_folder_name(line) for line in m.list()[1]
                   if b"\\Drafts" in line.split(b")")[0]), "Drafts")  # RFC 6154 special-use flag
    _ok(m.append(f'"{drafts}"', r"(\Draft \Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes()),
        "save draft")


def mkdir(name, acct, folder):
    _ok(_connect(name, acct).create(f'"{folder}"'), "create folder")


def move(name, acct, folder, refs, dest):
    m = _connect(name, acct)
    _select(m, folder, readonly=False)
    _ok(m.uid("MOVE", _uids(refs), f'"{dest}"'), "move")


def mark(name, acct, folder, refs, read):
    m = _connect(name, acct)
    _select(m, folder, readonly=False)
    _ok(m.uid("STORE", _uids(refs), "+FLAGS" if read else "-FLAGS", r"(\Seen)"), "mark")
