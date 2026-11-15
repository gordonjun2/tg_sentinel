# tg_sentinel CI/CD + systemd Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GitHub CI on every `main` push/PR, and a manual deploy workflow that always syncs code + deps on the VPS, with an optional clean restart of the two long-running bots via systemd.

**Architecture:** Mirror serendip-tg's "CI automatic, CD manual" contract (`appleboy/ssh-action` + `SERVER_*` secrets). Move main bot and partner summarisation from nohup/`pgrep` to two systemd units that are the **only** allowed starters. Deploy with `restart_long_running=true` runs `systemctl stop` → git sync → `pip install` → `systemctl start` → `is-active` checks. Deploy with the flag off skips stop/start so cron picks up new code without bouncing Telegram listeners. Session-file exclusivity is enforced by structure (one unit per process), not by README warnings.

**Tech Stack:** GitHub Actions, `appleboy/ssh-action`, systemd, existing `venv` + `requirements.txt`, bash deploy helper on the VPS.

**Decisions locked (2026-11-15):**
- Reach VPS via SSH from hosted Actions (same pattern as serendip `docs/DEPLOYMENT.md` Step 3).
- Every manual deploy: `git` sync to `origin/main` + `venv/bin/pip install -r requirements.txt`.
- Optional restart checkbox (default **false**).
- Process supervision: **systemd now** (exclusive owner of the two long-runners). Best practice for stop-before-start / Pyrogram `.session` safety.
- Cron jobs stay cron. Gmail/Calendar stays on hold.
- Separate deploy key from serendip (`~/.ssh/tg_sentinel_deploy`).

---

## File structure

| Path | Responsibility |
|------|----------------|
| `.github/workflows/ci.yml` | Lint/import sanity + pytest on PR and push to `main`. Never deploys. |
| `.github/workflows/deploy.yml` | `workflow_dispatch` only. Input `restart_long_running` (boolean, default false). SSH → `scripts/deploy.sh`. |
| `deploy/systemd/tg-sentinel-bot.service` | Unit for `bot.py`. |
| `deploy/systemd/tg-sentinel-partner.service` | Unit for `python -m partner_message_summarisation.bot`. |
| `scripts/deploy.sh` | Idempotent server-side deploy: sync, pip, optional systemd stop/start, health checks. |
| `start_all.sh` | Thin wrapper: `systemctl start` both units (no nohup). Safe to re-run. |
| `bot_script.sh` | Thin wrapper: `systemctl start tg-sentinel-bot`. |
| `partner_message_summarisation/partner_message_summarisation_bot.sh` | Thin wrapper: `systemctl start tg-sentinel-partner`. |
| `docs/DEPLOYMENT.md` | Operator runbook: SSH key, secrets, first unit install, how to Run workflow. |
| `README.md` | Point Running/restart section at systemd + link `docs/DEPLOYMENT.md`. |
| `partner_message_summarisation/README.md` | Remove "systemd backlog"; document unit as the supported runner. |

---

### Task 1: systemd unit files (exclusive ownership)

**Files:**
- Create: `deploy/systemd/tg-sentinel-bot.service`
- Create: `deploy/systemd/tg-sentinel-partner.service`

- [ ] **Step 1: Write main bot unit**

