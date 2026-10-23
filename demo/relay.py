"""SMTP on 127.0.0.1:2587 with STARTTLS, in front of GreenMail (which has none). Takes any login, hands the mail
to GreenMail for delivery and files a copy in the sender's Sent folder, the way Gmail does. Usage: python3 relay.py DEMO_HOME (where up.sh put cert.pem and key.pem)"""
import email
import imaplib
import smtplib
import socketserver
import ssl
import sys
import time
from pathlib import Path

ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)


class Handler(socketserver.StreamRequestHandler):
    def say(self, line):
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()

    def handle(self):
        self.say("220 demo ESMTP")
        rcpts = []
        while line := self.rfile.readline().decode().strip():
            cmd = line.split()[0].upper()
            if cmd == "EHLO":
                self.say("250-demo\r\n250-STARTTLS\r\n250 AUTH PLAIN")
            elif cmd == "STARTTLS":
                self.say("220 go")
                self.connection = ctx.wrap_socket(self.connection, server_side=True)
                self.rfile, self.wfile = self.connection.makefile("rb"), self.connection.makefile("wb")
            elif cmd == "AUTH":
                self.say("235 ok")
            elif cmd == "MAIL":
                rcpts = []
                self.say("250 ok")
            elif cmd == "RCPT":
                rcpts.append(line.split(":", 1)[1].strip(" <>"))
                self.say("250 ok")
            elif cmd == "DATA":
                self.say("354 go")
                data = b""
                while (chunk := self.rfile.readline()) not in (b".\r\n", b""):
                    data += chunk[1:] if chunk.startswith(b"..") else chunk
                deliver(data, rcpts)
                self.say("250 queued")
            elif cmd == "QUIT":
                self.say("221 bye")
                return
            else:
                self.say("250 ok")


def deliver(data, rcpts):
    sender = email.utils.parseaddr(email.message_from_bytes(data)["From"])[1]
    with smtplib.SMTP("127.0.0.1", 3025) as s:
        s.sendmail(sender, rcpts, data)
    m = imaplib.IMAP4_SSL("127.0.0.1", 3993)
    m.login(sender, "demo")
    m.create("Sent")
    m.append("Sent", r"(\Seen)", imaplib.Time2Internaldate(time.time()), data)
    m.logout()


if __name__ == "__main__":
    ctx.load_cert_chain(Path(sys.argv[1]) / "cert.pem", Path(sys.argv[1]) / "key.pem")  # made by up.sh
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", 2587), Handler) as srv:
        srv.serve_forever()
