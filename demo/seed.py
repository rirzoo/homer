"""Fill the GreenMail demo server with made-up mail and write a throwaway HOME that points homer at it.
Every person, company and address here is invented, on .example domains. Usage: python3 seed.py DEMO_HOME"""
import imaplib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime.now(IST).replace(second=0, microsecond=0)
WORK, HOME_ = "Maya Rao <maya@lumenlabs.example>", "Maya Rao <maya.rao@example.com>"
PRIYA, DANIEL, SAM = ("Priya Natarajan <priya@lumenlabs.example>", "Daniel Okafor <daniel@lumenlabs.example>",
                      "Sam Lee <sam@lumenlabs.example>")
ARJUN = "Arjun Mehta <arjun.mehta@example.org>"
PDF = b"%PDF-1.4\n" + b"%" * 186_000 + b"\n%%EOF\n"  # just a size to show

NEWSLETTER = """<html><body style="font-family:sans-serif">
<img src="https://news.example.net/t/open.gif" width="1" height="1">
<h1>The Weekly Byte</h1><p>Five things worth reading this week.</p>
<ol>
<li><a href="https://news.example.net/r/1">SQLite is faster than you think</a></li>
<li><a href="https://news.example.net/r/2">A field guide to terminal colours</a></li>
<li><a href="https://news.example.net/r/3">Why your cron job ran twice</a></li>
<li><a href="https://news.example.net/r/4">Rust 2027 edition: what changes</a></li>
<li><a href="https://news.example.net/r/5">The quiet return of plain-text email</a></li>
</ol>
<p><a href="https://news.example.net/unsub">Unsubscribe</a> · <a href="https://news.example.net/prefs">Preferences</a></p>
</body></html>"""
NEWSLETTER_TEXT = """[image: The Weekly Byte] ( https://news.example.net/t/1 )
Five things worth reading this week.
1. SQLite is faster than you think ( https://news.example.net/r/1?utm_source=mail&utm_id=88123 )
2. A field guide to terminal colours ( https://news.example.net/r/2?utm_source=mail&utm_id=88123 )
3. Why your cron job ran twice ( https://news.example.net/r/3?utm_source=mail&utm_id=88123 )
4. Rust 2027 edition: what changes ( https://news.example.net/r/4?utm_source=mail&utm_id=88123 )
5. The quiet return of plain-text email ( https://news.example.net/r/5?utm_source=mail&utm_id=88123 )
[image: spacer]
Unsubscribe ( https://news.example.net/unsub ) | Preferences ( https://news.example.net/prefs )"""