```ini
[Unit]
Description=tg_sentinel main Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/tg_sentinel
EnvironmentFile=-/root/tg_sentinel/.env
ExecStart=/root/tg_sentinel/venv/bin/python bot.py
Restart=on-failure
RestartSec=5
KillMode=control-group
TimeoutStopSec=30
StandardOutput=append:/root/tg_sentinel/script_tg_sentinel_output.log
StandardError=append:/root/tg_sentinel/script_tg_sentinel_output.log

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write partner summarisation unit**

Same shape with:
- `Description=tg_sentinel partner message summarisation`
- `ExecStart=/root/tg_sentinel/venv/bin/python -m partner_message_summarisation.bot`
- Logs → `/root/tg_sentinel/partner_message_summarisation_output.log`

Notes for implementers:
- Do **not** use `Restart=always` unless you also accept respawn during intentional maintenance. `on-failure` is enough for crash recovery. Reboot coverage comes from `enable`.
- `systemctl stop` disables restart until `start`; that is the stop-before-start guarantee.
- `EnvironmentFile` uses systemd syntax (no `export`, careful with quotes). If `.env` has shell-only syntax, keep loading via the Python app’s existing dotenv path and omit `EnvironmentFile`, or sanitize `.env` for systemd. Prefer keeping app-side dotenv if the current `.env` is shell-flavored.
- WorkingDirectory must be the repo root so `python -m partner_message_summarisation.bot` resolves.

- [ ] **Step 3: Document one-time VPS install in `docs/DEPLOYMENT.md` (draft stub OK until Task 5)**

```bash
# After killing any leftover nohup processes once:
pkill -f 'python bot.py' || true
pkill -f 'python -m partner_message_summarisation.bot' || true
# wait until pgrep is empty, then:
cp deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tg-sentinel-bot tg-sentinel-partner
systemctl status tg-sentinel-bot tg-sentinel-partner
```

Remove any `@reboot bash .../start_all.sh` crontab line so systemd is the only boot starter.

---

### Task 2: Retire nohup starters (migrate callers, delete dual path)

**Files:**
- Modify: `start_all.sh`
- Modify: `bot_script.sh`
- Modify: `partner_message_summarisation/partner_message_summarisation_bot.sh`

- [ ] **Step 1: Rewrite `start_all.sh` as systemctl wrapper**

Idempotent: `systemctl start tg-sentinel-bot tg-sentinel-partner` then print `systemctl is-active` for both. No `nohup`, no `pgrep` skip logic (systemd is already idempotent on `start`).

- [ ] **Step 2: Rewrite solo scripts** to `systemctl start` their unit only.

- [ ] **Step 3: Grep for remaining nohup launches of these two processes** and delete or redirect them. Cron must not start the long-runners.

---

### Task 3: Server deploy script

**Files:**
- Create: `scripts/deploy.sh`

- [ ] **Step 1: Implement `scripts/deploy.sh`**

Contract:

```bash
usage: scripts/deploy.sh [--restart]
```

Behavior (always):
1. `set -euo pipefail`
2. `cd` to repo root (`SERVER_PROJECT_DIR` / `/root/tg_sentinel`)
3. `git fetch origin main && git reset --hard origin/main` (match serendip: dirty local edits on the VPS are not sacred; `.env` and `*.session` are gitignored and survive)
4. `venv/bin/pip install -r requirements.txt`
5. If `--restart`:
   - `systemctl stop tg-sentinel-bot tg-sentinel-partner`
   - Assert both inactive (`systemctl is-active` fails / `is-active --quiet` false)
   - `systemctl start tg-sentinel-bot tg-sentinel-partner`
   - Assert both `active`
6. Print `git rev-parse --short HEAD` and unit status
7. Exit non-zero on any failed assert

Without `--restart`: leave units running (in-memory code stays old until a later restart). Cron scripts still see new files on disk.

- [ ] **Step 2: Make executable** (`chmod +x` in git)

---

### Task 4: GitHub Actions workflows

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/deploy.yml`

- [ ] **Step 1: CI**

```yaml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt
      - name: Compile check
        run: python -m compileall -q -x '(venv|openspec|\.git)' .
      - name: Pytest (unit, no live network)
        run: python -m pytest -q
          tests/
          partner_message_summarisation/tests/
          linkedin/tests/
          --ignore=partner_message_summarisation/tests  # adjust if DB-required tests need markers
```

Implementers must inspect existing tests and skip/mark DB- or network-bound tests so CI stays green without secrets. Prefer `pytest` markers already in the repo; add `requires_db` / skipif if needed. Do not call live Telegram/LLM APIs in CI.

- [ ] **Step 2: Deploy (manual only)**

