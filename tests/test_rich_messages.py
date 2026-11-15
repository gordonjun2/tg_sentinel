from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rich_messages import (
    send_animation,
    send_rich_message,
    send_rich_message_with_animation,
)


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
    assert session.post.call_args.kwargs["files"]["animation"][2] == "image/gif"


@pytest.mark.asyncio
async def test_send_animation_mp4_content_type(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    session = MagicMock()
    session.post.return_value = MagicMock(
        status_code=200,
        text='{"ok":true,"result":{"message_id":10}}',
        json=lambda: {"ok": True, "result": {"message_id": 10}},
    )
    await send_animation(
        bot_token="tok",
        chat_id=7,
        animation_path=mp4,
        session=session,
    )
    assert session.post.call_args.kwargs["files"]["animation"][2] == "video/mp4"


@pytest.mark.asyncio
async def test_send_animation_missing_file():
    with pytest.raises(FileNotFoundError):
        await send_animation(
            bot_token="tok",
            chat_id=7,
            animation_path="/tmp/does-not-exist-serendip.gif",
        )


@pytest.mark.asyncio
async def test_send_rich_message_with_animation_upload(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "rich_messages._FILE_ID_CACHE_PATH", tmp_path / "cache.json"
    )
    monkeypatch.setattr("rich_messages._file_id_cache", {})
    monkeypatch.setattr("rich_messages._file_id_cache_loaded", False)

    mp4 = tmp_path / "hero.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    session = MagicMock()
    session.post.return_value = MagicMock(
        status_code=200,
        text='{"ok":true,"result":{"message_id":3,"animation":{"file_id":"anim123"}}}',
        json=lambda: {
            "ok": True,
            "result": {
                "message_id": 3,
                "animation": {"file_id": "anim123"},
            },
        },
    )
    result = await send_rich_message_with_animation(
        bot_token="tok",
        chat_id=9,
        markdown="# SISC",
        animation_path=mp4,
        media_id="sisc_hero",
        reply_markup=None,
        session=session,
    )
    assert result["message_id"] == 3
    url = session.post.call_args.args[0]
    assert url.endswith("/sendRichMessage")
    data = session.post.call_args.kwargs["data"]
    assert data["chat_id"] == "9"
    assert "sisc_hero" in session.post.call_args.kwargs["files"]
    rich = __import__("json").loads(data["rich_message"])
    assert rich["markdown"].startswith("![](tg://video?id=sisc_hero)")
    assert "# SISC" in rich["markdown"]
    assert rich["media"][0]["media"]["type"] == "animation"
    assert rich["media"][0]["media"]["media"] == "attach://sisc_hero"
