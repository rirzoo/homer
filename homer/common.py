"""Shared bits: config, keyring secrets, and building/parsing messages."""
import email
import email.policy
import email.utils
import json
import mimetypes
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from email.message import EmailMessage
from html import escape
from html.parser import HTMLParser
from pathlib import Path

CONFIG = Path(os.environ.get("HOMER_CONFIG", "~/.config/homer/accounts.toml")).expanduser()
PRIVATE = (Path("~/.cache/homer").expanduser(), Path("~/.config/homer").expanduser())


def die(msg):
    sys.exit(f"homer: {msg}")


def private_dirs():
    """Only you can open homer's cache and config folders (S3), so what's inside needs no mode of its own.
    chmod as well, since mkdir leaves a folder that already exists as it was."""
    for d in PRIVATE:
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        d.chmod(0o700)


# --- config & secrets -------------------------------------------------------

def load_config():
    try:
        return tomllib.loads(CONFIG.read_text())
    except FileNotFoundError:
        die(f"no config at {CONFIG} (see README: Setup)")


def get_account(cfg, name):
    name = name or cfg.get("default") or die("no -a given and no default account set")
    acct = cfg.get("accounts", {}).get(name) or die(f"unknown account {name!r}")
    return name, acct


# ponytail: secret-tool (libsecret) only, so Linux only; add a `password_cmd` config key for macOS/others
def _secret_tool(args, **kw):
    try:
        return subprocess.run(["secret-tool", *args], text=True, **kw)
    except FileNotFoundError:
        die("secret-tool not found; install libsecret (Fedora: libsecret, Debian/Ubuntu: libsecret-tools)")


def secret_get(name, kind):
    r = _secret_tool(["lookup", "service", "homer", "account", name, "kind", kind], capture_output=True)
    return r.stdout.strip() or None


def secret_set(name, kind, value):
    _secret_tool(["store", "--label", f"homer {name} {kind}", "service", "homer", "account", name, "kind", kind],
                 input=value, check=True)


# --- HTTP (OAuth token endpoints and the rules/filters APIs) -----------------

def post_form(url, data):
    """POST a form to an OAuth token endpoint; returns the JSON reply, error replies included."""
    try:
        with urllib.request.urlopen(url, urllib.parse.urlencode(data).encode(), timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def http_json(url, token, data=None, method=None):
    """JSON request with a bearer token; returns the parsed reply (None if empty). HTTP errors die with the server's text."""
    req = urllib.request.Request(url, None if data is None else json.dumps(data).encode(), method=method,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        die(f"{urllib.parse.urlsplit(url).hostname} {e.code}: {e.read().decode(errors='replace')[:400]}")
    return json.loads(body) if body else None


# --- messages ---------------------------------------------------------------

class _HTMLText(HTMLParser):
    BLOCK = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html):
    p = _HTMLText()
    p.feed(html)
    text = re.sub(r"[ \t\xa0]+", " ", "".join(p.out))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def part_text(part):
    try:
        return part.get_content()
    except (LookupError, UnicodeError):
        return (part.get_payload(decode=True) or b"").decode(errors="replace")


def body_text(msg):
    part = msg.get_body(("plain", "html"))
    if part is None:
        return "(no text body)"
    text = part_text(part)
    return html_to_text(text) if part.get_content_type() == "text/html" else text


def attachments(msg):
    return [p for p in msg.walk() if not p.is_multipart() and p.get_filename()
            and p.get_content_type() != "application/pgp-signature"]  # a signature shows as a Signature: line instead


def save_attachments(parts, directory):
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    saved = []
    for p in parts:
        base = Path(Path(p.get_filename()).name or "attachment")  # .name strips ../ tricks
        dest, i = d / base, 1
        while dest.exists():  # never overwrite existing files
            dest, i = d / f"{base.stem}-{i}{base.suffix}", i + 1
        dest.write_bytes(p.get_payload(decode=True) or b"")
        saved.append(dest)
    return saved


def build_message(acct, to, cc, bcc, subject, body, attach, html=False, plain=None):
    msg = EmailMessage()
    msg["From"] = acct["email"]
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:  # Graph reads recipients from headers; smtplib strips Bcc before transmitting
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=acct["email"].rpartition("@")[2])
    # plain: hand-made text copy for HTML mail; otherwise derived. ponytail: derived copy drops link URLs
    msg.set_content((plain or html_to_text(body)) if html else body)
    if html:
        msg.add_alternative(body, subtype="html")
    for f in attach:
        p = Path(f)
        maintype, subtype = (mimetypes.guess_type(p)[0] or "application/octet-stream").split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
    return msg


