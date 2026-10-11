"""Shared bits: config, keyring secrets, and building/parsing messages."""
import email.utils
import mimetypes
import os
import re
import shlex
import subprocess
import sys
import tomllib
from datetime import date
from email.message import EmailMessage
from html import escape
from html.parser import HTMLParser
from pathlib import Path

CONFIG = Path(os.environ.get("HOMER_CONFIG", "~/.config/homer/accounts.toml")).expanduser()


def die(msg):
    sys.exit(f"homer: {msg}")


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
    return [p for p in msg.walk() if not p.is_multipart() and p.get_filename()]


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
