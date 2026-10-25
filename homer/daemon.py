"""Background service: keeps every account's mail fresh for the TUI and its connections open for the CLI.

`homer daemon enable` runs it as a systemd user service (homer.service). Every SYNC_EVERY seconds it fetches each
account's homer folders into the TUI's cache, so the TUI opens on current mail, and it shows a desktop notification
for new unread mail in the folders you picked with N in the TUI. Commands hand it their arguments over a Unix socket
and print what it sends back, so they skip connecting and logging in (about 1.3s of a 2s Gmail `list`); without the
service they run as before. It exits after MAX_AGE (the rules/filters tokens are cached without ever being
refreshed), when homer's code changes, and on `homer login`, and systemd starts a fresh one. HOMER_DAEMON=0 keeps
commands from using it."""
import contextlib
import io
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

COMMANDS = {"folders", "list", "read", "send", "reply", "forward", "move", "mark", "mkdir", "rules"}
MAX_AGE = 45 * 60  # access tokens last about an hour
SOCK = Path(os.environ.get("XDG_RUNTIME_DIR") or Path("~/.cache/homer").expanduser()) / "homer.sock"
PKG = Path(__file__).resolve().parent
UNIT = Path("~/.config/systemd/user/homer.service").expanduser()


def _code_mtime():
    return max(p.stat().st_mtime for p in PKG.glob("*.py"))


def unit_text():
    """The systemd user unit; -P and WorkingDirectory=/ keep a stray json.py in some folder off sys.path."""
    return f"""[Unit]
Description=homer: keeps your mail synced and signed in

[Service]
ExecStart={sys.executable} -P -m homer.daemon
WorkingDirectory=/
Environment=PYTHONPATH={PKG.parent}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""


# --- client side (called from cli.main and the TUI) -------------------------------

def call(req):
    """Send one request. Returns the reply, or None when the service didn't run it (none listening, or it
    stepped aside), so the caller runs the command itself. A connection lost after the service accepted the
    request dies instead: the command may have run, and running a send twice would mail people twice."""
    s = socket.socket(socket.AF_UNIX)
    try:
        s.connect(str(SOCK))
    except OSError:
        s.close()
        return None
    with s, s.makefile("rwb") as f:
        try:
            f.write(json.dumps(req).encode() + b"\n")
            f.flush()
            accepted = f.readline() == b'"run"\n'  # otherwise "stale", or a service that was just exiting
        except OSError:
            accepted = False
        if not accepted:
            return None
        try:
            reply = f.readline()
        except OSError:
            reply = b""
    if not reply:
        sys.exit("homer: lost the background service during the command; it may have run, so check before retrying")
    return json.loads(reply)


def running():
    s = socket.socket(socket.AF_UNIX)
    with s:
        try:
            s.connect(str(SOCK))  # the service reads an empty line as a probe and hangs up
            return True
        except OSError:
            return False


# --- service side ------------------------------------------------------------------

class _Stdin(io.StringIO):
    """The caller's piped stdin, or an empty terminal when it had none, so `isatty()` checks behave the same."""
    def __init__(self, text):
        super().__init__(text or "")
        self.tty = text is None

    def isatty(self):
        return self.tty


def handle(req):
    """Run one command the way `homer` would, in the caller's directory: {code, out, err}."""
    from . import cli
    out, err, cwd, stdin = io.StringIO(), io.StringIO(), os.getcwd(), sys.stdin
    code = 0
    try:
        os.chdir(req["cwd"])  # relative --attach and --save paths
        sys.stdin = _Stdin(req["stdin"])
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cli.run(cli.parser().parse_args(req["argv"]))
    except SystemExit as e:  # as the interpreter would: die() passes a message, which means print it and exit 1
        if e.code is None or isinstance(e.code, int):
            code = e.code or 0
        else:
            print(e.code, file=err)
            code = 1
    except Exception:
        traceback.print_exc(file=err)
        code = 1
    finally:
        sys.stdin = stdin
        os.chdir(cwd)
    return {"code": code, "out": out.getvalue(), "err": err.getvalue()}


