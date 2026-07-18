# GCP Setup Checklist — Gmail + Calendar Integration

One-time manual setup. After completing this, no further GCP work is needed.

## 1. Pick / create the GCP project

Use the same project that owns your existing `credentials.json` if you want
to reuse it. Otherwise create a new project at
<https://console.cloud.google.com/>.

Record the **Project ID** (e.g. `tg-sentinel-12345`) — this is
`GOOGLE_GMAIL_CAL_PROJECT_ID` in `.env`.

## 2. Enable APIs

In APIs & Services → Library, enable:

- **Gmail API**
- **Google Calendar API**
- **Cloud Pub/Sub API**

## 3. Configure OAuth consent screen

APIs & Services → OAuth consent screen:

1. Choose **External** (or Internal if you're on Workspace).
2. Add scopes:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/calendar.readonly`
3. Add yourself as a **test user** (required while the app is in "Testing"
   status — Verification is not needed for personal use).

## 4. Create OAuth credentials for this integration

This integration uses its **own** OAuth client (separate from the Drive
uploader's `credentials.json`) so the two never share config.

APIs & Services → Credentials → **Create Credentials** → **OAuth client ID**:

1. Application type: **Desktop app**
2. Name: `tg-sentinel-gmail-cal` (anything descriptive)
3. Click **Create**
4. Click **Download JSON**
5. Save the downloaded file as **`gmail_cal_credentials.json`** in the
   project root (next to the existing `credentials.json`).

The integration reads from `gmail_cal_credentials.json` by default; the
existing Drive uploader continues to read its own `credentials.json`
unchanged.

> The OAuth client itself isn't scoped — the scopes you request
> (`gmail.readonly` + `calendar.readonly`) are bound at runtime in
> `gmail_calendar/auth.py`.

## 5. Create the Pub/Sub topic

Cloud Pub/Sub → Topics → **Create Topic**:

- Topic ID: `gmail-events` (or anything — must match `GOOGLE_PUBSUB_TOPIC`)
- Leave encryption as Google-managed

## 6. Grant Gmail permission to publish

On the topic → Permissions → **Add principal**:

- New principals: `gmail-api-push@system.gserviceaccount.com`
- Role: **Pub/Sub Publisher**

Without this grant, `users.watch()` returns 403 / 400.

## 7. Create the pull subscription

On the topic → Subscriptions → **Create Subscription**:

- Subscription ID: `gmail-events-pull` (or anything — match
  `GOOGLE_PUBSUB_SUBSCRIPTION`)
- Delivery type: **Pull**
- Acknowledgement deadline: **60 seconds**
- Retention: 7 days (default is fine)

> Do **not** choose "Push" — that requires a public HTTPS endpoint on your
> VPS with a valid SSL cert. Pull is what lets us run on a plain VPS.

## 8. Authorize the app (run on your laptop)

```bash
# from project root, venv active
pip install -r requirements.txt
python -m gmail_calendar.auth
```

A browser opens. Pick the Google account, approve both scopes.

Expected output:

```
[ok] Gmail authorized for: you@example.com
[ok] Calendar authorized — visible calendars: ['you@example.com', 'work@...']
[ok] Token written to: /path/to/gmail_cal_token.json
```

## 9. Ship the token to the VPS

```bash
scp gmail_cal_token.json root@your-vps:/root/tg_sentinel/
scp -r gmail_calendar root@your-vps:/root/tg_sentinel/
```

(The `gmail_calendar/` folder ships your code; `gmail_cal_token.json` ships
the refresh token. Both are gitignored. You do **not** need to ship
`gmail_cal_credentials.json` to the VPS — the refresh token alone is enough.)

## 10. Start the service

```bash
ssh root@your-vps
cd /root/tg_sentinel
bash gmail_calendar/gmail_calendar_bot.sh
tail -f gmail_calendar_output.log
```

## Verifying it works

Within ~60s of starting:

- `gmail_calendar_output.log` should show
  `Watch renewal due — calling users.watch()` followed by
  `Watch renewed — expiration=...`.
- The next `Calendar sync touched N events` line tells you sync works.
- Send yourself a test email from a real address (not no-reply@). Within 60s
  an importance-scored message should appear in the admin group.
- Create a calendar event 25 minutes from now. Within 60–120s a reminder
  should fire.

## Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `Access blocked ... has not completed the Google verification process` | OAuth app in Testing mode and email not on test-users list | Step 3 — add the account email as a Test User |
| `watch()` returns 403 | Pub/Sub publisher grant missing | Step 6 |
| `Pub/Sub pull failed: 403` | Subscription doesn't exist or wrong name | Step 7 + `.env` |
| `Refresh failed` on VPS | OAuth didn't include `prompt=consent` (no refresh token) | Re-run step 8 (the bootstrap does this correctly) |
| Refresh token silently stops working after 7 days | App is in Testing mode — tokens expire weekly | Re-run `python -m gmail_calendar.auth` and re-SCP the token; or publish the app |
| No emails forwarded | Threshold too high or pre-filter is matching | Lower `GMAIL_IMPORTANCE_THRESHOLD` to `0.3` to debug |
| Same reminder fires twice | Same event on multiple calendars without `iCalUID` | Rare; manually dedup by inspecting `calendar_events` |
