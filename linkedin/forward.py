import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from linkedin.mapping import (
    MappedPost,
    PostKind,
    build_message_text,
    classify_and_map,
)
from linkedin.state import mark_seen, prune_seen, save_state
from linkedin import telegram_out

logger = logging.getLogger(__name__)

SEEN_CAP = 500


def new_posts(posts: List[Dict[str, Any]], seen: Dict[str, Any]) -> List[Dict[str, Any]]:
    fresh = [p for p in posts if p.get("id") and str(p["id"]) not in seen]
    fresh.sort(key=lambda p: int(p.get("createdAt") or 0))
    return fresh


def seed_state(state: dict, posts: List[Dict[str, Any]], now_iso: str) -> dict:
    for post in posts:
        post_id = post.get("id")
        if not post_id:
            continue
        mark_seen(state, str(post_id), {"first_seen": now_iso})
    state["seeded"] = True
    return state


def forward_mapped_post(mapped: MappedPost, li_client, bot_token: str, chat_id: int, session) -> bool:
    if mapped.kind == PostKind.SKIP:
        logger.info("Skipping unsupported post %s", mapped.post_id)
        return True

    if mapped.kind in (PostKind.TEXT, PostKind.ARTICLE, PostKind.VIDEO):
        text = build_message_text(
            mapped.commentary,
            mapped.permalink,
            mapped.article_url,
            telegram_out.TG_MESSAGE_MAX,
        )
        return telegram_out.send_text(bot_token, chat_id, text, session)

    caption = build_message_text(
        mapped.commentary,
        mapped.permalink,
        mapped.article_url,
        telegram_out.TG_CAPTION_MAX,
    )
    blobs = []
    for urn in mapped.image_urns:
        url = li_client.get_image_download_url(urn)
        blobs.append(li_client.download_bytes(url))
    if mapped.kind == PostKind.IMAGE:
        if not blobs:
            return False
        return telegram_out.send_photo(bot_token, chat_id, blobs[0], caption, session)
    if mapped.kind == PostKind.MULTI_IMAGE:
        if len(blobs) < 2:
            # fall back to single photo or text
            if len(blobs) == 1:
                return telegram_out.send_photo(
                    bot_token, chat_id, blobs[0], caption, session
                )
            return telegram_out.send_text(bot_token, chat_id, caption, session)
        return telegram_out.send_media_group(bot_token, chat_id, blobs, caption, session)
    return False


def process_posts(
    state: dict,
    posts: List[Dict[str, Any]],
    li_client,
    bot_token: str,
    chat_id: int,
    session,
    state_path: str,
    dry_run: bool,
) -> int:
    """Return number of posts successfully handled (sent or skipped)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    if not state.get("seeded"):
        if dry_run:
            logger.info("Dry run seed preview: would seed %d posts", len(posts))
            return 0
        seed_state(state, posts, now_iso)
        prune_seen(state, SEEN_CAP)
        save_state(state_path, state)
        logger.info("Seeded %d existing posts; no Telegram sends", len(state["seen"]))
        return 0

    handled = 0
    for post in new_posts(posts, state.get("seen") or {}):
        mapped = classify_and_map(post)
        if dry_run:
            logger.info("Dry run would forward %s kind=%s", mapped.post_id, mapped.kind)
            continue
        ok = forward_mapped_post(mapped, li_client, bot_token, chat_id, session)
        if not ok:
            logger.warning("Forward failed for %s; will retry next run", mapped.post_id)
            continue
        mark_seen(state, mapped.post_id, {"first_seen": now_iso})
        prune_seen(state, SEEN_CAP)
        save_state(state_path, state)
        handled += 1
    return handled
