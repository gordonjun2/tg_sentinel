# tg_sentinel deployment

CI runs on every push and pull request to `main`. Deploy is **manual only**.

You deploy from GitHub → Actions → **Deploy** → **Run workflow**.

Long-running processes are owned by systemd:

| Unit | Process |
|------|---------|
| `tg-sentinel-bot` | `python bot.py` |
| `tg-sentinel-partner` | `python -m partner_message_summarisation.bot` |

Cron jobs (`luma_reminder`, LinkedIn forwarder) are not restarted by deploy. They pick up new code on the next cron run after a sync.

---

## Step 1 — One-time: systemd on the VPS

Repo path on the server is `/root/tg_sentinel` (adjust if yours differs, and mirror that in `SERVER_PROJECT_DIR`).

1. Pull this branch (or `main` once merged) so `deploy/systemd/*.service` exists.
2. Stop any leftover nohup processes so they cannot fight Pyrogram session files:

```bash
pkill -f 'python bot.py' || true
pkill -f 'python -m partner_message_summarisation.bot' || true
# Wait until both are gone:
while pgrep -f 'python bot.py' >/dev/null || pgrep -f 'python -m partner_message_summarisation.bot' >/dev/null; do
  sleep 1
done
```

3. Install and enable units:

```bash
cd /root/tg_sentinel
cp deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tg-sentinel-bot tg-sentinel-partner
systemctl status tg-sentinel-bot tg-sentinel-partner
```

4. Remove any `@reboot bash /root/tg_sentinel/start_all.sh` line from crontab. systemd `enable` covers reboot. Dual starters cause session races.

5. Confirm `.env` and `partner_message_summarisation/data/sentinel_listener.session` are present (gitignored). Apps load `.env` via python-dotenv; the units do not use systemd `EnvironmentFile`.

---

## Step 2 — Connect GitHub Actions to the VPS

### 2.1 Create a deploy SSH key (on your Mac)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/tg_sentinel_deploy -C "github-actions-tg-sentinel" -N ""
```

No passphrase. Actions cannot type one. Use a **separate** key from serendip’s deploy key.

### 2.2 Authorize the key on the VPS

```bash
ssh-copy-id -i ~/.ssh/tg_sentinel_deploy.pub <user>@<SERVER_IP>
ssh -i ~/.ssh/tg_sentinel_deploy <user>@<SERVER_IP> 'echo ok && systemctl is-active tg-sentinel-bot'
```

You should see `ok` and `active` (after Step 1).

The SSH user must be allowed to run `systemctl` for these units (typically `root`, or a user with the right polkit/sudo rules).

### 2.3 Store GitHub secrets

From the **tg_sentinel** repo root on your Mac:

```bash
gh secret set SERVER_HOST        # paste IP or hostname (no ssh://, port 22)
gh secret set SERVER_USER        # paste SSH user
gh secret set SERVER_SSH_KEY < ~/.ssh/tg_sentinel_deploy
gh secret set SERVER_PROJECT_DIR # paste absolute path, e.g. /root/tg_sentinel
```

Confirm:

```bash
gh secret list
```

### 2.4 VPS git fetch access

`scripts/deploy.sh` runs `git fetch origin main` and `git reset --hard origin/main`. The clone on the VPS needs credentials to fetch (deploy key or HTTPS token). `.env` and `*.session` are gitignored and survive the reset.

---

## Step 3 — Day-to-day deploy

1. Merge (or push) a green `main` commit. CI must pass first.
2. GitHub → Actions → **Deploy** → **Run workflow**.
3. Leave **restart_long_running** unchecked for cron-only / on-disk code updates.
4. Check **restart_long_running** when main bot or partner listener code must reload.

What the job always does:

1. SSH into the VPS.
2. `git fetch` + `reset --hard origin/main`.
3. `venv/bin/pip install -r requirements.txt`.

When restart is checked, it also:

4. `systemctl stop tg-sentinel-bot tg-sentinel-partner` (waits until inactive).
5. `systemctl start` both units.
6. Fails the job if either unit is not `active`.

### Prove it worked

```bash
ssh -i ~/.ssh/tg_sentinel_deploy <user>@<SERVER_IP>
cd /root/tg_sentinel
git rev-parse --short HEAD
systemctl is-active tg-sentinel-bot tg-sentinel-partner
tail -n 50 script_tg_sentinel_output.log
tail -n 50 partner_message_summarisation_output.log
```

Optional: Telegram smoke (`/start` on the bot) or `python -m partner_message_summarisation.status`.

---

## Manual helpers on the VPS

```bash
bash start_all.sh                          # systemctl start both units
bash scripts/deploy.sh                     # sync + pip, no restart
bash scripts/deploy.sh --restart           # sync + pip + clean restart
systemctl restart tg-sentinel-bot          # bounce main bot only
systemctl restart tg-sentinel-partner      # bounce partner listener only
systemctl status tg-sentinel-bot tg-sentinel-partner
```

---

## Rollback

1. Actions → Deploy is always from current `main`. To roll back code, revert or reset `main` on GitHub, then Run workflow with **restart_long_running** checked.
2. Or on the VPS: `git fetch` + `git reset --hard <good-sha>` + `bash scripts/deploy.sh --restart` (edit temporarily) / `systemctl restart` both units after a manual checkout.
