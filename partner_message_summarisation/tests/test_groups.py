"""Tests for group discovery: title_matches regex matrix + dialog scan."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pyrogram.enums import ChatType

from partner_message_summarisation.groups import discover_target_chats, title_matches


def test_title_matches_matrix() -> None:
    assert title_matches("SISC <> AI Builders") is True
    assert title_matches("SISC <> Founders") is True
    assert title_matches("SISC <>") is True
    # anchored: prefix required
    assert title_matches("Random SISC <> chat") is False
    # case-sensitive by design
    assert title_matches("sisc <> ai builders") is False
    # no <> → not a partner group
    assert title_matches("SISC Discussion") is False
    assert title_matches("") is False
    assert title_matches(None) is False


def test_title_matches_custom_pattern() -> None:
    assert title_matches("PARTNER Ops", r"^PARTNER") is True
    assert title_matches("SISC <> AI", r"^PARTNER") is False


class _FakeDB:
    def __init__(self) -> None:
        self.upserts: list[tuple] = []

    async def upsert_chat(self, chat_id: int, title: str, chat_type: str) -> None:
        self.upserts.append((chat_id, title, chat_type))


class _FakeClient:
    def __init__(self, dialogs: list) -> None:
        self._dialogs = dialogs

    async def get_dialogs(self):
        for d in self._dialogs:
            yield d


def _run(coro):
    return asyncio.run(coro)


def test_discover_target_chats_upserts_only_matching() -> None:
    dialogs = [
        SimpleNamespace(
            chat=SimpleNamespace(
                id=-100111, title="SISC <> AI Builders", type=ChatType.SUPERGROUP
            )
        ),
        SimpleNamespace(
            chat=SimpleNamespace(
                id=-100222, title="SISC Discussion", type=ChatType.GROUP
            )
        ),
        SimpleNamespace(
            chat=SimpleNamespace(id=333, title=None, type=ChatType.PRIVATE)
        ),
        SimpleNamespace(
            chat=SimpleNamespace(
                id=-100444, title="SISC <> Founders", type=ChatType.GROUP
            )
        ),
    ]
    db = _FakeDB()
    targets = _run(discover_target_chats(_FakeClient(dialogs), db))

    assert [c.id for c in targets] == [-100111, -100444]
    assert db.upserts == [
        (-100111, "SISC <> AI Builders", "supergroup"),
        (-100444, "SISC <> Founders", "group"),
    ]


def test_discover_empty_dialog_list() -> None:
    db = _FakeDB()
    targets = _run(discover_target_chats(_FakeClient([]), db))
    assert targets == [] and db.upserts == []