def _addrs(*headers):
    return [a for h in headers if h is not None for a in h.addresses]


def _quote_html(msg):
    part = msg.get_body(("html",))
    if part is None:
        return escape(body_text(msg)).replace("\n", "<br>\n")
    page = part_text(part)
    inner = re.search(r"<body[^>]*>(.*)</body>", page, re.S | re.I)
    return inner.group(1) if inner else page


def _cid_images(orig):
    """Images the original's HTML shows via cid: links."""
    if orig.get_body(("html",)) is None:
        return []
    return [p for p in orig.walk() if p["Content-ID"] and p.get_content_maintype() == "image"]


def _keep_images(msg, images):
    """Put `images` next to msg's HTML part (multipart/related) so the quoted cid: links still resolve."""
    page = msg.get_body(("html",))
    for p in images:
        page.add_related(p.get_payload(decode=True) or b"", *p.get_content_type().split("/", 1),
                         cid=p["Content-ID"], disposition="inline", filename=p.get_filename())


def build_reply(acct, orig, body, reply_all=False, attach=(), html=False, plain=None):
    """Reply to `orig` (a parsed message): quotes it and sets the threading headers.
    plain: your text for the plain-text copy of an HTML reply (default: derived from body)."""
    me = acct["email"].lower()
    seen, to, cc = {me}, [], []
    for bucket, addrs in ((to, _addrs(orig["Reply-To"] or orig["From"])),
                          (cc, _addrs(orig["To"], orig["Cc"]) if reply_all else [])):
        for a in addrs:
            if a.addr_spec.lower() not in seen:
                seen.add(a.addr_spec.lower())
                bucket.append(str(a))
    if not to:  # replying to your own sent mail: answer its original recipients
        to, cc = cc or [str(a) for a in _addrs(orig["To"])], []
    subject = orig["Subject"] or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}".rstrip()
    said = f"On {orig['Date']}, {orig['From']} wrote:"
    quoted = "\n".join("> " + line if line else ">" for line in body_text(orig).splitlines())
    text = f"{(plain if plain is not None else html_to_text(body) if html else body).rstrip()}\n\n{said}\n{quoted}\n"
    if html:  # HTML part quotes the original's HTML; the plain copy keeps "> " quoting
        page = (f'{body}\n<p>{escape(said)}</p>\n<blockquote style="margin:0 0 0 .8ex;border-left:1px solid #ccc;'
                f'padding-left:1ex">{_quote_html(orig)}</blockquote>')
        msg = build_message(acct, to, cc, [], subject, page, attach, html=True, plain=text)
        _keep_images(msg, _cid_images(orig))
    else:
        msg = build_message(acct, to, cc, [], subject, text, attach)
    if orig["Message-ID"]:
        msg["In-Reply-To"] = orig["Message-ID"]
        msg["References"] = " ".join(filter(None, [orig["References"], orig["Message-ID"]]))
    return msg


