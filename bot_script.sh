#!/bin/bash
# Start the main bot via systemd (exclusive owner of the process).
# Logs: /root/tg_sentinel/script_tg_sentinel_output.log

set -euo pipefail

systemctl start tg-sentinel-bot
systemctl is-active --quiet tg-sentinel-bot
echo "tg-sentinel-bot is active. Logs: script_tg_sentinel_output.log"
