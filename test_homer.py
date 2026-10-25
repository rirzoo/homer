"""Offline self-check: python3 test_homer.py"""
import email
import email.policy
import tempfile
from pathlib import Path

from homer import common as homer

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    pdf = tmp / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake\x00\xff")

    msg = homer.build_message({"email": "me@example.com"}, ["a@x.com", "b@x.com"], ["c@x.com"], ["d@x.com"],
                              "Héllo", "body text\n", [pdf])
    parsed = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
    assert parsed["To"] == "a@x.com, b@x.com" and parsed["Subject"] == "Héllo"
    assert parsed["Bcc"] == "d@x.com"
    assert homer.body_text(parsed).strip() == "body text"

    parts = homer.attachments(parsed)
    assert [p.get_filename() for p in parts] == ["report.pdf"]
    assert parts[0].get_content_type() == "application/pdf"

    out = tmp / "out"
    first = homer.save_attachments(parts, out)
    second = homer.save_attachments(parts, out)  # must not overwrite
    assert first[0].read_bytes() == pdf.read_bytes()
    assert second[0].name == "report-1.pdf"

    # path traversal in a received filename stays inside the target dir
    evil = email.message_from_bytes(
        b'Content-Type: text/plain\r\nContent-Disposition: attachment; filename="../../evil.txt"\r\n\r\nx',
        policy=email.policy.default)
    assert homer.save_attachments([evil], out)[0].parent == out

    msg = homer.build_message({"email": "me@example.com"}, ["a@x.com"], [], [], "s",
                              '<p>Hi</p><p><a href="https://github.com/rirzoo/homer">homer</a></p>', [pdf], html=True)
    parsed = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
    assert 'href="https://github.com/rirzoo/homer"' in parsed.get_body(("html",)).get_content()
    assert parsed.get_body(("plain",)).get_content().strip() == "Hi\nhomer"
    assert [p.get_filename() for p in homer.attachments(parsed)] == ["report.pdf"]

assert homer.html_to_text("<style>x{}</style><p>Hi&nbsp;there</p><p>bye</p>") == "Hi there\nbye"
print("ok")

# reply: recipients, subject, quoting, threading headers
orig = email.message_from_bytes(
    b"From: Bob <bob@x.com>\r\nTo: me@example.com, carol@x.com\r\nCc: dave@x.com\r\nSubject: Plans\r\n"
    b"Date: Sun, 11 Oct 2026 10:00:00 +0530\r\nMessage-ID: <m2@x.com>\r\nReferences: <m1@x.com>\r\n\r\nSee you\r\n",
    policy=email.policy.default)
r = homer.build_reply({"email": "Me@Example.com"}, orig, "Sure")
assert (r["To"], r["Cc"], r["Subject"]) == ("Bob <bob@x.com>", None, "Re: Plans")
assert r["In-Reply-To"] == "<m2@x.com>" and r["References"] == "<m1@x.com> <m2@x.com>"
assert homer.body_text(r).startswith("Sure\n\nOn Sun, 11 Oct 2026") and "> See you" in homer.body_text(r)
ra = homer.build_reply({"email": "me@example.com"}, orig, "Sure", reply_all=True)
assert ra["Cc"] == "carol@x.com, dave@x.com"  # me excluded, no duplicates
mine = email.message_from_bytes(b"From: me@example.com\r\nTo: bob@x.com\r\nSubject: Re: Plans\r\n\r\nhi\r\n",
                                policy=email.policy.default)
rm = homer.build_reply({"email": "me@example.com"}, mine, "again")
assert (rm["To"], rm["Subject"]) == ("bob@x.com", "Re: Plans")  # follow-up to own mail goes to its recipients
print("reply ok")

# HTML reply: HTML part quotes the original's HTML body, plain copy keeps "> " quoting
horig = email.message_from_bytes(
    b"From: Bob <bob@x.com>\r\nTo: me@example.com\r\nSubject: Hi\r\nMessage-ID: <h1@x.com>\r\n"
    b"Content-Type: text/html\r\n\r\n<html><head><style>p{}</style></head><body><p>See <b>you</b></p></body></html>\r\n",
    policy=email.policy.default)