def build_forward(acct, orig, to, cc, bcc, body="", attach=(), html=False, attached=False, plain=None):
    """Forward `orig` inline (your note, its headers, body and attachments), or with attached=True as an .eml."""
    subject = orig["Subject"] or ""
    if not subject.lower().startswith(("fwd:", "fw:")):
        subject = f"Fwd: {subject}".rstrip()
    if attached:  # the whole original rides along: headers, HTML, images, attachments
        msg = build_message(acct, to, cc, bcc, subject, body, attach, html)
        name = re.sub(r'[\\/:*?"<>|\s]+', " ", orig["Subject"] or "").strip() or "message"
        msg.add_attachment(orig, filename=f"{name}.eml")
        return msg
    head = [f"{h}: {orig[h]}" for h in ("From", "Date", "Subject", "To", "Cc") if orig[h]]
    note = (plain if plain is not None else html_to_text(body) if html else body).rstrip()
    text = f"{note}\n\n---------- Forwarded message ---------\n" + "\n".join(head) + f"\n\n{body_text(orig)}\n"
    text = text.lstrip("\n")
    images = []
    if html:
        page = (f'{body}\n<p>---------- Forwarded message ---------<br>\n'
                + "<br>\n".join(escape(h) for h in head) + f"</p>\n{_quote_html(orig)}")
        msg = build_message(acct, to, cc, bcc, subject, page, attach, html=True, plain=text)
        images = _cid_images(orig)
        _keep_images(msg, images)
    else:
        msg = build_message(acct, to, cc, bcc, subject, text, attach)
    for p in attachments(orig):
        if p not in images:  # images shown inline already travel with the HTML
            maintype, subtype = p.get_content_type().split("/", 1)
            msg.add_attachment(p.get_payload(decode=True) or b"", maintype=maintype, subtype=subtype,
                               filename=p.get_filename())
    return msg


# --- OpenPGP signatures (PGP/MIME, RFC 3156, through the gpg command) --------

def _gpg(args, data):
    """Run gpg with data on stdin; None if gpg isn't installed."""
    env = dict(os.environ)
    if "GPG_TTY" not in env and sys.stderr.isatty():
        env["GPG_TTY"] = os.ttyname(2)  # lets a terminal pinentry ask for the key's passphrase
    try:
        return subprocess.run(["gpg", "--batch", *args], input=data, capture_output=True, env=env)
    except FileNotFoundError:
        return None


def sign(acct, msg):
    """msg's content becomes the first part of a multipart/signed and gpg's detached signature the second, made with
    acct's pgp_key, or whichever key gpg has for acct's address. The signed bytes are written out by hand here and
    Python's generator leaves multipart/signed parts as they are, so sending doesn't change them."""
    inner = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
    for part in inner.walk():  # a relay may re-encode 8bit text on the way, which would break the signature
        if not part.is_multipart() and part.get("Content-Transfer-Encoding", "").lower() == "8bit":
            part.set_content(part.get_content(), subtype=part.get_content_subtype(), cte="quoted-printable")
    for h in set(inner.keys()):
        if not h.lower().startswith("content-"):
            del inner[h]
    data = inner.as_bytes(policy=email.policy.SMTP)
    key = acct.get("pgp_key") or acct["email"]
    r = _gpg(["--armor", "--detach-sign", "--digest-algo", "SHA256", "-u", key], data)
    if r is None:
        die("signing needs gpg; install GnuPG (Fedora: gnupg2, Debian/Ubuntu: gnupg)")
    if r.returncode:
        err = r.stderr.decode(errors="replace").strip()
        hint = f'\nmake one with: gpg --quick-gen-key "Your Name <{acct["email"]}>"' if "No secret key" in err \
            or "skipped" in err else ""
        die(f"gpg couldn't sign with {key}: {err}{hint}")
    b = b"homer-" + secrets.token_hex(12).encode()
    head = b"".join(email.policy.SMTP.fold_binary(k, v) for k, v in msg.items()
                    if not k.lower().startswith("content-") and k.lower() != "mime-version")
    sig = b"\r\n".join(r.stdout.strip().splitlines())
    raw = (head + b'MIME-Version: 1.0\r\nContent-Type: multipart/signed; micalg=pgp-sha256;\r\n'
           b' protocol="application/pgp-signature"; boundary="' + b + b'"\r\n\r\n'
           b"--" + b + b"\r\n" + data + b"\r\n--" + b + b"\r\n"
           b'Content-Type: application/pgp-signature; name="signature.asc"\r\n'
           b'Content-Disposition: attachment; filename="signature.asc"\r\n\r\n'
           + sig + b"\r\n--" + b + b"--\r\n")
    return email.message_from_bytes(raw, policy=email.policy.default)


