from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rich_messages import (
    STREAM_CHUNK_DELAY_SECONDS,
    cumulative_markdown_chunks,
    send_animation,
    send_rich_message,
    stream_rich_then_animation,
)


def _ok_response(result: object) -> MagicMock:
    payload = {"ok": True, "result": result}
    return MagicMock(
        status_code=200,
        text=__import__("json").dumps(payload),
        json=lambda: payload,
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


def test_cumulative_markdown_chunks():
    text = "One\n\nTwo\n\nThree"
    assert cumulative_markdown_chunks(text) == [
        "One",
        "One\n\nTwo",
        "One\n\nTwo\n\nThree",
    ]


@pytest.mark.asyncio
async def test_stream_rich_then_animation(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "rich_messages._FILE_ID_CACHE_PATH", tmp_path / "cache.json"
    )
    monkeypatch.setattr("rich_messages._file_id_cache", {})
    monkeypatch.setattr("rich_messages._file_id_cache_loaded", False)

    mp4 = tmp_path / "hero.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    session = MagicMock()

    def _route(*args, **kwargs):
        url = args[0]
        if url.endswith("/sendRichMessageDraft"):
            return _ok_response(True)
        if url.endswith("/sendRichMessage"):
            return _ok_response(
                {
                    "message_id": 3,
                    "animation": {"file_id": "anim123"},
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")

    session.post.side_effect = _route
    markdown = "Hello\n\nWorld"
    result = await stream_rich_then_animation(
        "tok",
        9,
        markdown,
        mp4,
        "sisc_welcome",
        None,
        0.0,
        session,
    )
    assert result["message_id"] == 3
    methods = [
        call.args[0].rsplit("/", 1)[-1] for call in session.post.call_args_list
    ]
    assert methods == [
        "sendRichMessageDraft",
        "sendRichMessageDraft",
        "sendRichMessage",
    ]
    draft_bodies = [
        call.kwargs["json"]
        for call in session.post.call_args_list
        if call.args[0].endswith("/sendRichMessageDraft")
    ]
    assert draft_bodies[0]["rich_message"]["markdown"] == "Hello"
    assert draft_bodies[1]["rich_message"]["markdown"] == "Hello\n\nWorld"
    assert draft_bodies[0]["draft_id"] == draft_bodies[1]["draft_id"]
    rich_call = session.post.call_args_list[-1]
    data = rich_call.kwargs["data"]
    rich = __import__("json").loads(data["rich_message"])
    assert rich["markdown"].startswith("![](tg://video?id=sisc_welcome)")
    assert "Hello\n\nWorld" in rich["markdown"]
    assert STREAM_CHUNK_DELAY_SECONDS > 0
