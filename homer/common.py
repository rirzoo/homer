"""Shared bits: config, keyring secrets, and building/parsing messages."""
import email.utils
import mimetypes
import os
import re
import subprocess
import sys
import tomllib
from email.message import EmailMessage
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


def build_message(acct, to, cc, bcc, subject, body, attach, html=False):
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
    msg.set_content(html_to_text(body) if html else body)  # ponytail: plain fallback drops link URLs
    if html:
        msg.add_alternative(body, subtype="html")
    for f in attach:
        p = Path(f)
        maintype, subtype = (mimetypes.guess_type(p)[0] or "application/octet-stream").split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
    return msg
