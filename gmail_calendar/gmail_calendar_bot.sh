#!/bin/bash
# Deployment script for the Gmail + Calendar integration.
# Mirrors bot_script.sh / luma_reminder.sh conventions.
#
# VPS path: /root/tg_sentinel/gmail_calendar/gmail_calendar_bot.sh
# Run with: bash gmail_calendar/gmail_calendar_bot.sh

# Always operate from project root so 'python -m gmail_calendar.bot' resolves.
cd /root/tg_sentinel || exit 1

exec > >(tee -a /root/tg_sentinel/gmail_calendar_output.log) 2>&1

source venv/bin/activate || { echo "Failed to activate virtual environment"; exit 1; }
echo "Activated virtual environment"

# Long-running process — nohup + disown so it survives SSH disconnect.
echo "Starting gmail_calendar.bot"
nohup python -m gmail_calendar.bot >> /root/tg_sentinel/gmail_calendar_output.log 2>&1 &
disown

echo "gmail_calendar.bot is running in the background. Logs: gmail_calendar_output.log"
