"""Supabase (Postgres) storage for user data: survey/approval state (`users`)
and Telegram group membership (`group_members`).

Everything else the bot persists stays in SQLite (see database.py). Tables live
in their own schema (``DB_SCHEMA``, default ``tg_sentinel``) so other services
sharing the Supabase project never collide with them. Row-level security is
enabled with no policies, so the tables are not readable through Supabase's
public API; the bot connects as the database owner, which bypasses RLS.

One lazily-opened connection guarded by a lock (the bot is single-process),
reopened transparently if the pooler drops it.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Callable, List, Optional, TypeVar

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from user_models import UserData, UserState

T = TypeVar("T")
_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DDL = [
    "CREATE SCHEMA IF NOT EXISTS {schema}",
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        state TEXT NOT NULL,
        current_question INTEGER NOT NULL DEFAULT 0,
        answers JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        join_datetime TIMESTAMPTZ NOT NULL DEFAULT now(),
        invite_links JSONB NOT NULL DEFAULT '[]'::jsonb,
        approval_group TEXT,
        rejection_message_id BIGINT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_members (
        user_id BIGINT NOT NULL,
        telegram_group_id BIGINT NOT NULL,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        is_bot BOOLEAN NOT NULL DEFAULT FALSE,
        status TEXT NOT NULL DEFAULT 'member',
        first_seen_at TIMESTAMPTZ NOT NULL,
        last_seen_at TIMESTAMPTZ NOT NULL,
        left_at TIMESTAMPTZ,
        welcomed BOOLEAN NOT NULL DEFAULT FALSE,
        PRIMARY KEY (user_id, telegram_group_id)
    )
    """,
    "ALTER TABLE users ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE group_members ENABLE ROW LEVEL SECURITY",
]

_USER_COLS = (
    "user_id, username, state, current_question, answers, join_datetime, "
    "invite_links, approval_group, rejection_message_id"
)


