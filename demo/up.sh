#!/bin/sh
# Start a throwaway mail server with made-up mail, for screenshots and recordings.
# Usage: demo/up.sh DEMO_HOME, then run homer as: HOME=DEMO_HOME PATH=DEMO_HOME/bin:$PATH homer ...
# Stop with: podman rm -f homer-demo-mail; pkill -f demo/relay.py
set -e
here=$(cd "$(dirname "$0")" && pwd)
home=${1:?usage: demo/up.sh DEMO_HOME}
[ "$home" != "$HOME" ] || { echo "DEMO_HOME must not be your HOME" >&2; exit 1; }
rm -rf "$home" && mkdir -p "$home"
# one certificate for GreenMail's IMAP and the relay's SMTP; homer trusts it through SSL_CERT_FILE (bin/homer below)
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj /CN=localhost -addext subjectAltName=IP:127.0.0.1,DNS:localhost \
  -keyout "$home/key.pem" -out "$home/cert.pem" 2>/dev/null
openssl pkcs12 -export -inkey "$home/key.pem" -in "$home/cert.pem" -passout pass:demo -out "$home/greenmail.p12"
chmod 644 "$home/greenmail.p12"  # GreenMail runs as its own user in the container
podman rm -f homer-demo-mail >/dev/null 2>&1 || true
podman run -d --name homer-demo-mail -p 3025:3025 -p 3993:3993 -v "$home/greenmail.p12:/greenmail.p12:ro,Z" \
  -e GREENMAIL_OPTS='-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0 -Dgreenmail.users.login=email
  -Dgreenmail.verbose=false -Dgreenmail.tls.keystore.file=/greenmail.p12 -Dgreenmail.tls.keystore.password=demo
  -Dgreenmail.users=maya:demo@lumenlabs.example,maya.rao:demo@example.com' docker.io/greenmail/standalone:2.1.2 >/dev/null
pkill -f "$here/relay.py" 2>/dev/null || true
nohup python3 "$here/relay.py" "$home" >/dev/null 2>&1 &
until python3 -c 'import imaplib; imaplib.IMAP4_SSL("127.0.0.1", 3993)' 2>/dev/null; do sleep 1; done
python3 "$here/seed.py" "$home"
printf '#!/bin/sh\nSSL_CERT_FILE=%s PYTHONPATH=%s:%s exec python3 -m homer "$@"\n' "$home/cert.pem" "$(dirname "$here")" \
  "$(python3 -m site --user-site)" \
  > "$home/bin/homer" && chmod +x "$home/bin/homer"
echo "ready: HOME=$home PATH=$home/bin:\$PATH homer"
