from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional, Union

import requests

API_TMPL = "https://api.telegram.org/bot{token}/{method}"


def _animation_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".gif":
        return "image/gif"
    if suffix in {".mp4", ".m4v"}:
        return "video/mp4"
    raise ValueError(
        f"Unsupported animation type {suffix!r} for {path}; use .gif or .mp4"
    )


def _post_animation(
    bot_token: str,
    chat_id: int,
    animation_path: Path,
    session: requests.Session,
) -> dict[str, Any]:
    content_type = _animation_content_type(animation_path)
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
        raise RuntimeError(
            f"sendAnimation failed status={resp.status_code} body={resp.text}"
        )
    return data["result"]


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