def signature(raw, msg=None):
    """What gpg says about a PGP/MIME signature, in words; None if the mail isn't signed that way.
    ponytail: top-level multipart/signed only; inline (BEGIN PGP SIGNED MESSAGE) and nested signatures go unchecked.
    Homer never looks keys up on a keyserver, since that tells it which mail you're reading."""
    msg = msg or email.message_from_bytes(raw, policy=email.policy.default)
    if msg.get_content_type() != "multipart/signed" or \
            str(msg.get_param("protocol", "")).lower() != "application/pgp-signature":
        return None
    raw = re.sub(rb"\r?\n", b"\r\n", raw)
    b = b"\r\n--" + (msg.get_boundary() or "").encode()
    start = raw.find(b + b"\r\n")
    end = raw.find(b, start + len(b))
    parts = msg.get_payload()
    if start < 0 or end < 0 or len(parts) != 2:
        return "can't check: the signed mail is malformed"
    with tempfile.NamedTemporaryFile(suffix=".asc") as f:
        f.write(parts[1].get_payload(decode=True) or b"")
        f.flush()
        r = _gpg(["--status-fd", "1", "--verify", f.name, "-"], raw[start + len(b) + 2:end])
    if r is None:
        return "can't check: gpg isn't installed"
    st = {}
    for line in r.stdout.decode(errors="replace").splitlines():
        if line.startswith("[GNUPG:] "):
            kw, _, rest = line[9:].partition(" ")
            st[kw] = rest
    uid = lambda kw: re.sub(r"[\x00-\x1f\x7f-\x9f]", "", st[kw].partition(" ")[2])  # whoever made the key wrote it
    if "GOODSIG" in st:
        trusted = "TRUST_FULLY" in st or "TRUST_ULTIMATE" in st
        return f"good signature from {uid('GOODSIG')}" + ("" if trusted else ", key not verified")
    if "BADSIG" in st:
        return f"BAD signature from {uid('BADSIG')}: the mail was changed after it was signed"
    if "EXPKEYSIG" in st:
        return f"good signature from {uid('EXPKEYSIG')}, but the key has expired"
    if "REVKEYSIG" in st:
        return f"signed by {uid('REVKEYSIG')} with a key they have revoked"
    if "NO_PUBKEY" in st:
        frm = msg["from"].addresses[0].addr_spec if msg["from"] is not None and msg["from"].addresses else "ADDRESS"
        return f"signed with key {st['NO_PUBKEY']}, which isn't in your keyring (gpg --locate-keys {frm})"
    return "can't check: " + (r.stderr.decode(errors="replace").strip().splitlines() or ["gpg failed"])[-1]


# --- search -----------------------------------------------------------------

SEARCH_KEYS = ("from", "to", "subject", "is", "has", "before", "after")


def parse_query(q):
    """Split homer's search syntax (from: to: subject: is:unread|read has:attachment before:/after:YYYY-MM-DD,
    plus free words) into a dict for the backends to translate. Raises ValueError for a known filter with a
    value it can't use (before:yesterdy); anything else is searched as words."""
    try:
        parts = shlex.split(q)  # from:"Kavitha Das" stays one part
    except ValueError:
        raise ValueError("unbalanced quote") from None
    out = {"from": [], "to": [], "subject": [], "words": [], "unread": None, "attachment": False,
           "before": None, "after": None}
    for part in parts:
        key, sep, value = part.partition(":")
        key = key.lower()
        if not sep or key not in SEARCH_KEYS:  # plain words, and things like urls or 10:30
            out["words"].append(part)
        elif not value:
            raise ValueError(f"{key}: needs a value")
        elif key in ("from", "to", "subject"):
            out[key].append(value)
        elif key == "is" and value.lower() in ("unread", "read"):
            out["unread"] = value.lower() == "unread"
        elif key == "has" and value.lower() in ("attachment", "attachments"):
            out["attachment"] = True
        elif key in ("before", "after"):
            try:
                out[key] = date.fromisoformat(value)
            except ValueError:
                raise ValueError(f"{key}:{value} is not a date like 2026-10-01") from None
        else:
            raise ValueError(f"{key}:{value} isn't a filter homer knows")
    return out
