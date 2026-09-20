#!/bin/bash
# Deployment script for the Telegram intelligence pipeline (partner_message_summarisation).
# Mirrors gmail_calendar/gmail_calendar_bot.sh conventions.
#
# VPS path: /root/tg_sentinel/partner_message_summarisation/partner_message_summarisation_bot.sh
# Run with: bash partner_message_summarisation/partner_message_summarisation_bot.sh
#
# Prerequisites (see partner_message_summarisation/README.md):
#   * DATABASE_URL (hosted PostgreSQL) in /root/tg_sentinel/.env
#   * partner_message_summarisation/data/sentinel_listener.session deployed (python -m partner_message_summarisation.login)

# Always operate from project root so 'python -m partner_message_summarisation.bot' resolves.
cd /root/tg_sentinel || exit 1

exec > >(tee -a /root/tg_sentinel/partner_message_summarisation_output.log) 2>&1

source venv/bin/activate || { echo "Failed to activate virtual environment"; exit 1; }
echo "Activated virtual environment"

# Long-running process — nohup + disown so it survives SSH disconnect.
echo "Starting partner_message_summarisation.bot"
nohup python -m partner_message_summarisation.bot >> /root/tg_sentinel/partner_message_summarisation_output.log 2>&1 &
disown

echo "partner_message_summarisation.bot is running in the background. Logs: partner_message_summarisation_output.log"
