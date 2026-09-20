"""Tests for the run_now CLI wiring (fakes only — no Telegram/LLM/DB)."""

from __future__ import annotations

import asyncio

import pytest

from partner_message_summarisation import run_now as run_now_mod


class _FakeDB:
    def __init__(self) -> None:
        self.closed = False
        self.last_run = [{"id": 1, "status": "success", "message_count": 3,
                          "chat_count": 1, "report_parts": 1, "error": None}]

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True

    async def last_runs(self, limit: int) -> list[dict]:
        return self.last_run


class _FakeBot:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def _run(coro):
    return asyncio.run(coro)


def test_run_now_wires_and_closes_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    db, bot = _FakeDB(), _FakeBot()
    calls: list[tuple] = []

    async def fake_summary(db_, bot_) -> None:
        calls.append((db_, bot_))

    monkeypatch.setattr(run_now_mod, "PartnerMessageSummarisationDB", lambda: db)
    monkeypatch.setattr(run_now_mod, "make_bot_client", lambda name=None: bot)
    monkeypatch.setattr(run_now_mod, "run_daily_summary", fake_summary)

    _run(run_now_mod.run_now())

    assert calls == [(db, bot)]
    assert bot.started and bot.stopped
    assert db.closed


def test_run_now_stops_bot_when_summary_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    db, bot = _FakeDB(), _FakeBot()

    async def boom(db_, bot_) -> None:
        raise RuntimeError("llm down")

    monkeypatch.setattr(run_now_mod, "PartnerMessageSummarisationDB", lambda: db)
    monkeypatch.setattr(run_now_mod, "make_bot_client", lambda name=None: bot)
    monkeypatch.setattr(run_now_mod, "run_daily_summary", boom)

    with pytest.raises(RuntimeError):
        _run(run_now_mod.run_now())

    assert bot.stopped and db.closed
