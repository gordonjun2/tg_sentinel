from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Optional, Union

import requests

API_TMPL = "https://api.telegram.org/bot{token}/{method}"
_FILE_ID_CACHE_PATH = Path(__file__).resolve().parent / ".onboarding_animation_file_ids.json"
_file_id_cache: dict[str, str] = {}
_file_id_cache_loaded = False


def _animation_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".gif":
        return "image/gif"
    if suffix in {".mp4", ".m4v"}:
        return "video/mp4"
    raise ValueError(
        f"Unsupported animation type {suffix!r} for {path}; use .gif or .mp4"
    )


def _cache_key(path: Path) -> str:
    resolved = path.resolve()
    stat = resolved.stat()
    return f"{resolved}:{stat.st_mtime_ns}:{stat.st_size}"


def _load_file_id_cache() -> None:
    global _file_id_cache_loaded
    if _file_id_cache_loaded:
        return
    _file_id_cache_loaded = True
    if not _FILE_ID_CACHE_PATH.is_file():
        return
    try:
        raw = json.loads(_FILE_ID_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(raw, dict):
        _file_id_cache.update(
            {str(key): str(value) for key, value in raw.items() if value}
        )


def _persist_file_id_cache() -> None:
    try:
        _FILE_ID_CACHE_PATH.write_text(
            json.dumps(_file_id_cache, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _remember_file_id(path: Path, file_id: str) -> None:
    _load_file_id_cache()
    _file_id_cache[_cache_key(path)] = file_id
    _persist_file_id_cache()


def _cached_file_id(path: Path) -> Optional[str]:
    _load_file_id_cache()
    return _file_id_cache.get(_cache_key(path))


def _extract_animation_file_id(result: dict[str, Any]) -> Optional[str]:
    animation = result.get("animation")
    if isinstance(animation, dict):
        file_id = animation.get("file_id")
        if isinstance(file_id, str) and file_id:
            return file_id
    document = result.get("document")
    if isinstance(document, dict):
        file_id = document.get("file_id")
        if isinstance(file_id, str) and file_id:
            return file_id
    return None


def _post_animation(
    bot_token: str,
    chat_id: int,
    animation_path: Path,
    session: requests.Session,
) -> dict[str, Any]:
    content_type = _animation_content_type(animation_path)
    cached = _cached_file_id(animation_path)
    if cached is not None:
        resp = session.post(
            API_TMPL.format(token=bot_token, method="sendAnimation"),
            data={"chat_id": str(chat_id), "animation": cached},
            timeout=60,
        )
    else:
        with animation_path.open("rb") as handle:
            resp = session.post(
                API_TMPL.format(token=bot_token, method="sendAnimation"),
                data={"chat_id": str(chat_id)},
                files={
                    "animation": (animation_path.name, handle, content_type),
                },
                timeout=60,
            )
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(
            f"sendAnimation non-JSON response status={resp.status_code} body={resp.text}"
        ) from exc
    if resp.status_code != 200 or not data.get("ok"):
        if cached is not None:
            _file_id_cache.pop(_cache_key(animation_path), None)
            _persist_file_id_cache()
        raise RuntimeError(
            f"sendAnimation failed status={resp.status_code} body={resp.text}"
        )
    result = data["result"]
    file_id = _extract_animation_file_id(result)
    if file_id is not None:
        _remember_file_id(animation_path, file_id)
    return result


async def send_animation(
    bot_token: str,
    chat_id: int,
    animation_path: Union[Path, str],
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
    path = Path(animation_path)
    if not path.is_file():
        raise FileNotFoundError(f"Animation file not found: {path}")
    owns_session = session is None
    sess = session or requests.Session()
    try:
        return await asyncio.to_thread(
            _post_animation, bot_token, chat_id, path, sess
        )
    finally:
        if owns_session:
            sess.close()


def _embedded_markdown(markdown: str, media_id: str) -> str:
    return f"![](tg://video?id={media_id})\n\n{markdown}"


def _post_rich_message(
    bot_token: str,
    chat_id: int,
    markdown: str,
    reply_markup: Optional[dict],
    session: requests.Session,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "rich_message": {"markdown": markdown},
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    resp = session.post(
        API_TMPL.format(token=bot_token, method="sendRichMessage"),
        json=payload,
        timeout=30,
    )
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(
            f"sendRichMessage non-JSON response status={resp.status_code} body={resp.text}"
        ) from exc
    if resp.status_code != 200 or not data.get("ok"):
        raise RuntimeError(
            f"sendRichMessage failed status={resp.status_code} body={resp.text}"
        )
    return data["result"]


def _post_rich_message_with_animation(
    bot_token: str,
    chat_id: int,
    markdown: str,
    animation_path: Path,
    media_id: str,
    reply_markup: Optional[dict],
    session: requests.Session,
) -> dict[str, Any]:
    content_type = _animation_content_type(animation_path)
    body_markdown = _embedded_markdown(markdown, media_id)
    cached = _cached_file_id(animation_path)
    rich_message: dict[str, Any] = {
        "markdown": body_markdown,
        "media": [
            {
                "id": media_id,
                "media": {
                    "type": "animation",
                    "media": cached if cached is not None else f"attach://{media_id}",
                },
            }
        ],
    }
    payload_fields: dict[str, Any] = {
        "chat_id": str(chat_id),
        "rich_message": json.dumps(rich_message),
    }
    if reply_markup is not None:
        payload_fields["reply_markup"] = json.dumps(reply_markup)

    if cached is not None:
        resp = session.post(
            API_TMPL.format(token=bot_token, method="sendRichMessage"),
            data=payload_fields,
            timeout=60,
        )
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(
                f"sendRichMessage non-JSON response status={resp.status_code} body={resp.text}"
            ) from exc
        if resp.status_code == 200 and data.get("ok"):
            return data["result"]
        _file_id_cache.pop(_cache_key(animation_path), None)
        _persist_file_id_cache()
        rich_message["media"][0]["media"]["media"] = f"attach://{media_id}"
        payload_fields["rich_message"] = json.dumps(rich_message)

    with animation_path.open("rb") as handle:
        resp = session.post(
            API_TMPL.format(token=bot_token, method="sendRichMessage"),
            data=payload_fields,
            files={
                media_id: (animation_path.name, handle, content_type),
            },
            timeout=60,
        )
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(
            f"sendRichMessage non-JSON response status={resp.status_code} body={resp.text}"
        ) from exc
    if resp.status_code != 200 or not data.get("ok"):
        raise RuntimeError(
            f"sendRichMessage failed status={resp.status_code} body={resp.text}"
        )
    result = data["result"]
    file_id = _extract_animation_file_id(result)
    if file_id is not None:
        _remember_file_id(animation_path, file_id)
    return result


STREAM_CHUNK_DELAY_SECONDS = 0.28


def _new_draft_id() -> int:
    # Non-zero; reuse across draft updates so Telegram animates the growth.
    value = int(time.time() * 1000) % 2_147_483_647
    if value == 0:
        return 1
    return value


def cumulative_markdown_chunks(markdown: str) -> list[str]:
    parts = [part.strip() for part in markdown.strip().split("\n\n") if part.strip()]
    if not parts:
        return [markdown]
    return ["\n\n".join(parts[: index + 1]) for index in range(len(parts))]


def _post_rich_message_draft(
    bot_token: str,
    chat_id: int,
    draft_id: int,
    markdown: str,
    session: requests.Session,
) -> bool:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "draft_id": draft_id,
        "rich_message": {"markdown": markdown},
    }
    resp = session.post(
        API_TMPL.format(token=bot_token, method="sendRichMessageDraft"),
        json=payload,
        timeout=30,
    )
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(
            f"sendRichMessageDraft non-JSON response status={resp.status_code} body={resp.text}"
        ) from exc
    if resp.status_code != 200 or not data.get("ok"):
        raise RuntimeError(
            f"sendRichMessageDraft failed status={resp.status_code} body={resp.text}"
        )
    return bool(data.get("result"))


async def send_rich_message(
    bot_token: str,
    chat_id: int,
    markdown: str,
    reply_markup: Optional[dict] = None,
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
    owns_session = session is None
    sess = session or requests.Session()
    try:
        return await asyncio.to_thread(
            _post_rich_message, bot_token, chat_id, markdown, reply_markup, sess
        )
    finally:
        if owns_session:
            sess.close()


async def _stream_rich_drafts(
    bot_token: str,
    chat_id: int,
    markdown: str,
    chunk_delay_seconds: float,
    session: requests.Session,
) -> None:
    draft_id = _new_draft_id()
    for chunk in cumulative_markdown_chunks(markdown):
        await asyncio.to_thread(
            _post_rich_message_draft, bot_token, chat_id, draft_id, chunk, session
        )
        if chunk_delay_seconds > 0:
            await asyncio.sleep(chunk_delay_seconds)


async def stream_rich_message(
    bot_token: str,
    chat_id: int,
    markdown: str,
    reply_markup: Optional[dict],
    chunk_delay_seconds: float,
    session: Optional[requests.Session],
) -> dict[str, Any]:
    """Stream text as a rich draft, then persist with sendRichMessage."""
    owns_session = session is None
    sess = session or requests.Session()
    try:
        await _stream_rich_drafts(
            bot_token, chat_id, markdown, chunk_delay_seconds, sess
        )
        return await asyncio.to_thread(
            _post_rich_message, bot_token, chat_id, markdown, reply_markup, sess
        )
    finally:
        if owns_session:
            sess.close()


async def stream_rich_then_animation(
    bot_token: str,
    chat_id: int,
    markdown: str,
    animation_path: Union[Path, str],
    media_id: str,
    reply_markup: Optional[dict],
    chunk_delay_seconds: float,
    session: Optional[requests.Session],
) -> dict[str, Any]:
    """Send rich text with embedded animation and optional keyboard in one shot.

    Draft streaming is skipped on purpose: drafts cannot carry reply_markup or
    new media uploads, so streaming only delayed the button and the GIF.
    Prefer a warmed file_id (see warm_animation_file_ids) so Telegram does not
    re-upload on every /start or /serendip.
    """
    _ = chunk_delay_seconds  # kept for call-site compatibility; drafts skipped
    path = Path(animation_path)
    if not path.is_file():
        raise FileNotFoundError(f"Animation file not found: {path}")
    if not media_id:
        raise ValueError("media_id must be a non-empty string")
    owns_session = session is None
    sess = session or requests.Session()
    try:
        return await asyncio.to_thread(
            _post_rich_message_with_animation,
            bot_token,
            chat_id,
            markdown,
            path,
            media_id,
            reply_markup,
            sess,
        )
    finally:
        if owns_session:
            sess.close()


def _delete_message(
    bot_token: str,
    chat_id: int,
    message_id: int,
    session: requests.Session,
) -> None:
    resp = session.post(
        API_TMPL.format(token=bot_token, method="deleteMessage"),
        data={"chat_id": str(chat_id), "message_id": str(message_id)},
        timeout=30,
    )
    try:
        data = resp.json()
    except Exception:
        return
    if resp.status_code != 200 or not data.get("ok"):
        return


def warm_animation_file_ids(
    bot_token: str,
    warm_chat_id: int,
    animation_paths: list[Path],
    session: Optional[requests.Session] = None,
) -> None:
    """Upload each animation once (if uncached) so later sends reuse file_id."""
    owns_session = session is None
    sess = session or requests.Session()
    try:
        for path in animation_paths:
            resolved = Path(path)
            if not resolved.is_file():
                raise FileNotFoundError(f"Animation file not found: {resolved}")
            if _cached_file_id(resolved) is not None:
                continue
            result = _post_animation(bot_token, warm_chat_id, resolved, sess)
            message_id = result.get("message_id")
            if isinstance(message_id, int):
                _delete_message(bot_token, warm_chat_id, message_id, sess)
    finally:
        if owns_session:
            sess.close()


async def warm_onboarding_animations(
    bot_token: str,
    warm_chat_id: int,
    animation_paths: list[Path],
    session: Optional[requests.Session] = None,
) -> None:
    await asyncio.to_thread(
        warm_animation_file_ids,
        bot_token,
        warm_chat_id,
        animation_paths,
        session,
    )
