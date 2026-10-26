#!/usr/bin/env bash
# One-time setup of the Babylon pack station backend on a fresh AWS Lightsail Ubuntu 24.04 instance.
#
#   sudo bash setup.sh <domain>
#
# <domain> is the HTTPS address the API will answer on: a DNS name pointing at the instance's static IP
# (e.g. api.babylonllc.com), or <static-ip-with-dashes>.sslip.io if there's no domain yet
# (e.g. 3-101-42-7.sslip.io). Caddy gets and renews the HTTPS certificate by itself.
#
# Private repo? Set REPO to a URL with a read-only token first:
#   sudo REPO=https://<token>@github.com/Bytechsol1/Babylon-shopify-odoo-Db.git bash setup.sh <domain>
set -euo pipefail

DOMAIN="${1:?usage: sudo bash setup.sh <domain>}"
REPO="${REPO:-https://github.com/Bytechsol1/Babylon-shopify-odoo-Db.git}"
APP=/opt/babylon
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> packages"
apt-get update -q
apt-get install -y -q python3-venv python3-pip git curl gpg debian-keyring debian-archive-keyring apt-transport-https
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt -o /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy
fi

echo "==> app user and code in $APP"
id -u babylon >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin babylon
if [ ! -d "$APP/.git" ]; then git clone -q "$REPO" "$APP"; fi
git config --global --add safe.directory "$APP"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
mkdir -p "$APP/var/labels"
if [ ! -f "$APP/.env" ]; then
  cp "$HERE/env.example" "$APP/.env"
  echo "   created $APP/.env from the template: fill in the secrets before starting"
fi
chown -R babylon:babylon "$APP"
chmod 600 "$APP/.env"

echo "==> service"
install -m 644 "$HERE/babylon.service" /etc/systemd/system/babylon.service
sed "s/{DOMAIN}/$DOMAIN/" "$HERE/Caddyfile" > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable babylon caddy
systemctl restart caddy

echo
echo "Done. Next:"
echo "  1. sudo nano $APP/.env      (DATABASE_URL, Shopify, Supabase, ALLOWED_EMAILS)"
echo "  2. sudo systemctl restart babylon && sudo systemctl status babylon"
echo "  3. curl https://$DOMAIN/api/public-config"
echo "  Logs: sudo journalctl -u babylon -f"
