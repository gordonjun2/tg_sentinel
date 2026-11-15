#!/bin/bash
# Idempotent orchestrator for long-running services via systemd.
#
#   * Main bot (tg-sentinel-bot)
#   * Partner message summarisation (tg-sentinel-partner)
#   * Gmail + Calendar — on hold (no unit)
#   * luma_reminder / linkedin — cron-managed, not started here
#
# Safe to re-run: systemctl start is a no-op when already active.
# Restart: systemctl restart tg-sentinel-bot tg-sentinel-partner
#   or: bash scripts/deploy.sh --restart
#
# One-time unit install and CD: see docs/DEPLOYMENT.md

set -euo pipefail

UNITS=(tg-sentinel-bot tg-sentinel-partner)

echo "Starting long-running units via systemd"
systemctl start "${UNITS[@]}"
echo ""
echo "=== Service status ==="
for unit in "${UNITS[@]}"; do
  state="$(systemctl is-active "$unit" 2>/dev/null || echo inactive)"
  echo "$unit: $state"
done
echo "gmail-calendar:         not running (on hold)"
echo "luma-reminder:          cron-managed (not managed here)"
