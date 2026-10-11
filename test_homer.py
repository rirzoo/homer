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

from homer.tui import parse_draft  # noqa: E402
d = parse_draft("To: a@x.com, \"Doe, Jane\" <j@x.com>\nCc:\nSubject: Hi, all\nAttach: ~/a.pdf, b.txt\n\nline 1\n\nline 2\n")
assert d["to"] == ["a@x.com", "\"Doe, Jane\" <j@x.com>"] and d["cc"] == [] and d["subject"] == "Hi, all"
assert d["attach"] == [str(Path("~/a.pdf").expanduser()), "b.txt"] and d["body"] == "line 1\n\nline 2"
assert parse_draft("To: \nSubject: \nAttach: \n\n")["body"] == ""  # untouched template = discard
assert parse_draft("Attach: \n\nthanks!")["to"] == []  # reply template
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