hr = homer.build_reply({"email": "me@example.com"}, horig, '<p>Sure, <a href="https://x.com">here</a></p>', html=True)
page = hr.get_body(("html",)).get_content()
assert '<a href="https://x.com">here</a>' in page and "<blockquote" in page and "<p>See <b>you</b></p>" in page
assert "<style>" not in page  # only the original's <body> is quoted
assert hr.get_body(("plain",)).get_content().startswith("Sure, here\n\nOn ") and "> See you" in hr.get_body(("plain",)).get_content()
assert hr["In-Reply-To"] == "<h1@x.com>"
pr = homer.build_reply({"email": "me@example.com"}, orig, "<p>ok</p>", html=True)  # plain-text original
assert "See you" in pr.get_body(("html",)).get_content()
print("html reply ok")

# forward: subject, header block, original body, original attachments carried over
fo = email.message_from_bytes(
    b"From: Bob <bob@x.com>\r\nTo: me@example.com\r\nSubject: Report\r\nDate: Sun, 11 Oct 2026 10:00:00 +0530\r\n"
    b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n--B\r\nContent-Type: text/plain\r\n\r\n"
    b"Numbers inside\r\n--B\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=q3.pdf\r\n"
    b"Content-Transfer-Encoding: base64\r\n\r\nJVBERi0=\r\n--B--\r\n", policy=email.policy.default)
me = {"email": "me@example.com"}
f = email.message_from_bytes(homer.build_forward(me, fo, ["carol@x.com"], [], [], "FYI").as_bytes(),
                             policy=email.policy.default)
assert (f["To"], f["Subject"], f["In-Reply-To"]) == ("carol@x.com", "Fwd: Report", None)
t = homer.body_text(f)
assert t.startswith("FYI\n\n---------- Forwarded message") and "From: Bob <bob@x.com>" in t and "Numbers inside" in t
assert [(p.get_filename(), p.get_payload(decode=True)) for p in homer.attachments(f)] == [("q3.pdf", b"%PDF-")]
assert homer.body_text(homer.build_forward(me, fo, ["c@x.com"], [], [])).startswith("----------")  # no note
assert homer.build_forward(me, f, ["c@x.com"], [], [])["Subject"] == "Fwd: Report"  # no "Fwd: Fwd:"
page = homer.build_forward(me, horig, ["c@x.com"], [], [], "<p>look</p>", html=True).get_body(("html",)).get_content()
assert "<p>See <b>you</b></p>" in page and "From: Bob &lt;bob@x.com&gt;" in page
print("forward ok")

# cid: images in an HTML original stay inline in HTML forwards and replies; other attachments stay attachments
img = email.message.EmailMessage()
img["From"], img["Subject"] = "Bob <bob@x.com>", "Pic"
img.set_content("see pic")
img.add_alternative('<html><body><p>Look <img src="cid:dot@x"></p></body></html>', subtype="html")
img.get_body(("html",)).add_related(b"\x89PNG", "image", "png", cid="<dot@x>", filename="dot.png")
img.add_attachment(b"%PDF-", maintype="application", subtype="pdf", filename="q3.pdf")
img = email.message_from_bytes(img.as_bytes(), policy=email.policy.default)
for out in (homer.build_forward(me, img, ["c@x.com"], [], [], "<p>fyi</p>", html=True),
            homer.build_reply(me, img, "<p>nice</p>", html=True)):
    out = email.message_from_bytes(out.as_bytes(), policy=email.policy.default)
    assert 'src="cid:dot@x"' in out.get_body(("html",)).get_content()
    rel = next(p for p in out.walk() if p["Content-ID"] == "<dot@x>")
    assert rel.get_payload(decode=True) == b"\x89PNG" and rel.get_content_disposition() == "inline"
assert [p.get_filename() for p in homer.attachments(out)] == ["dot.png"]  # reply: only the inline image
fw = homer.build_forward(me, img, ["c@x.com"], [], [], "<p>fyi</p>", html=True)
assert sorted(p.get_filename() for p in homer.attachments(fw)) == ["dot.png", "q3.pdf"]  # image once, not twice
plain = homer.build_forward(me, img, ["c@x.com"], [], [])  # text forward: images come as plain attachments
assert sorted(p.get_filename() for p in homer.attachments(plain)) == ["dot.png", "q3.pdf"]

