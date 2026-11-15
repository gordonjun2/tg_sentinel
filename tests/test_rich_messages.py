from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rich_messages import send_animation, send_rich_message


@pytest.mark.asyncio
async def test_send_rich_message_payload():
    session = MagicMock()
    session.post.return_value = MagicMock(
        status_code=200,
        text='{"ok":true,"result":{"message_id":1}}',
        json=lambda: {"ok": True, "result": {"message_id": 1}},
    )
    result = await send_rich_message(
        bot_token="tok",
        chat_id=42,
        markdown="# Hello",
        reply_markup={"inline_keyboard": []},
        session=session,
    )
    assert result["message_id"] == 1
    url = session.post.call_args.args[0]
    assert url.endswith("/sendRichMessage")
    body = session.post.call_args.kwargs["json"]
    assert body["chat_id"] == 42
    assert body["rich_message"] == {"markdown": "# Hello"}
    assert body["reply_markup"] == {"inline_keyboard": []}


@pytest.mark.asyncio
async def test_send_rich_message_raises_on_api_error():
    session = MagicMock()
    session.post.return_value = MagicMock(
        status_code=400,
        text='{"ok":false,"description":"Bad Request"}',
        json=lambda: {"ok": False, "description": "Bad Request"},
    )
    with pytest.raises(RuntimeError, match="Bad Request"):
        await send_rich_message(
            bot_token="tok",
            chat_id=42,
            markdown="x",
            reply_markup=None,
            session=session,
        )


@pytest.mark.asyncio
async def test_send_animation_payload(tmp_path: Path):
    gif = tmp_path / "clip.gif"
    gif.write_bytes(b"GIF89a")
    session = MagicMock()
    session.post.return_value = MagicMock(
        status_code=200,
        text='{"ok":true,"result":{"message_id":9}}',
        json=lambda: {"ok": True, "result": {"message_id": 9}},
    )
    result = await send_animation(
        bot_token="tok",
        chat_id=7,
        animation_path=gif,
        session=session,
    )
    assert result["message_id"] == 9
    url = session.post.call_args.args[0]
    assert url.endswith("/sendAnimation")
    assert session.post.call_args.kwargs["data"]["chat_id"] == "7"
    assert "animation" in session.post.call_args.kwargs["files"]


@pytest.mark.asyncio
async def test_send_animation_missing_file():
    with pytest.raises(FileNotFoundError):
        await send_animation(
            bot_token="tok",
            chat_id=7,
            animation_path="/tmp/does-not-exist-serendip.gif",
        )
