#!/usr/bin/env python3
"""Copy `users` and `group_members` from the SQLite bot_data.db into Supabase.

Idempotent: rows already in Supabase are left alone (ON CONFLICT DO NOTHING),
so it is safe to re-run before cutover. The SQLite tables are not modified.

    python scripts/migrate_user_data_to_supabase.py [--dry-run]
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402,F401  (loads .env)
from psycopg.types.json import Jsonb  # noqa: E402
from user_store import UserStore  # noqa: E402

SQLITE = ROOT / "data" / "bot_data.db"


def ts(value):
    return datetime.fromisoformat(value) if value else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = sqlite3.connect(f"file:{SQLITE}?mode=ro", uri=True)
    users = src.execute(
        "SELECT user_id, username, state, current_question, answers, join_datetime,"
        " invite_links, approval_group, rejection_message_id FROM users"
    ).fetchall()
    members = src.execute(
        "SELECT user_id, telegram_group_id, username, first_name, last_name, is_bot,"
        " status, first_seen_at, last_seen_at, left_at, welcomed FROM group_members"
    ).fetchall()
    print(f"SQLite: users={len(users)} group_members={len(members)}")
    if args.dry_run:
        return

    store = UserStore(os.getenv("SUPABASE_DB_URL"), os.getenv("DB_SCHEMA", "tg_sentinel"))

    def copy(c):
        with c.transaction():
            for r in users:
                c.execute(
                    "INSERT INTO users VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (user_id) DO NOTHING",
                    (r[0], r[1], r[2], r[3], Jsonb(json.loads(r[4] or "{}")),
                     ts(r[5]) or datetime.now(timezone.utc),
                     Jsonb(json.loads(r[6] or "[]")), r[7], r[8]),
                )
            for r in members:
                c.execute(
                    "INSERT INTO group_members (user_id, telegram_group_id, username,"
                    " first_name, last_name, is_bot, status, first_seen_at, last_seen_at,"
                    " left_at, welcomed) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (user_id, telegram_group_id) DO NOTHING",
                    (r[0], r[1], r[2], r[3], r[4], bool(r[5]), r[6], ts(r[7]),
                     ts(r[8]), ts(r[9]), bool(r[10])),
                )
        return (
            c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"],
            c.execute("SELECT COUNT(*) AS n FROM group_members").fetchone()["n"],
        )

    n_users, n_members = store._run(copy)
    print(f"Supabase: users={n_users} group_members={n_members}")
    ok = n_users >= len(users) and n_members >= len(members)
    print("OK: all rows present" if ok else "MISMATCH: investigate before cutover")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