# --attached: original goes whole as message/rfc822, note is the body
at = homer.build_forward(me, img, ["c@x.com"], [], [], "see attached", attached=True)
at = email.message_from_bytes(at.as_bytes(), policy=email.policy.default)
assert at["Subject"] == "Fwd: Pic" and homer.body_text(at).strip() == "see attached"
eml = next(p for p in at.walk() if p.get_content_type() == "message/rfc822")
assert eml.get_filename() == "Pic.eml"
inner = eml.get_content()
assert inner["From"] == "Bob <bob@x.com>" and "cid:dot@x" in inner.get_body(("html",)).get_content()
assert sorted(p.get_filename() for p in homer.attachments(inner)) == ["dot.png", "q3.pdf"]
assert homer.build_forward(me, fo, ["c@x.com"], [], [], attached=True).get_body(("plain",)) is not None  # no note
print("inline images + attached forward ok")

# backends: one cached connection per account, one call at a time, reconnect once when it drops
import imaplib  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from homer import gmail, outlook  # noqa: E402


class FakeIMAP:
    made = live = peak = 0

    def __init__(self):
        FakeIMAP.made += 1
        self.dead = False

    def list(self):
        FakeIMAP.live += 1
        FakeIMAP.peak = max(FakeIMAP.peak, FakeIMAP.live)
        time.sleep(0.02)
        FakeIMAP.live -= 1
        if self.dead:
            raise imaplib.IMAP4.abort("socket closed")
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"']

    def shutdown(self):
        pass