# (account, folder, hours ago, from, to, subject, body, msgid, reply-to msgid, read, attachment name)
MAIL = [
    # work: a design review thread with a reply from Maya
    ("work", "INBOX", 30, PRIYA, WORK, "Design review moved to Thursday",
     "Hi Maya,\n\nThe design review for the onboarding flow is moving to Thursday at 3 pm, "
     "since half the team is out tomorrow. Can you bring the updated mockups?\n\nThanks,\nPriya",
     "review1", None, True, None),
    ("work", "Sent", 29, WORK, PRIYA, "Re: Design review moved to Thursday",
     "Thursday works. I'll have the new mockups and the empty-state screens ready.\n\nMaya",
     "review2", "review1", True, None),
    ("work", "INBOX", 2, PRIYA, WORK, "Re: Design review moved to Thursday",
     "Perfect. Could you also add the error screens? Legal wants to see the wording before we ship.\n\nPriya",
     "review3", "review2", False, None),
    ("work", "INBOX", 1, "Lumen CI <ci@lumenlabs.example>", WORK, "Build #2187 failed on main",
     "Build #2187 failed on main.\n\nCommit: 4f2c9e1 Bump search index version\nFailed step: test (3 of 412 failed)\n\n"
     "  test_search_ranking ......... FAILED\n  test_index_migration ........ FAILED\n  test_cache_warmup ........... FAILED\n\n"
     "Logs: https://ci.lumenlabs.example/builds/2187", "ci1", None, False, None),
    ("work", "INBOX", 5, DANIEL, WORK, "Q4 roadmap draft",
     "Hi all,\n\nAttached is the first draft of the Q4 roadmap. The big items are search, the billing "
     "migration and the mobile beta. Comments by Friday, please.\n\nDaniel", "road1", None, False, "roadmap-q4.pdf"),
    ("work", "INBOX", 26, "Northwind Hosting <billing@northwind.example>", WORK,
     "Invoice INV-2041 for October",
     "Hello,\n\nYour invoice INV-2041 for October is attached. Amount due: $1,240.00, by November 15.\n\n"
     "Northwind Hosting", "inv1", None, True, "INV-2041.pdf"),
    ("work", "INBOX", 50, SAM, WORK, "Lunch Friday?",
     "There's a new ramen place two blocks down. Friday at 1?\n\nSam", "lunch1", None, True, None),
    ("work", "Sent", 49, WORK, SAM, "Re: Lunch Friday?", "Yes! Count me in.", "lunch2", "lunch1", True, None),
    ("work", "INBOX", 48, SAM, WORK, "Re: Lunch Friday?", "Booked a table for four.", "lunch3", "lunch2", True, None),
    ("work", "INBOX", 72, "People Team <people@lumenlabs.example>", WORK, "Holiday calendar for 2027",
     "The 2027 holiday calendar is up on the wiki. Remember to block your days off in the team calendar.\n\n"
     "People Team", "hol1", None, True, None),
    ("work", "INBOX", 8, "DevConf <cfp@devconf.example>", WORK, "Your talk was accepted",
     "Congratulations! Your talk \"Plain text, fast search\" was accepted for DevConf 2027. "
     "Please confirm by November 5.\n\nDevConf program committee", "conf1", None, False, None),
    ("work", "INBOX", 23, DANIEL, WORK, "1:1 agenda",
     "For tomorrow: the search hiring plan, your conference talk, and the mobile beta timeline.", "one1", None,
     True, None),
    ("work", "INBOX", 33, "IT Help <it@lumenlabs.example>", WORK, "Security training due Nov 1",
     "A reminder that the yearly security training is due by November 1.", "sec1", None, True, None),
    ("work", "INBOX", 44, PRIYA, WORK, "Standup notes, Oct 21",
     "Search: ranking fix in review.\nBilling: migration dry run on Friday.\nMobile: beta build 0.9.3 is out.",
     "stand1", None, True, None),
    ("work", "INBOX", 60, "Expenses <expenses@lumenlabs.example>", WORK, "Expense report approved",
     "Your expense report EXP-311 ($86.40) was approved.", "exp1", None, True, None),
    ("work", "Archive", 240,"People Team <people@lumenlabs.example>", WORK, "Welcome to Lumen Labs!",
     "Welcome aboard, Maya! Your laptop arrives Monday.", "welcome1", None, True, None),
    ("work", "Archive", 200, PRIYA, WORK, "Offsite photos", "Photos from the offsite are in the shared drive.",
     "offsite1", None, True, None),
    ("work", "Drafts", 4, WORK, DANIEL, "Re: Q4 roadmap draft",
     "Looks good overall. Two thoughts on the billing migration:", "draft1", "road1", True, None),
    ("work", "Trash", 100, "Webinars <events@saas.example>", WORK, "Your webinar seat is confirmed",
     "See you on Tuesday!", "web1", None, True, None),
    # personal
    ("personal", "INBOX", 3, "The Weekly Byte <hello@news.example.net>", HOME_, "Five things worth reading this week",
     None, "news1", None, False, None),
    ("personal", "INBOX", 20, ARJUN, HOME_, "Goa trip: dates?",
     "Hey! Are we still on for Goa? I'm thinking Dec 19 to 23. Flights are cheap if we book this week.", "goa1",
     None, True, None),
    ("personal", "Sent", 19, HOME_, ARJUN, "Re: Goa trip: dates?",
     "Dec 19 to 23 works for me. Booking tonight.", "goa2", "goa1", True, None),
    ("personal", "INBOX", 6, ARJUN, HOME_, "Re: Goa trip: dates?",
     "Booked! Sending the hotel options tomorrow.", "goa3", "goa2", False, None),
    ("personal", "INBOX", 30, "Harbor Bank <statements@harborbank.example>", HOME_,
     "Your October statement is ready", "Your October statement is attached.", "bank1", None, True,
     "statement-oct.pdf"),
    ("personal", "INBOX", 54, "Leela Iyer <leela.iyer@example.org>", HOME_, "Water shutoff on Saturday",
     "Hi Maya, the water will be off on Saturday from 10 to 2 for the tank repairs.\n\nLeela", "water1", None,
     True, None),
]
LOGIN = {"work": "maya@lumenlabs.example", "personal": "maya.rao@example.com"}


