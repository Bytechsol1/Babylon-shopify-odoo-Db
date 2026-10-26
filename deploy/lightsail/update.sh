#!/usr/bin/env bash
# Pull the latest code from GitHub and restart:  sudo bash /opt/babylon/deploy/lightsail/update.sh
set -euo pipefail
APP=/opt/babylon
sudo -u babylon git -C "$APP" pull --ff-only
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
chown -R babylon:babylon "$APP"
systemctl restart babylon
sleep 2
systemctl --no-pager --lines=5 status babylon