gmail._connect = lambda name, acct: FakeIMAP()
got = []
threads = [threading.Thread(target=lambda: got.append(gmail.folders("t", {}))) for _ in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()
assert got == [["INBOX"]] * 4 and FakeIMAP.peak == 1 and FakeIMAP.made == 1
gmail._sessions["t"].m.dead = True  # server dropped the connection
assert gmail.folders("t", {}) == ["INBOX"] and FakeIMAP.made == 2

calls = []


class FakeConn:
    made = 0

    def __init__(self, host, timeout):
        FakeConn.made += 1

    def request(self, method, path, data, headers):
        calls.append(headers["Authorization"])

    def getresponse(self):
        return type("R", (), {"status": 401 if len(calls) == 1 else 200, "read": lambda self: b"{}"})()

    def close(self):
        pass


outlook.http.client.HTTPSConnection = FakeConn
tokens = iter(["old", "new"])
outlook.access_token = lambda name, acct: outlook._tokens.get(name) or outlook._tokens.setdefault(name, next(tokens))
assert outlook._graph("t", {}, "/me") == b"{}" and calls == ["Bearer old", "Bearer new"]  # expired token refreshed
assert outlook._graph("t", {}, "/me") == b"{}" and FakeConn.made == 1  # keep-alive connection reused

with tempfile.TemporaryDirectory() as tmp:
    outlook.ID_CACHE = Path(tmp)
    outlook._graph = lambda *a, **k: json.dumps({"value": [
        {"id": "AAMk1", "isRead": False, "receivedDateTime": "2026-10-11T10:00:00Z", "subject": "s"}]}).encode()
    assert outlook.list_messages("t", {}, "inbox", 5, False, None, ids=True)[0][0] == "AAMk1"
    assert not any(Path(tmp).iterdir())  # ids=True leaves the agents' row numbers alone
    assert outlook.list_messages("t", {}, "inbox", 5, False, None)[0][0] == "1"
    assert json.loads((Path(tmp) / "t.json").read_text()) == ["AAMk1"]
print("backend sessions ok")

# TUI data side: search syntax, conversations, the catch-all Archive, folder map, footer, dates
import asyncio  # noqa: E402
import types  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from homer import store  # noqa: E402

q = homer.parse_query('from:"Kavitha Das" has:attachment is:unread after:2026-09-01 exam http://x.com/a')
assert q["from"] == ["Kavitha Das"] and q["attachment"] and q["unread"] and q["words"] == ["exam", "http://x.com/a"]
assert gmail._gmail_raw(q) == 'from:"Kavitha Das" is:unread has:attachment after:2026/09/01 "exam" "http://x.com/a"'
assert outlook._kql(q) == 'from:"Kavitha Das" hasAttachments:true received>=2026-09-01 exam http://x.com/a'
for bad in ("before:yesterdy", "is:starred", 'from:"open'):
    try:
        homer.parse_query(bad)
        raise AssertionError(bad)
    except ValueError:
        pass
assert gmail._uid_set("5:7,9") == ["5", "6", "7", "9"]


def row(i, folder, addr, thread, when, unread=False, msgid=None, to=("", "a@x.com"), labels=()):
    return {"id": str(i), "folder": folder, "unread": unread, "when": when, "name": "", "addr": addr,
            "to": [list(to)], "subject": f"s{thread}", "msgid": msgid or f"<{i}@x>", "thread": thread,
            "attach": False, "labels": list(labels)}


mine = {"me@x.com"}
msgs = [row(1, "INBOX", "a@x.com", "t1", "2026-10-11T10:00:00+05:30", unread=True),
        row(2, "Sent", "me@x.com", "t1", "2026-10-11T05:00:00+00:00"),  # 10:30 in India: after mail 1
        row(3, "INBOX", "me@x.com", "t2", "2026-10-10T10:00:00+00:00", msgid="<self@x>"),
        row(4, "Sent", "me@x.com", "t2", "2026-10-10T10:00:00+00:00", msgid="<self@x>"),
        row(5, "Sent", "me@x.com", "t3", "2026-10-09T10:00:00+00:00", to=("Amit", "p@x.com"))]
cs = store.conversations(msgs, mine)
assert [c["key"] for c in cs] == ["t1", "t2", "t3"] and cs[0]["unread"]
assert [m["id"] for m in cs[0]["msgs"]] == ["1", "2"]  # time zones compared, not strings
assert cs[1]["msgs"][0]["locs"] == [["INBOX", "3"], ["Sent", "4"]]  # mail to yourself: one mail, both places
assert cs[2]["who"] == "To Amit" and cs[2]["mine"]
assert len(store.conversations(msgs, mine, single=True)) == 4

with tempfile.TemporaryDirectory() as tmp:
    store.CACHE, store.FOLDERS_FILE = Path(tmp) / "cache", Path(tmp) / "folders.toml"
    special = {r"\Inbox": "INBOX", r"\Sent": "[Gmail]/Sent Mail", r"\All": "[Gmail]/All Mail",
               r"\Drafts": "[Gmail]/Drafts", r"\Trash": "[Gmail]/Bin"}
    allmail = [row(10, "[Gmail]/All Mail", "a@x.com", "t1", None, labels=[r"\Inbox"]),
               row(11, "[Gmail]/All Mail", "a@x.com", "t9", None),
               row(12, "[Gmail]/All Mail", "a@x.com", "t8", None, labels=["Travels/2026"]),
               row(13, "[Gmail]/All Mail", "me@x.com", "t7", None, labels=[r"\Draft"])]
    fake = types.SimpleNamespace(special_folders=lambda n, a: special,
                                 summaries=lambda n, a, f, k, q=None: allmail if f == "[Gmail]/All Mail" else [])
    acct = store.Account("g", {"email": "me@x.com"}, fake, store.load_maps())
    acct.load_special()
    assert acct.folders()["Mailbox"] == ["INBOX", "[Gmail]/Sent Mail"] and acct.target("Archive") == "[Gmail]/All Mail"
    acct.set_sources("Travels", ["Travels/2026"])
    assert [m["thread"] for m in acct.fetch("Archive", 50)] == ["t9"]  # inbox, own-folder and draft mail stay out
    assert store.load_maps() == {"g": {"Travels": ["Travels/2026"]}}  # the TOML homer writes reads back
    acct.rename("Travels", "Uni")
    assert acct.custom() == ["Uni"]
    acct.delete("Uni")
    assert acct.custom() == []
    acct.set_sources("Mailbox", ["INBOX"])
    acct.set_sources("Mailbox", [])  # nothing ticked: back to homer's default
    assert acct.folders()["Mailbox"] == ["INBOX", "[Gmail]/Sent Mail"]
    acct.set_sources("Feed", [])  # your own folder can stay, fed by nothing
    assert acct.folders()["Feed"] == [] and acct.fetch("Feed", 50) == []
    store.STATE_FILE = Path(tmp) / "tui.toml"
    store.save_state({"theme": "nord", "names": {"g": "Work \"mail\""}})
    assert store.load_state() == {"theme": "nord", "names": {"g": "Work \"mail\""}}

html, plain = store.with_footer("Hi <Bob>,\n\nsee https://x.com/a.\n")
assert html == '<p>Hi &lt;Bob&gt;,</p>\n<p>see <a href="https://x.com/a">https://x.com/a</a>.</p>\n' + store.FOOTER_HTML
assert plain.endswith("\n\nSent with homer (https://github.com/rirzoo/homer)")
assert store.strip_footer(plain) == "Hi <Bob>,\n\nsee https://x.com/a."
r = store.build_outgoing({"email": "me@example.com"}, "reply", orig, ["Bob <bob@x.com>"], [], ["z@x.com"],
                         "Re: Plans", "Sure", [])
text = r.get_body(("plain",)).get_content()
assert text.index("Sent with homer (") < text.index("wrote:")  # footer ends your text, quote below (docs/ISSUES.md I1)
assert store.FOOTER_HTML in r.get_body(("html",)).get_content() and r["Bcc"] == "z@x.com"
assert r["In-Reply-To"] == "<m2@x.com>" and r["To"] == "Bob <bob@x.com>"

now = datetime(2026, 10, 11, 6, 0, tzinfo=timezone.utc)
assert [store.short_when(now - d, now) for d in (timedelta(seconds=20), timedelta(minutes=5))] == ["now", "5m"]
assert store.short_when(datetime(2026, 3, 3, 6, 0, tzinfo=timezone.utc), now) == "3 Mar"
assert store.short_when(datetime(2025, 3, 3, 6, 0, tzinfo=timezone.utc), now) == "3 Mar 25"
print("tui data ok")

# rules: native <-> Homer's rule dict, and edits keep the parts Homer doesn't model
from homer import cli  # noqa: E402

blank = dict.fromkeys(cli.RULE_KEYS)
pascal = {"Id": "R1", "DisplayName": "tpc", "IsEnabled": True,
          "Conditions": {"FromAddresses": [{"EmailAddress": {"Address": "tpc@x.com"}}], "HasAttachments": True},
          "Actions": {"MoveToFolder": "F1", "StopProcessingRules": True}}
ol = outlook._recase(pascal, str.lower)
assert outlook._recase(ol, str.upper) == pascal and ol["conditions"]["fromAddresses"][0]["emailAddress"]["address"]
r = outlook._rule(ol, {"F1": "Placements"}.get)
assert (r["from"], r["move"], r["read"], r["other"]) == (["tpc@x.com"], "Placements", False, True)
assert cli.describe_rule(r) == "from: tpc@x.com -> move to Placements (+ more)"
n = outlook._native({**blank, "from": ["cell@x.com"], "read": True}, ol)
assert n["conditions"] == {"senderContains": ["cell@x.com"], "hasAttachments": True}  # address form replaced, rest kept
assert n["actions"] == {"moveToFolder": "F1", "stopProcessingRules": True, "markAsRead": True}
assert ol["conditions"]["fromAddresses"]  # base left untouched
n = outlook._native({**blank, "archive": True, "forward": ["a@x.com"]}, folder_id=lambda f: "id-" + f)
assert n["actions"] == {"moveToFolder": "id-archive", "forwardTo": [{"emailAddress": {"address": "a@x.com"}}]}

gf = {"id": "G1", "criteria": {"from": "a@x.com OR b@x.com", "query": "invoice"},
      "action": {"addLabelIds": ["Label_7", "STARRED"], "removeLabelIds": ["INBOX", "UNREAD"]}}
r = gmail._rule(gf, {"Label_7": "Bills"}.get)
assert (r["from"], r["has"], r["move"], r["archive"], r["read"], r["other"]) == (
    ["a@x.com", "b@x.com"], ["invoice"], "Bills", False, True, True)  # STARRED counts as more
r = gmail._rule({"id": "G2", "criteria": {"to": "me+x@x.com"}, "action": {"removeLabelIds": ["INBOX"]}})
assert r["archive"] and not r["move"] and not r["other"]
r = gmail._rule({"id": "G3", "criteria": {"from": "bank@x.com"}, "action": {"addLabelIds": ["Label_7"]}}, {"Label_7": "Bills"}.get)
assert cli.describe_rule(r) == "from: bank@x.com -> label Bills"  # label only, mail stays in the inbox
n = gmail._native({**blank, "move": "Bills", "read": False, "subject": ["due"]}, gf, {"Bills": "Label_9"}.get)
assert n == {"criteria": {"from": "a@x.com OR b@x.com", "query": "invoice", "subject": "due"},
             "action": {"addLabelIds": ["Label_9", "STARRED"], "removeLabelIds": ["INBOX"]}}  # old folder label swapped
assert gmail._native({**blank, "from": ["z@x.com"], "delete": True}) == {
    "criteria": {"from": "z@x.com"}, "action": {"addLabelIds": ["TRASH"]}}
print("rules ok")

# the background helper: a command runs as `homer` would, over a real socket, and a code change makes it step aside
import os  # noqa: E402
import sys  # noqa: E402
from homer import daemon  # noqa: E402


def fake_run(a):
    print(sys.stdin.read() if not sys.stdin.isatty() else "tty", os.getcwd())
    if a.cmd == "mark":
        homer.die("nope")


real_run, real_mtime, cwd = cli.run, daemon._code_mtime, os.getcwd()
cli.run = fake_run
with tempfile.TemporaryDirectory() as tmp:
    assert daemon.handle({"argv": ["send", "--to", "x@x.com", "-s", "hi"], "cwd": tmp, "stdin": "body"}) == {
        "code": 0, "out": f"body {tmp}\n", "err": ""}  # piped stdin and the caller's directory
    assert daemon.handle({"argv": ["mark", "read", "1"], "cwd": "/", "stdin": None}) == {
        "code": 1, "out": "tty /\n", "err": "homer: nope\n"}  # die() is exit 1 with its message
    assert os.getcwd() == cwd
    daemon.SOCK = Path(tmp) / "homer.sock"
    t = threading.Thread(target=daemon.serve, daemon=True)
    t.start()
    while not daemon.SOCK.exists():
        time.sleep(0.01)
    assert daemon.call({"argv": ["list"], "cwd": "/", "stdin": None}) == {"code": 0, "out": "tty /\n", "err": ""}
    daemon._code_mtime = lambda: real_mtime() + 1  # homer was updated
    assert daemon.call({"argv": ["list"], "cwd": "/", "stdin": None}) is None and not daemon.SOCK.exists()
    t.join(5)
    assert not t.is_alive() and daemon.call({"stop": True}) is None  # gone, and nothing listening
cli.run, daemon._code_mtime = real_run, real_mtime
print("daemon ok")

# the TUI itself, driven headless against a fake backend: archive + undo, then send a mail
try:
    from homer import tui
except ModuleNotFoundError:
    print("tui skipped: Textual not installed")
else:
    with tempfile.TemporaryDirectory() as tmp:
        store.CACHE, store.FOLDERS_FILE = Path(tmp) / "cache", Path(tmp) / "folders.toml"
        tui.base.DRAFTS, tui.base.SEND_DELAY = Path(tmp) / "drafts", 1
        box = {"INBOX": [row(1, "INBOX", "a@x.com", "t1", "2026-10-11T10:00:00+00:00", unread=True)],
               "Sent": [], "All": [], "Trash": []}
        done = []

        def move(n, a, folder, refs, dest):
            done.append((folder, dest))
            box[dest] += [{**m, "folder": dest, "id": m["id"] + "0"} for m in box[folder] if m["id"] in refs]
            box[folder] = [m for m in box[folder] if m["id"] not in refs]
            return [r + "0" for r in refs]

        fake = types.SimpleNamespace(
            special_folders=lambda n, a: {r"\Inbox": "INBOX", r"\Sent": "Sent", r"\All": "All", r"\Trash": "Trash"},
            summaries=lambda n, a, f, k, q=None: [dict(m) for m in box[f]], move=move, mark=lambda *a: None,
            fetch_raw=lambda n, a, f, ref: orig.as_bytes(), send=lambda n, a, msg, rcpts: done.append(msg))
        tui.app.BACKENDS = {"gmail": fake}

        async def drive():
            app = tui.Homer({"accounts": {"g": {"email": "me@x.com"}}}, "g")
            async with app.run_test(size=(110, 30)) as pilot:
                await pilot.pause(0.5)
                await pilot.press("e")
                await pilot.pause(0.3)
                assert app.screen.convs == [] and done == [("INBOX", "All")]
                await pilot.press("u")
                await pilot.pause(0.5)
                assert done[-1] == ("All", "INBOX") and len(app.screen.convs) == 1
                await pilot.press("c")
                await pilot.pause(0.3)
                await pilot.press(*"bob@x.com", "tab", "tab", "tab", *"Hi")
                app.screen.st["text"] = "hello"  # stands in for $EDITOR
                await pilot.press("ctrl+s")
                await pilot.pause(0.3)
                assert isinstance(app.screen, tui.MainScreen), str(app.screen.query_one(tui.Status).render())
                assert app.screen.convs[0]["out"]["state"] == "wait"
                await pilot.pause(2)
                sent = done[-1]
                assert sent["To"] == "bob@x.com" and sent["Subject"] == "Hi"
                assert sent.get_body(("plain",)).get_content().rstrip().endswith(store.FOOTER_TEXT)
                assert str(app.screen.query_one(tui.Status).render()).startswith('✓ Sent "Hi" to bob@x.com')

        asyncio.run(drive())

        # newsletters: link targets, image placeholders and long tracking URLs go; prose and short links stay
        news = ("Hi Arjun,\n[image: Logo]\n<https://t.example.com/open?id=1>\n\nRead the plan <https://t.example.com/c/9>"
                " at https://example.com/trip/plan.\nTrack it: https://click.example.com/" + "a" * 60 + "\n\n\n\nBye")
        assert tui.readable(news) == ("Hi Arjun,\n\nRead the plan at https://example.com/trip/plan.\n"
                                      "Track it: click.example.com/…\n\nBye")

        # folders: Enter unticks, s saves an empty feed; R on an account row renames only what's shown
        store.STATE_FILE = Path(tmp) / "tui.toml"
        box["Sent"] = [row(9, "Sent", "me@x.com", "t9", "2026-10-11T09:00:00+00:00")]
        fake.folders = lambda n, a: ["INBOX", "Sent", "All", "Trash", "News"]

        async def drive2():
            app = tui.Homer({"accounts": {"g": {"email": "me@x.com"}},
                             "tui": {"theme": "nord", "colors": {"sent": "nonsense!!"}}}, "g")
            async with app.run_test(size=(110, 30)) as pilot:
                await pilot.pause(0.5)
                main = app.screen
                assert app.theme == "nord" and "colors.sent" in str(main.query_one(tui.Status).render())
                sent = next(c for c in main.convs if c["mine"])
                assert main.row(sent, 80).plain.startswith(" →")
                both = {**sent, "mine": False, "msgs": sent["msgs"] + [{**sent["msgs"][0], "addr": "a@x.com"}]}
                assert any(s.style == tui.STYLE["thread"] and "(2)" in main.row(both, 80).plain[s.start:s.end]
                           for s in main.row(both, 80).spans)  # you replied in it: the count is coloured
                acct = app.accounts["g"]
                acct.set_sources("Feed", ["News"])
                app.push_screen(tui.MapScreen(acct, "Feed"))
                await pilot.pause(0.5)
                app.screen.query_one(tui.Ticks).highlighted = 0
                await pilot.press("enter", "s")
                await pilot.pause(0.2)
                assert app.screen is main and store.load_maps()["g"]["Feed"] == []
                await pilot.press("f")
                await pilot.pause(0.2)
                main.query_one(tui.FolderPane).highlighted = 0  # the account row
                await pilot.press("R", *"\b" * 5, *"Work", "enter")
                await pilot.pause(0.2)
                assert acct.label == "Work" and store.load_state() == {"names": {"g": "Work"}} and acct.name == "g"
                assert str(main.query_one("#hleft").render()).startswith("homer · Work ·")

                # ctrl+t: the cursor previews a theme, Esc puts nord back, Enter keeps and saves one
                await pilot.press("escape", "ctrl+t", "j")
                await pilot.pause(0.2)
                assert app.theme != "nord"
                await pilot.press("escape")
                await pilot.pause(0.2)
                assert app.theme == "nord" and "theme" not in store.load_state()
                await pilot.press("ctrl+t", "k", "enter")
                await pilot.pause(0.2)
                assert app.screen is main and store.load_state() == {"theme": app.theme, "names": {"g": "Work"}}
                assert app.theme != "nord" and tui.STYLE["me"].endswith(app.get_css_variables()["accent"])

                # a role takes the next colour of the theme's palette, and follows the theme when it changes
                await pilot.press("ctrl+t", "tab", "right", "b", "enter")
                await pilot.pause(0.2)
                assert store.load_state()["colors"] == {"unread": "$secondary"}  # bold toggled off
                assert tui.STYLE["unread"] == app.get_css_variables()["secondary"]
                await pilot.press("ctrl+t", "j", "enter")
                await pilot.pause(0.2)
                assert tui.STYLE["unread"] == app.get_css_variables()["secondary"] and app.screen is main

        asyncio.run(drive2())

        # the preview follows the cursor: each row shows its own conversation, newest mail opened, even when
        # the cursor moves faster than the server answers
        mails = {}
        inbox = []
        for i, (addr, thread, hour) in enumerate([("a@x.com", "p1", 9), ("me@x.com", "p1", 10), ("b@x.com", "p2", 8),
                                                  ("me@x.com", "p3", 7), ("c@x.com", "p4", 6), ("d@x.com", "p5", 5)]):
            inbox.append(row(100 + i, "INBOX", addr, thread, f"2026-10-11T{hour:02}:00:00+00:00", to=("", "z@x.com")))
            m = email.message.EmailMessage()
            m.set_content(f"Body of mail {100 + i}")
            mails[str(100 + i)] = m

        def slow_raw(n, a, f, ref):
            time.sleep(0.15)  # a server slower than the cursor
            return mails[ref].as_bytes()

        fake3 = types.SimpleNamespace(
            special_folders=lambda n, a: {r"\Inbox": "INBOX", r"\Sent": "Sent", r"\All": "All", r"\Trash": "Trash"},
            summaries=lambda n, a, f, k, q=None: [dict(m) for m in inbox] if f == "INBOX" else [],
            fetch_raw=slow_raw, mark=lambda *a: None)

        async def drive3():
            tui.app.BACKENDS = {"gmail": fake3}
            store.CACHE = Path(tmp) / "cache3"
            app = tui.Homer({"accounts": {"g": {"email": "me@x.com"}}}, "g")
            async with app.run_test(size=(140, 30)) as pilot:
                await pilot.pause(0.6)
                main = app.screen
                shown = lambda: "".join(str(w.render()) for w in main.query_one("#preview").children)

                async def check(i):
                    await pilot.pause(0.6)
                    c = main.convs[main.query_one("#list").highlighted]
                    assert c is main.convs[i] and c["subject"] in shown(), (i, shown())
                    assert f"Body of mail {c['msgs'][-1]['id']}" in shown(), (i, shown())
                    others = [m["id"] for d in main.convs if d is not c for m in d["msgs"]]
                    assert not any(f"Body of mail {o}" in shown() for o in others), (i, shown())

                assert [c["key"] for c in main.convs] == ["p1", "p2", "p3", "p4", "p5"]
                await check(0)
                for i in range(1, 5):
                    await pilot.press("j")
                    await check(i)
                for i in (3, 2):
                    await pilot.press("k")
                    await check(i)
                await pilot.press("g")
                await check(0)
                await pilot.press("G")
                await check(4)
                main.raws.clear()  # nothing cached: every row has to be fetched again
                await pilot.press("g", "j", "j", "j")  # faster than the server; only the last row may win
                await check(3)
                await pilot.press("k", "k")
                await check(1)

        asyncio.run(drive3())
        print("tui ok")