def notify(title, body):
    try:
        subprocess.run(["notify-send", "-a", "homer", title, body], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass  # no notification daemon: the mail is still in the cache for the TUI


def sync_once():
    """Fetch every account's homer folders (not Trash) into the TUI's cache, and announce new unread mail in the
    folders picked for notifications. New means not in the cached listing it replaces, so a restart or the TUI
    syncing in between never announces mail twice, and a first sync (nothing cached yet) announces nothing.
    Absolute paths only: a CLI command may have changed the directory meanwhile."""
    from . import cli, store
    from .common import load_config
    try:
        cfg, maps, state = load_config(), store.load_maps(), store.load_state()  # re-read: the TUI may change them
    except (Exception, SystemExit):
        return
    for name, a in cfg.get("accounts", {}).items():
        backend = cli.BACKENDS.get(a.get("provider", "gmail"))
        if backend is None:
            continue
        acct = store.Account(name, a, backend, maps, state.get("names", {}).get(name))
        watched = state.get("notify", {}).get(name, ["Mailbox"])
        try:
            if acct.special is None:
                acct.load_special()
            for folder in [f for f in acct.folders() if f != "Trash"]:
                old = acct.cached(folder)
                msgs = acct.fetch(folder, store.PAGE)
                acct.store(folder, msgs)
                seen = {m["msgid"] for m in old}
                new = [m for m in msgs if m["unread"] and m["msgid"] not in seen and m["addr"].lower() not in acct.me]
                if not (old and new and folder in watched):
                    continue
                if len(new) > 3:
                    notify(f"homer · {acct.label}", f"{len(new)} new in {folder}")
                else:
                    for m in new:
                        notify(f"homer · {acct.label} · {folder}", f"{m['name'] or m['addr']}: {m['subject']}")
        except (Exception, SystemExit):
            continue  # offline, signed out or the keyring locked: try again next round


def sync_loop():
    from .store import SYNC_EVERY
    while True:
        started = time.monotonic()
        sync_once()
        time.sleep(max(5, SYNC_EVERY - (time.monotonic() - started)))


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        req = json.loads(self.rfile.readline() or b"null")
        srv = self.server
        if not req:  # a probe
            return
        if req.get("stop") or _code_mtime() > srv.mtime:  # `homer login`, or homer was updated: step aside
            SOCK.unlink(missing_ok=True)  # first, so the next command doesn't wait on a service that's leaving
            srv.done = srv.unlinked = True
            self.wfile.write(b'"stale"\n')
            return
        self.wfile.write(b'"run"\n')
        self.wfile.flush()
        self.wfile.write(json.dumps(handle(req)).encode() + b"\n")
        srv.done = time.monotonic() - srv.started > MAX_AGE


class _Server(socketserver.UnixStreamServer):
    timeout = 60  # one request at a time: handle() changes the process's directory and stdout

    def handle_timeout(self):
        self.done = time.monotonic() - self.started > MAX_AGE or _code_mtime() > self.mtime


def serve(sync=True):
    SOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    call({"stop": True})  # a helper started by an older homer: make it step aside
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.connect(str(SOCK))
        return  # another service is already listening
    except OSError:
        SOCK.unlink(missing_ok=True)  # left behind by one that crashed
    finally:
        probe.close()
    umask = os.umask(0o077)  # the socket is only for this user
    try:
        srv = _Server(str(SOCK), _Handler)
    except OSError:
        return  # another service bound it first
    finally:
        os.umask(umask)
    srv.started, srv.mtime, srv.done, srv.unlinked = time.monotonic(), _code_mtime(), False, False
    if sync:
        threading.Thread(target=sync_loop, daemon=True).start()
    with srv:
        while not srv.done:
            srv.handle_request()
    if not srv.unlinked:  # once unlinked, the path may already belong to a newer service
        SOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    serve()
