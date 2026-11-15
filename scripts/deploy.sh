#!/usr/bin/env bash
# Server-side deploy for tg_sentinel.
# Always syncs origin/main and refreshes venv deps.
# Optional --restart: clean systemd stop, then start both long-runners.
#
# Usage (on the VPS, from any cwd):
#   bash /root/tg_sentinel/scripts/deploy.sh
#   bash /root/tg_sentinel/scripts/deploy.sh --restart

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RESTART=0
for arg in "$@"; do
  case "$arg" in
    --restart) RESTART=1 ;;
    -h|--help)
      echo "usage: scripts/deploy.sh [--restart]" >&2
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      echo "usage: scripts/deploy.sh [--restart]" >&2
      exit 2
      ;;
  esac
done

UNITS=(tg-sentinel-bot tg-sentinel-partner)

echo "==> syncing to origin/main"
git fetch origin main
git reset --hard origin/main

if [ ! -x "$ROOT/venv/bin/pip" ]; then
  echo "missing venv at $ROOT/venv — create it before deploying" >&2
  exit 1
fi

echo "==> installing requirements"
"$ROOT/venv/bin/pip" install -r requirements.txt

if [ "$RESTART" -eq 1 ]; then
  echo "==> stopping long-running units"
  systemctl stop "${UNITS[@]}"

  for unit in "${UNITS[@]}"; do
    if systemctl is-active --quiet "$unit"; then
      echo "unit still active after stop: $unit" >&2
      systemctl status "$unit" --no-pager >&2 || true
      exit 1
    fi
  done

  echo "==> starting long-running units"
  systemctl start "${UNITS[@]}"

  for unit in "${UNITS[@]}"; do
    if ! systemctl is-active --quiet "$unit"; then
      echo "unit failed to become active: $unit" >&2
      systemctl status "$unit" --no-pager >&2 || true
      exit 1
    fi
  done
else
  echo "==> skip restart (long-runners keep current in-memory code)"
fi

echo "==> deploy ok"
echo "commit=$(git rev-parse --short HEAD)"
for unit in "${UNITS[@]}"; do
  echo "$unit: $(systemctl is-active "$unit" 2>/dev/null || echo unknown)"
done