class UserStore:
    def __init__(self, dsn: Optional[str], schema: str = "tg_sentinel"):
        if not _SCHEMA_RE.match(schema):
            raise ValueError(f"Invalid schema name: {schema!r}")
        self._dsn = dsn
        self._schema = schema
        self._conn: Optional[psycopg.Connection] = None
        self._lock = threading.RLock()

    # -- connection handling -------------------------------------------------

    def _connect(self) -> psycopg.Connection:
        if not self._dsn:
            raise RuntimeError(
                "SUPABASE_DB_URL is not set; user data lives in Supabase"
            )
        conn = psycopg.connect(
            self._dsn, autocommit=True, row_factory=dict_row, connect_timeout=15
        )
        schema = sql.Identifier(self._schema)
        for stmt in _DDL:
            conn.execute(sql.SQL(stmt).format(schema=schema))
            if stmt.startswith("CREATE SCHEMA"):
                conn.execute(sql.SQL("SET search_path TO {}").format(schema))
        return conn

    def _run(self, fn: Callable[[psycopg.Connection], T]) -> T:
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None or self._conn.closed:
                        self._conn = self._connect()
                    return fn(self._conn)
                except psycopg.OperationalError:
                    try:
                        if self._conn is not None:
                            self._conn.close()
                    finally:
                        self._conn = None
                    if attempt == 2:
                        raise
            raise AssertionError("unreachable")

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -- users ---------------------------------------------------------------

    @staticmethod
    def _user_from_row(r: dict) -> UserData:
        return UserData(
            user_id=r["user_id"],
            username=r["username"],
            state=UserState(r["state"]),
            current_question=r["current_question"],
            answers=r["answers"] or {},
            join_datetime=r["join_datetime"],
            invite_links=r["invite_links"] or [],
            approval_group=r["approval_group"],
            rejection_message_id=r["rejection_message_id"],
        )

    def get_user(self, user_id: int) -> Optional[UserData]:
        row = self._run(
            lambda c: c.execute(
                f"SELECT {_USER_COLS} FROM users WHERE user_id = %s", (user_id,)
            ).fetchone()
        )
        return self._user_from_row(row) if row else None

    def create_user(self, user_id: int, username: Optional[str]) -> UserData:
        user = UserData(
            user_id=user_id,
            username=username,
            state=UserState.IDLE,
            current_question=0,
            answers={},
            join_datetime=datetime.now(timezone.utc),
            invite_links=[],
            rejection_message_id=None,
        )
        self._run(
            lambda c: c.execute(
                """
                INSERT INTO users (user_id, username, state, current_question,
                    answers, join_datetime, invite_links, approval_group,
                    rejection_message_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user.user_id, user.username, user.state.value,
                    user.current_question, Jsonb(user.answers),
                    user.join_datetime, Jsonb(user.invite_links),
                    user.approval_group, user.rejection_message_id,
                ),
            )
        )
        return user

    def update_user(self, u: UserData) -> None:
        self._run(
            lambda c: c.execute(
                """
                UPDATE users SET username = %s, state = %s, current_question = %s,
                    answers = %s, join_datetime = %s, invite_links = %s,
                    approval_group = %s, rejection_message_id = %s
                WHERE user_id = %s
                """,
                (
                    u.username, u.state.value, u.current_question,
                    Jsonb(u.answers), u.join_datetime, Jsonb(u.invite_links or []),
                    u.approval_group, u.rejection_message_id, u.user_id,
                ),
            )
        )

    def get_pending_requests(self) -> List[UserData]:
        rows = self._run(
            lambda c: c.execute(
                f"SELECT {_USER_COLS} FROM users WHERE state = %s",
                (UserState.PENDING_APPROVAL.value,),
            ).fetchall()
        )
        return [self._user_from_row(r) for r in rows]

    def get_all_users(self) -> List[UserData]:
        rows = self._run(
            lambda c: c.execute(f"SELECT {_USER_COLS} FROM users").fetchall()
        )
        return [self._user_from_row(r) for r in rows]

    def delete_pending_approval_users(self) -> int:
        return self._run(
            lambda c: c.execute(
                "DELETE FROM users WHERE state = %s",
                (UserState.PENDING_APPROVAL.value,),
            ).rowcount
        )

    # -- group members -------------------------------------------------------

    def count_group_members(self, group_id: int) -> int:
        return self._run(
            lambda c: c.execute(
                "SELECT COUNT(*) AS n FROM group_members WHERE telegram_group_id = %s",
                (group_id,),
            ).fetchone()["n"]
        )

    def upsert_group_member(
        self,
        group_id: int,
        user_id: int,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
        is_bot: bool = False,
        status: str = "member",
        welcomed: bool = False,
    ) -> bool:
        """Insert or refresh a member. True when the user is new to the group
        or rejoining after leaving (a welcome is due); `welcomed` applies only
        in those cases."""
        now = datetime.now(timezone.utc)

        def op(c: psycopg.Connection) -> bool:
            with c.transaction():
                row = c.execute(
                    "SELECT left_at IS NOT NULL AS was_left FROM group_members "
                    "WHERE user_id = %s AND telegram_group_id = %s FOR UPDATE",
                    (user_id, group_id),
                ).fetchone()
                if row is None:
                    c.execute(
                        """
                        INSERT INTO group_members
                            (user_id, telegram_group_id, username, first_name,
                             last_name, is_bot, status, first_seen_at,
                             last_seen_at, welcomed)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (user_id, group_id, username, first_name, last_name,
                         is_bot, status, now, now, welcomed),
                    )
                    return True
                rejoined = row["was_left"]
                c.execute(
                    """
                    UPDATE group_members
                    SET username = %s, first_name = %s, last_name = %s,
                        is_bot = %s, status = %s, last_seen_at = %s,
                        left_at = NULL,
                        welcomed = CASE WHEN %s THEN %s ELSE welcomed END
                    WHERE user_id = %s AND telegram_group_id = %s
                    """,
                    (username, first_name, last_name, is_bot, status, now,
                     rejoined, welcomed, user_id, group_id),
                )
                return rejoined

        return self._run(op)

    def mark_group_member_left(
        self, group_id: int, user_id: int, status: str = "left"
    ) -> None:
        self._run(
            lambda c: c.execute(
                """
                UPDATE group_members SET status = %s, left_at = now()
                WHERE user_id = %s AND telegram_group_id = %s AND left_at IS NULL
                """,
                (status, user_id, group_id),
            )
        )

    def mark_absent_members_left(self, group_id: int, present_ids: set) -> int:
        return self._run(
            lambda c: c.execute(
                """
                UPDATE group_members SET status = 'left', left_at = now()
                WHERE telegram_group_id = %s AND left_at IS NULL
                  AND NOT (user_id = ANY(%s))
                """,
                (group_id, list(present_ids)),
            ).rowcount
        )

    def get_unwelcomed_members(self, group_id: int) -> List[dict]:
        return self._run(
            lambda c: c.execute(
                """
                SELECT user_id, username, first_name, last_name
                FROM group_members
                WHERE telegram_group_id = %s AND NOT welcomed
                  AND NOT is_bot AND left_at IS NULL
                ORDER BY first_seen_at
                """,
                (group_id,),
            ).fetchall()
        )

    def mark_members_welcomed(self, group_id: int, user_ids: List[int]) -> None:
        if not user_ids:
            return
        self._run(
            lambda c: c.execute(
                """
                UPDATE group_members SET welcomed = TRUE
                WHERE telegram_group_id = %s AND user_id = ANY(%s)
                """,
                (group_id, list(user_ids)),
            )
        )
