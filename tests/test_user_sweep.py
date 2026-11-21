import asyncio
from types import SimpleNamespace

import pytest

import user_sweep
from database import Database

G = -1001


@pytest.fixture
def database(tmp_path):
    return Database(str(tmp_path / "t.db"))


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
