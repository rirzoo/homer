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

assert homer.html_to_text("<style>x{}</style><p>Hi&nbsp;there</p><p>bye</p>") == "Hi there\nbye"
print("ok")
