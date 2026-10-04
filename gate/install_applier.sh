#!/usr/bin/env bash
# macOS, sudo required. Supply deployment-specific paths and service label.
set -euo pipefail
if [ "${1:-}" = --check ]; then
  echo 'CHECK: requires macOS, sudo, GW_DIR, GATEWAY_PYTHON, SERVICE_LABEL; no files changed'
  exit 0
fi
: "${GW_DIR:?Directory containing config.yaml and run.sh}"
: "${GATEWAY_PYTHON:?Absolute Python path with PyYAML installed}"
: "${SERVICE_LABEL:?launchd service label}"
[ "$(uname -s)" = Darwin ] || { echo 'REFUSE: macOS installer'; exit 2; }
[ "$(id -u)" != 0 ] || { echo 'Run as an ordinary user; the installer invokes sudo'; exit 2; }
if [ -n "${EXPECTED_HOST:-}" ]; then
  [ "$(hostname -s)" = "$EXPECTED_HOST" ] || { echo 'REFUSE: unexpected host'; exit 2; }
fi
SRC=$(cd "$(dirname "$0")" && pwd)
ROOT=/usr/local/etc/litellm-gateway
"$GATEWAY_PYTHON" -c 'import yaml'
test -f "$GW_DIR/config.yaml" && test -f "$GW_DIR/run.sh"
sudo mkdir -p /usr/local/sbin /etc/sudoers.d "$ROOT/staging" "$ROOT/consumed"
if [ ! -L "$GW_DIR/config.yaml" ]; then sudo cp -p "$GW_DIR/config.yaml" "$ROOT/config.yaml"; fi
sudo chown -R root:wheel "$ROOT"
sudo chmod 755 "$ROOT" "$ROOT/consumed"
sudo chmod 644 "$ROOT/config.yaml"
sudo chown "$(id -un):staff" "$ROOT/staging"
sudo chmod 755 "$ROOT/staging"
sudo install -o root -g wheel -m 755 "$SRC/route_apply.py" /usr/local/sbin/route_apply.py
sudo sed -i '' "1s|.*|#!${GATEWAY_PYTHON}|" /usr/local/sbin/route_apply.py
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/route_apply.py\n' "$(id -un)" | sudo tee /etc/sudoers.d/route-apply >/dev/null
sudo chmod 440 /etc/sudoers.d/route-apply
sudo visudo -cf /etc/sudoers.d/route-apply
if ! grep -q -- "--config $ROOT/config.yaml" "$GW_DIR/run.sh"; then
  cp -p "$GW_DIR/run.sh" "$GW_DIR/run.sh.bak-$(date +%Y%m%d-%H%M%S)"
  sed -i '' "s|--config config.yaml|--config $ROOT/config.yaml|" "$GW_DIR/run.sh"
  grep -q -- "--config $ROOT/config.yaml" "$GW_DIR/run.sh"
fi
sudo chown root:wheel "$GW_DIR/run.sh"
sudo chmod 755 "$GW_DIR/run.sh"
if [ ! -L "$GW_DIR/config.yaml" ]; then
  mv "$GW_DIR/config.yaml" "$GW_DIR/config.yaml.before-$(date +%Y%m%d-%H%M%S)"
  ln -s "$ROOT/config.yaml" "$GW_DIR/config.yaml"
fi
launchctl kickstart -k "gui/$(id -u)/$SERVICE_LABEL"
echo 'INSTALL_DONE: verify ordinary-user write refusal, permit refusal, and service health'
