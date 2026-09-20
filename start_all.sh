#!/bin/bash
# Idempotent orchestrator — starts every long-running service in one command.
#
#   * Main bot (bot.py)                      — started here
#   * Partner message summarisation          — started here (listener + daily digest)
#   * Gmail + Calendar integration           — ON HOLD (kept in repo, block commented out below)
#   * luma_reminder                          — cron-managed, intentionally NOT started here
#
# Safe to re-run: a service already running is skipped (pgrep guard), so this
# never double-spawns or fights over a Pyrogram session file.
#
# Restart flow: kill <pid> (SIGTERM), then re-run this script — killed services
# come back, running ones are left alone. For auto-start after a VPS reboot add:
#   @reboot bash /root/tg_sentinel/start_all.sh
# to crontab (crontab -e).

cd /root/tg_sentinel || exit 1

source venv/bin/activate || { echo "Failed to activate virtual environment"; exit 1; }
echo "Activated virtual environment"
echo ""

# --- 1. Main bot ------------------------------------------------------------
MAIN_PATTERN="python bot.py"
if pgrep -f "$MAIN_PATTERN" > /dev/null; then
    echo "[main-bot] already running (pid $(pgrep -f "$MAIN_PATTERN" | head -1)) — skipped"
else
    nohup python bot.py >> /root/tg_sentinel/script_tg_sentinel_output.log 2>&1 &
    disown
    echo "[main-bot] started (pid $!) — logs: script_tg_sentinel_output.log"
fi

# --- 2. Partner message summarisation ---------------------------------------
PMS_PATTERN="python -m partner_message_summarisation.bot"
PMS_SESSION="/root/tg_sentinel/partner_message_summarisation/data/sentinel_listener.session"

# Non-blocking preflight: warn about missing prerequisites, start anyway.
if [ ! -f "$PMS_SESSION" ]; then
    echo "[partner-summarisation] WARNING: user session file missing:"
    echo "    $PMS_SESSION"
    echo "  Run once before first use: python -m partner_message_summarisation.login"
fi
if ! grep -q "^DATABASE_URL=" /root/tg_sentinel/.env 2>/dev/null; then
    echo "[partner-summarisation] WARNING: DATABASE_URL not found in .env — service will exit at startup"
fi

if pgrep -f "$PMS_PATTERN" > /dev/null; then
    echo "[partner-summarisation] already running (pid $(pgrep -f "$PMS_PATTERN" | head -1)) — skipped"
else
    nohup python -m partner_message_summarisation.bot >> /root/tg_sentinel/partner_message_summarisation_output.log 2>&1 &
    disown
    echo "[partner-summarisation] started (pid $!) — logs: partner_message_summarisation_output.log"
fi

# --- 3. Gmail + Calendar integration — ON HOLD -------------------------------
# Kept in the repo but not started. Uncomment to re-enable:
# GC_PATTERN="python -m gmail_calendar.bot"
# if pgrep -f "$GC_PATTERN" > /dev/null; then
#     echo "[gmail-calendar] already running (pid $(pgrep -f "$GC_PATTERN" | head -1)) — skipped"
# else
#     nohup python -m gmail_calendar.bot >> /root/tg_sentinel/gmail_calendar_output.log 2>&1 &
#     disown
#     echo "[gmail-calendar] started (pid $!) — logs: gmail_calendar_output.log"
# fi

# --- Summary ------------------------------------------------------------------
echo ""
echo "=== Service status ==="
pgrep -af "$MAIN_PATTERN" > /dev/null \
    && echo "main-bot:               running (pid $(pgrep -f "$MAIN_PATTERN" | head -1))" \
    || echo "main-bot:               NOT running"
pgrep -af "$PMS_PATTERN" > /dev/null \
    && echo "partner-summarisation:  running (pid $(pgrep -f "$PMS_PATTERN" | head -1))" \
    || echo "partner-summarisation:  NOT running"
pgrep -af "python -m gmail_calendar.bot" > /dev/null \
    && echo "gmail-calendar:         running" \
    || echo "gmail-calendar:         not running (on hold)"
echo "luma-reminder:          cron-managed (not managed here)"
