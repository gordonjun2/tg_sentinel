import asyncio
import os
import uuid
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import sql

import config  # noqa: F401  (loads .env so SUPABASE_DB_URL is visible)
import user_sweep
from user_store import UserStore

G = -1001


@pytest.fixture
def database():
    """UserStore on a throwaway schema in the Supabase/Postgres DB, dropped
    afterwards. Skipped when no database is reachable."""
    dsn = os.getenv("USER_STORE_TEST_DSN") or os.getenv("SUPABASE_DB_URL")
    if not dsn:
        pytest.skip("no SUPABASE_DB_URL / USER_STORE_TEST_DSN")
    schema = f"tg_sentinel_test_{uuid.uuid4().hex[:8]}"
    store = UserStore(dsn, schema)
    try:
        store.count_group_members(0)  # connects + creates the schema
    except psycopg.OperationalError as e:
        pytest.skip(f"database unreachable: {e}")
    yield store
    store.close()
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def member(uid, username=None, first="F", bot=False, deleted=False, status="member"):
    user = SimpleNamespace(
        id=uid, username=username, first_name=first, last_name=None,
        is_bot=bot, is_deleted=deleted,
    )
    return SimpleNamespace(user=user, status=SimpleNamespace(value=status))


class FakeClient:
    def __init__(self, groups):
        self.groups = groups

    async def get_chat_members(self, gid):
        if isinstance(self.groups[gid], Exception):
            raise self.groups[gid]
        for m in self.groups[gid]:
            yield m


class FakeBot:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send_message(self, chat_id, text, parse_mode=None):
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append((chat_id, text))


def run_sweep(database, groups, bot, silent=False, monkeypatch=None):
    client = FakeClient(groups)

    async def get_client():
        return client

    asyncio.run(user_sweep.sweep_once(get_client, bot, database=database, silent=silent))


@pytest.fixture(autouse=True)
def one_group(monkeypatch):
    monkeypatch.setattr(user_sweep, "TARGET_GROUP_IDS", (G,))


def test_first_sweep_backfills_silently(database):
    bot = FakeBot()
    run_sweep(database, {G: [member(1, "a"), member(2, "b")]}, bot)
    assert database.count_group_members(G) == 2
    assert bot.sent == []
    assert database.get_unwelcomed_members(G) == []


def test_later_sweep_welcomes_only_new_members_once(database):
    bot = FakeBot()
    run_sweep(database, {G: [member(1, "a")]}, bot)
    run_sweep(database, {G: [member(1, "a"), member(2, "newbie"), member(3, bot=True)]}, bot)
    assert len(bot.sent) == 1 and "@newbie" in bot.sent[0][1]
    run_sweep(database, {G: [member(1, "a"), member(2, "newbie")]}, bot)
    assert len(bot.sent) == 1  # no repeat welcome


def test_failed_send_is_retried(database):
    run_sweep(database, {G: [member(1)]}, FakeBot())
    run_sweep(database, {G: [member(1), member(2, "x")]}, FakeBot(fail=True))
    assert [m["user_id"] for m in database.get_unwelcomed_members(G)] == [2]
    bot = FakeBot()
    run_sweep(database, {G: [member(1), member(2, "x")]}, bot)
    assert len(bot.sent) == 1


def test_realtime_join_is_welcomed_by_sweep(database):
    run_sweep(database, {G: [member(1)]}, FakeBot())
    database.upsert_group_member(G, 9, "rt", "R", None)
    bot = FakeBot()
    run_sweep(database, {G: [member(1), member(9, "rt")]}, bot)
    assert len(bot.sent) == 1 and "@rt" in bot.sent[0][1]


def test_leave_and_rejoin(database):
    run_sweep(database, {G: [member(1), member(2)]}, FakeBot())
    run_sweep(database, {G: [member(1)]}, FakeBot())
    assert database.get_unwelcomed_members(G) == []
    bot = FakeBot()
    run_sweep(database, {G: [member(1), member(2, "back")]}, bot)
    assert len(bot.sent) == 1  # rejoin is welcomed again


def test_enumeration_failure_keeps_data(database):
    run_sweep(database, {G: [member(1), member(2)]}, FakeBot())
    run_sweep(database, {G: RuntimeError("api down")}, FakeBot())
    run_sweep(database, {G: []}, FakeBot())  # empty listing must not wipe
    assert database.mark_absent_members_left(G, {1, 2}) == 0


def test_deleted_accounts_skipped(database):
    run_sweep(database, {G: [member(1), member(2, deleted=True)]}, FakeBot())
    assert database.count_group_members(G) == 1


def test_welcome_names_formatting():
    people = [
        SimpleNamespace(id=1, username="a_b", full_name="A"),
        SimpleNamespace(id=2, username=None, full_name="Bob [x]"),
    ]
    assert user_sweep.build_welcome_names(people) == "@a\\_b and [Bob \\[x]](tg://user?id=2)"


def test_user_roundtrip(database):
    from user_models import UserState

    u = database.create_user(9_000_000_001, "alice")
    assert database.get_user(9_000_000_001).state is UserState.IDLE
    u.state = UserState.PENDING_APPROVAL
    u.answers = {"0": "Alice", "1": "ACME"}
    u.invite_links = ["https://t.me/+x"]
    u.approval_group = "sub"
    database.update_user(u)
    got = database.get_user(9_000_000_001)
    assert got.answers == {"0": "Alice", "1": "ACME"}
    assert got.invite_links == ["https://t.me/+x"]
    assert got.approval_group == "sub"
    assert [x.user_id for x in database.get_pending_requests()] == [9_000_000_001]
    assert database.delete_pending_approval_users() == 1
    assert database.get_user(9_000_000_001) is None


def test_rls_enabled(database):
    with database._lock:
        rows = database._conn.execute(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relnamespace = current_schema()::regnamespace "
            "AND relname IN ('users', 'group_members')"
        ).fetchall()
    assert len(rows) == 2 and all(r["relrowsecurity"] for r in rows)