```yaml
name: Deploy

on:
  workflow_dispatch:
    inputs:
      restart_long_running:
        description: "Stop systemd units cleanly, then start after sync (bots + partner listener)"
        required: true
        type: boolean
        default: false

concurrency:
  group: deploy-production
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    environment: production
    steps:
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1
        env:
          RESTART: ${{ inputs.restart_long_running }}
        with:
          host: ${{ secrets.SERVER_HOST }}
          username: ${{ secrets.SERVER_USER }}
          key: ${{ secrets.SERVER_SSH_KEY }}
          envs: RESTART
          script: |
            set -euo pipefail
            cd ${{ secrets.SERVER_PROJECT_DIR }}
            if [ "$RESTART" = "true" ]; then
              bash scripts/deploy.sh --restart
            else
              bash scripts/deploy.sh
            fi
```

Secrets (set on the **tg_sentinel** GitHub repo, separate key from serendip):

| Secret | Example |
|--------|---------|
| `SERVER_HOST` | VPS IP/hostname |
| `SERVER_USER` | `root` (or deploy user that can `systemctl` these units) |
| `SERVER_SSH_KEY` | private ed25519 key |
| `SERVER_PROJECT_DIR` | `/root/tg_sentinel` |

VPS git remote must be able to `fetch origin main` (deploy key or HTTPS credential on the server, same class of setup as serendip).

---

### Task 5: Operator docs

**Files:**
- Create: `docs/DEPLOYMENT.md`
- Modify: `README.md`
- Modify: `partner_message_summarisation/README.md`

- [ ] **Step 1: Write `docs/DEPLOYMENT.md`** modeled on serendip Step 3, but for this stack:

1. Create `~/.ssh/tg_sentinel_deploy` (no passphrase).
2. `ssh-copy-id` to the VPS; prove `ssh ... 'echo ok'`.
3. `gh secret set` the four secrets from the tg_sentinel repo root.
4. One-time: install units, kill leftover nohup PIDs, remove `@reboot start_all.sh`, `enable --now`.
5. Day-to-day: push/merge to `main` (CI). Actions → **Deploy** → Run workflow. Check **restart_long_running** only when bot/listener code must reload.
6. Prove: `systemctl is-active tg-sentinel-bot tg-sentinel-partner`, tail logs, optional Telegram smoke (`/start` or partner status module).

- [ ] **Step 2: Update README Running section** to systemd + link Deployment doc. Document restart as `systemctl restart tg-sentinel-bot` (or Deploy workflow with checkbox).

- [ ] **Step 3: Partner README** — mark systemd as the supported runner; delete the backlog bullet that promised it later.

---

### Task 6: Verification

- [ ] **Step 1: Local**

```bash
# from tg_sentinel/
python -m compileall -q -x '(venv|openspec|\.git)' .
python -m pytest -q  # with CI-equivalent skips
```

- [ ] **Step 2: VPS dry run (manual)**

1. Install units, migrate off nohup once.
2. `bash scripts/deploy.sh` (no restart) → confirm bots stay up, `HEAD` advances.
3. `bash scripts/deploy.sh --restart` → confirm brief stop, both `active`, no session lock errors in logs.
4. Trigger GitHub **Deploy** twice (restart off, then on) from a green `main`.

- [ ] **Step 3: Failure modes to confirm**

- Second overlapping Deploy queues (concurrency group), does not interleave stop/start.
- `deploy.sh --restart` fails loud if a unit will not become `active`.
- No leftover `nohup python bot.py` under `pgrep`.

---

## Out of scope

- Docker / GHCR (serendip-only).
- systemd for cron jobs (luma, linkedin).
- Bringing gmail_calendar back online.
- Auto-deploy on every push to `main`.
- Flower / complex health HTTP endpoints.

---

## Principles that shaped this plan

| Principle | Choice it forced |
|-----------|------------------|
| Separate before serializing shared state | One systemd unit owns each process that holds a `.session`; no parallel nohup starter. |
| Make operations idempotent | `deploy.sh` and `systemctl start` converge; re-run safe. |
| Redesign from first principles | CD assumes supervised processes from day one, not a bolted-on stop script around nohup. |
| Outcome-oriented execution | Migrate to systemd in this plan; do not ship a temporary stop_long_running.sh forever. |
| Laziness protocol | No dual nohup+systemd path; wrappers call systemctl only. |
| Encode lessons in structure | Session exclusivity lives in unit ownership + deploy stop/start, not prose. |
