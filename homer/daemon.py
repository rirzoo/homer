"""Background helper that keeps mail connections open between `homer` commands.

The first command starts it; later ones hand it their arguments over a Unix socket and print what it sends back,
so they skip connecting and logging in (about 1.3s of a 2s Gmail `list`). It exits after IDLE seconds without a
request, after MAX_AGE in all (the rules/filters tokens are cached without ever being refreshed), and when homer's
code changes. HOMER_DAEMON=0 turns it off."""
import contextlib
import io
import json
import os
import socket
import socketserver
import subprocess
import sys
import time
import traceback
from pathlib import Path

COMMANDS = {"folders", "list", "read", "send", "reply", "forward", "move", "mark", "mkdir", "rules"}
IDLE = 600  # seconds; matches gmail.IDLE, after which a cached IMAP session is replaced anyway
MAX_AGE = 45 * 60  # access tokens last about an hour
SOCK = Path(os.environ.get("XDG_RUNTIME_DIR") or Path("~/.cache/homer").expanduser()) / "homer.sock"
PKG = Path(__file__).resolve().parent


def _code_mtime():
    return max(p.stat().st_mtime for p in PKG.glob("*.py"))


# --- client side (called from cli.main) ---------------------------------------

def call(req):
    """Send one request. Returns the reply, or None when the helper didn't run it (none listening, or it
    stepped aside), so the caller runs the command itself. A connection lost after the helper accepted the
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
            accepted = f.readline() == b'"run"\n'  # otherwise "stale", or a helper that was just exiting
        except OSError:
            accepted = False
        if not accepted:
            return None
        try:
            reply = f.readline()
        except OSError:
            reply = b""
    if not reply:
        sys.exit("homer: lost the background helper during the command; it may have run, so check before retrying")
    return json.loads(reply)


def spawn():
    """Start the helper in the background for the next command. Failing is fine: homer runs commands itself."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(PKG.parent), os.environ.get("PYTHONPATH")]))}
    try:  # -P and cwd="/": `-m` would otherwise put the caller's directory, and any stray json.py there, on sys.path
        subprocess.Popen([sys.executable, "-P", "-m", "homer.daemon"], cwd="/", env=env, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# --- helper side ----------------------------------------------------------------

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


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        req = json.loads(self.rfile.readline() or b"null")
        srv = self.server
        if not req:  # a probe from a helper that's starting up
            return
        if req.get("stop") or _code_mtime() > srv.mtime:  # `homer login`, or homer was updated: step aside
            SOCK.unlink(missing_ok=True)  # first, so the next command starts a fresh helper
            srv.done = srv.unlinked = True
            self.wfile.write(b'"stale"\n')
            return
        self.wfile.write(b'"run"\n')
        self.wfile.flush()
        self.wfile.write(json.dumps(handle(req)).encode() + b"\n")
        srv.done = time.monotonic() - srv.started > MAX_AGE


class _Server(socketserver.UnixStreamServer):
    timeout = IDLE  # one request at a time: handle() changes the process's directory and stdout

    def handle_timeout(self):
        self.done = True


def serve():
    SOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.connect(str(SOCK))
        return  # another helper is already listening
    except OSError:
        SOCK.unlink(missing_ok=True)  # left behind by one that crashed
    finally:
        probe.close()
    umask = os.umask(0o077)  # the socket is only for this user
    try:
        srv = _Server(str(SOCK), _Handler)
    except OSError:
        return  # another helper bound it first
    finally:
        os.umask(umask)
    srv.started, srv.mtime, srv.done, srv.unlinked = time.monotonic(), _code_mtime(), False, False
    with srv:
        while not srv.done:
            srv.handle_request()
    if not srv.unlinked:  # once unlinked, the path may already belong to a newer helper
        SOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    serve()
