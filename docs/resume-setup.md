# Resuming Setup — Next Steps

You paused here: implementation is **done** and tested, OAuth client setup is
**pending**. Pick up from step 1 below.

## Current state (already complete)

- All code in `gmail_calendar/` — built, lint-clean, 43 tests passing
- `config.py`, `.env.example`, `.gitignore`, `requirements.txt` updated
- Deployment script: `gmail_calendar/gmail_calendar_bot.sh`
- Full GCP reference: `docs/gcp-setup.md`
- Default OAuth client file is **`gmail_cal_credentials.json`** (separate
  from the Drive uploader's `credentials.json` — they don't share anything)

## Resume from here

### Step 1 — Create a dedicated OAuth client

<https://console.cloud.google.com/apis/credentials>

1. **+ CREATE CREDENTIALS** → **OAuth client ID**
2. Application type: **Desktop app**
3. Name: `tg-sentinel-gmail-cal` (anything memorable)
4. Click **Create**
5. Click **Download JSON**
6. Save the download to: **`/Users/gordonjun/Desktop/Projects/tg_sentinel/gmail_cal_credentials.json`**

### Step 2 — Add the test user

The app is in **"Testing"** status, so only emails on the test-users list
can authorize.

<https://console.cloud.google.com/apis/credentials/consent>

1. Open the **"Audience"** tab
2. Under **"Test users"** → **+ ADD USERS**
3. Paste `hello@sisc.club`
4. **Save** — wait ~30s for propagation

(While the app stays in Testing mode, refresh tokens expire after 7 days.
For personal use that's fine — just re-run Step 4 if the VPS ever stops
refreshing. If it gets annoying, click **"Publish app"** on the same screen;
Google only enforces formal verification at scale.)

### Step 3 — Configure `.env`

Add this line to `/Users/gordonjun/Desktop/Projects/tg_sentinel/.env`:

```env
GOOGLE_GMAIL_CAL_PROJECT_ID=<your-gcp-project-id>
```

(Get the project ID from the GCP Console top bar — looks like
`tg-sentinel-12345`.) All other Gmail/Calendar vars have working defaults.

### Step 4 — Run OAuth locally (one time)

```bash
cd /Users/gordonjun/Desktop/Projects/tg_sentinel
source venv/bin/activate
pip install -r requirements.txt          # picks up google-cloud-pubsub
python -m gmail_calendar.auth
```

Browser opens → pick `hello@sisc.club` → approve both scopes (Gmail + Calendar).

Expected output:

```
[ok] Gmail authorized for: hello@sisc.club
[ok] Calendar authorized — visible calendars: [...]
[ok] Token written to: /Users/gordonjun/Desktop/Projects/tg_sentinel/gmail_cal_token.json
```

If you see "Access blocked" again, Step 2 didn't propagate — wait 60s and retry.

### Step 5 — Sanity-check locally (optional but worth it)

Run the service in the foreground for 2 minutes to confirm loops work end-to-end:

```bash
python -m gmail_calendar.bot
```

Within ~60s you should see log lines like:

```
Watch renewal due — calling users.watch()
Watch renewed — expiration=...
Calendar sync touched N events across {...}
```

Press Ctrl+C to stop. Confirm the SQLite DB has data:

```bash
sqlite3 gmail_calendar/data/gmail_calendar.db "SELECT COUNT(*) FROM calendar_events;"
```

### Step 6 — Ship to VPS

```bash
# From your laptop
scp gmail_cal_token.json root@<vps>:/root/tg_sentinel/
scp -r gmail_calendar/ root@<vps>:/root/tg_sentinel/
scp config.py requirements.txt .env root@<vps>:/root/tg_sentinel/
```

(You do **not** need to ship `gmail_cal_credentials.json` — the refresh token
in `gmail_cal_token.json` is enough.)

### Step 7 — Start on VPS

```bash
ssh root@<vps>
cd /root/tg_sentinel
source venv/bin/activate
pip install -r requirements.txt
bash gmail_calendar/gmail_calendar_bot.sh
tail -f gmail_calendar_output.log
```

To stop later: `pkill -f "python -m gmail_calendar.bot"`

## Reference

- **Full GCP setup (Pub/Sub topic + subscription + grants)**: `docs/gcp-setup.md`
- **Architecture + tuning**: `gmail_calendar/README.md`
- **Original plan**: `docs/gmail-calendar-integration-plan.md`

## Verification checklist (do this after Step 7)

- [ ] `gmail_calendar_output.log` shows `Watch renewed — expiration=...`
- [ ] Within 60s: `Calendar sync touched N events` line appears
- [ ] Create a calendar event 25 min from now → reminder fires in admin group
- [ ] Send yourself a real (non-no-reply) email → forwarded to admin group within 60s
- [ ] Marketing/newsletter emails do NOT get forwarded (pre-filter + LLM both gate them)

## If something breaks

| Symptom | Look at |
|---|---|
| 403 / access_denied | Step 2 (test user) — wait 60s, retry |
| `Pub/Sub pull failed: 403` | Pub/Sub subscription missing or wrong name — see `docs/gcp-setup.md` steps 5-7 |
| `Refresh failed` on VPS | Re-run Step 4 locally, re-SCP `gmail_cal_token.json` |
| No emails forwarded | Lower `GMAIL_IMPORTANCE_THRESHOLD=0.3` in `.env` to debug |
| No reminders fire | Check `sqlite3 ... 'SELECT * FROM calendar_events WHERE reminder_sent=0'` |
| Tests fail locally | `python -m pytest gmail_calendar/tests/` — should be 43 passed |
