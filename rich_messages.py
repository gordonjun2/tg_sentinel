from __future__ import annotations

import asyncio
import json
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


async def send_rich_message_with_animation(
    bot_token: str,
    chat_id: int,
    markdown: str,
    animation_path: Union[Path, str],
    media_id: str,
    reply_markup: Optional[dict],
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
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
