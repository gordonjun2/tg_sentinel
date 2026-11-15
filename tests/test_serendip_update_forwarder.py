"""Unit tests for serendip update fan-out."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from serendip_update_forwarder import (
    _BOT_DATA_CLIENT_KEY,
    forward_update_to_serendip,
)


@pytest.mark.asyncio
async def test_forward_skips_when_url_unset() -> None:
    update = MagicMock()
    update.update_id = 1
    update.to_dict.return_value = {"update_id": 1}
    context = MagicMock()

    with patch("serendip_update_forwarder.SERENDIP_UPDATE_FORWARD_URL", ""):
        await forward_update_to_serendip(update, context)

    context.application.bot_data.get.assert_not_called()


@pytest.mark.asyncio
async def test_forward_posts_update_json() -> None:
    update = MagicMock()
    update.update_id = 42
    update.to_dict.return_value = {"update_id": 42, "message": {"text": "/matches"}}
    client = AsyncMock()
    response = MagicMock()
    response.status_code = 200
    response.text = '{"ok":true}'
    client.post = AsyncMock(return_value=response)

    context = MagicMock()
    context.application.bot_data = {_BOT_DATA_CLIENT_KEY: client}

    with (
        patch(
            "serendip_update_forwarder.SERENDIP_UPDATE_FORWARD_URL",
            "https://api.example.com/bot/webhook",
        ),
        patch(
            "serendip_update_forwarder.SERENDIP_UPDATE_FORWARD_SECRET",
            "secret-value",
        ),
    ):
        await forward_update_to_serendip(update, context)

    client.post.assert_awaited_once()
    args, kwargs = client.post.await_args
    assert args[0] == "https://api.example.com/bot/webhook"
    assert kwargs["json"] == {"update_id": 42, "message": {"text": "/matches"}}
    assert kwargs["headers"]["X-Telegram-Bot-Api-Secret-Token"] == "secret-value"


@pytest.mark.asyncio
async def test_forward_logs_http_error_without_raising() -> None:
    update = MagicMock()
    update.update_id = 7
    update.to_dict.return_value = {"update_id": 7}
    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("down"))

    context = MagicMock()
    context.application.bot_data = {_BOT_DATA_CLIENT_KEY: client}

    with (
        patch(
            "serendip_update_forwarder.SERENDIP_UPDATE_FORWARD_URL",
            "https://api.example.com/bot/webhook",
        ),
        patch(
            "serendip_update_forwarder.SERENDIP_UPDATE_FORWARD_SECRET",
            "secret-value",
        ),
    ):
        await forward_update_to_serendip(update, context)
