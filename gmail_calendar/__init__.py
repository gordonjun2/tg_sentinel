"""Gmail + Google Calendar integration for tg_sentinel.

Long-running polling service that:
  * Detects new Gmail emails via Cloud Pub/Sub pull + history.list
  * Scores email importance with Gemini (OpenAI fallback) and forwards
    important emails to the Telegram admin group
  * Incrementally syncs all calendars on the account via events.list
  * Sends a 30-minute heads-up Telegram reminder before each meeting

Entry point: ``python -m gmail_calendar.bot``
"""
