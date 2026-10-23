#!/bin/sh
# Start a throwaway mail server with made-up mail, for screenshots and recordings.
# Usage: demo/up.sh DEMO_HOME, then run homer as: HOME=DEMO_HOME PATH=DEMO_HOME/bin:$PATH homer ...
# Stop with: podman rm -f homer-demo-mail; pkill -f demo/relay.py
set -e
here=$(cd "$(dirname "$0")" && pwd)
home=${1:?usage: demo/up.sh DEMO_HOME}
[ "$home" != "$HOME" ] || { echo "DEMO_HOME must not be your HOME" >&2; exit 1; }
rm -rf "$home" && mkdir -p "$home"
podman rm -f homer-demo-mail >/dev/null 2>&1 || true
podman run -d --name homer-demo-mail -p 3025:3025 -p 3993:3993 -e GREENMAIL_OPTS='-Dgreenmail.setup.test.all
  -Dgreenmail.hostname=0.0.0.0 -Dgreenmail.users.login=email -Dgreenmail.verbose=false
  -Dgreenmail.users=maya:demo@lumenlabs.example,maya.rao:demo@example.com' docker.io/greenmail/standalone:2.1.2 >/dev/null
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj /CN=localhost \
  -keyout "$home/key.pem" -out "$home/cert.pem" 2>/dev/null
pkill -f "$here/relay.py" 2>/dev/null || true
nohup python3 "$here/relay.py" "$home" >/dev/null 2>&1 &
until python3 -c 'import imaplib; imaplib.IMAP4_SSL("127.0.0.1", 3993)' 2>/dev/null; do sleep 1; done
python3 "$here/seed.py" "$home"
printf '#!/bin/sh\nPYTHONPATH=%s:%s exec python3 -m homer "$@"\n' "$(dirname "$here")" "$(python3 -m site --user-site)" \
  > "$home/bin/homer" && chmod +x "$home/bin/homer"
echo "ready: HOME=$home PATH=$home/bin:\$PATH homer"
