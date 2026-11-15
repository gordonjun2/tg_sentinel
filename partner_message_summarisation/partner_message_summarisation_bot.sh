#!/bin/bash
# Start partner message summarisation via systemd (exclusive owner).
# Logs: /root/tg_sentinel/partner_message_summarisation_output.log
#
# Prerequisites (see partner_message_summarisation/README.md):
#   * DATABASE_URL in /root/tg_sentinel/.env
#   * partner_message_summarisation/data/sentinel_listener.session
#     (python -m partner_message_summarisation.login once)

set -euo pipefail

systemctl start tg-sentinel-partner
systemctl is-active --quiet tg-sentinel-partner
echo "tg-sentinel-partner is active. Logs: partner_message_summarisation_output.log"
