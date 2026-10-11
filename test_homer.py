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