def build(frm, to, subject, body, msgid, parent, attach, when):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = frm, to, subject, format_datetime(when)
    m["Message-ID"] = f"<{msgid}@demo.example>"
    if parent:
        chain = [p for p in MAIL if p[7] == parent]
        refs = []
        while chain:
            refs.insert(0, f"<{chain[0][7]}@demo.example>")
            chain = [p for p in MAIL if p[7] == chain[0][8]]
        m["In-Reply-To"], m["References"] = refs[-1], " ".join(refs)
    if body is None:
        m.set_content(NEWSLETTER_TEXT)
        m.add_alternative(NEWSLETTER, subtype="html")
    else:
        m.set_content(body)
    if attach:
        m.add_attachment(PDF, maintype="application", subtype="pdf", filename=attach)
    return m


def seed():
    for acct, login in LOGIN.items():
        m = imaplib.IMAP4_SSL("127.0.0.1", 3993)
        m.login(login, "demo")
        for f in ("Sent", "Archive", "Drafts", "Trash"):
            m.create(f)
        for a, folder, hours, frm, to, subject, body, msgid, parent, read, attach in sorted(MAIL, key=lambda x: -x[2]):
            if a != acct:
                continue
            when = NOW - timedelta(hours=hours, minutes=hours * 7 % 53)
            msg = build(frm, to, subject, body, msgid, parent, attach, when)
            flags = " ".join(([r"\Seen"] if read else []) + ([r"\Draft"] if folder == "Drafts" else []))
            m.append(folder, f"({flags})", imaplib.Time2Internaldate(when.timestamp()), msg.as_bytes())
        m.logout()


def write_home(home):
    """Config and a pre-filled special-folder cache (GreenMail doesn't flag Sent/Drafts/Trash), and a fake
    secret-tool, so neither the real config nor the real keyring is touched."""
    cfg = home / ".config/homer"
    cfg.mkdir(parents=True, exist_ok=True)
    acct = 'imap = "127.0.0.1"\nimap_port = 3993\nsmtp = "127.0.0.1"\nsmtp_port = 2587\n'
    (cfg / "accounts.toml").write_text(f'default = "work"\n\n[accounts.work]\nemail = "{LOGIN["work"]}"\n{acct}\n'
                                       f'[accounts.personal]\nemail = "{LOGIN["personal"]}"\n{acct}')
    special = {r"\Inbox": "INBOX", r"\Sent": "Sent", r"\Drafts": "Drafts", r"\Trash": "Trash", r"\All": "Archive"}
    for name in LOGIN:
        d = home / ".cache/homer/tui" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "special.json").write_text(json.dumps(special))
    bindir = home / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "secret-tool").write_text('#!/bin/sh\n[ "$1" = lookup ] && echo demo\nexit 0\n')
    (bindir / "secret-tool").chmod(0o755)
    skill = home / ".claude/skills/homer-mail"  # a project skill for an agent started in DEMO_HOME (README example)
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(SKILL)


SKILL = """---
name: homer-mail
description: Send, draft, reply to, read or search email with the homer CLI. Use whenever I ask to email someone, send a file, answer a mail or check my mail.
---

Use `homer` for all mail. `homer --help` and `homer <command> --help` list the options.

- Accounts: `work` (the default, Lumen Labs mail) and `personal`. Pick one with `-a`.
- Pass the body on stdin with a quoted heredoc (`<<'EOF'`) so quotes and `$` survive.
- Answer a mail with `homer reply ID`, not `send`, so it stays in the same thread.
- Ask me before adding, changing or removing a rule with `homer rules`.
- Before sending, show me the recipient, subject and body and wait for my OK. If unsure, use `--draft`.
"""


if __name__ == "__main__":
    seed()
    write_home(Path(sys.argv[1]).resolve())
