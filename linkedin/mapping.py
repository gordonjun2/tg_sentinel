# linkedin/mapping.py
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class PostKind(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    MULTI_IMAGE = "multi_image"
    ARTICLE = "article"
    VIDEO = "video"
    SKIP = "skip"


@dataclass
class MappedPost:
    post_id: str
    kind: PostKind
    commentary: str
    permalink: str
    article_url: Optional[str] = None
    image_urns: List[str] = field(default_factory=list)


def permalink_for(post_id: str) -> str:
    return f"https://www.linkedin.com/feed/update/{post_id}"


def _commentary(post: Dict[str, Any]) -> str:
    raw = post.get("commentary")
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        text = raw.get("text")
        if isinstance(text, str):
            return text.strip()
    return ""


def classify_and_map(post: Dict[str, Any]) -> MappedPost:
    post_id = str(post.get("id") or "")
    if not post_id:
        raise ValueError("post missing id")
    commentary = _commentary(post)
    permalink = permalink_for(post_id)
    content = post.get("content") or {}
    if not isinstance(content, dict):
        content = {}

    if "poll" in content or "document" in content:
        return MappedPost(post_id, PostKind.SKIP, commentary, permalink)

    if "multiImage" in content:
        images = (content.get("multiImage") or {}).get("images") or []
        urns = [str(img.get("id")) for img in images if img.get("id")]
        return MappedPost(post_id, PostKind.MULTI_IMAGE, commentary, permalink, image_urns=urns)

    if "article" in content:
        article = content.get("article") or {}
        source = article.get("source") or article.get("sourceUrl")
        return MappedPost(
            post_id,
            PostKind.ARTICLE,
            commentary,
            permalink,
            article_url=str(source) if source else None,
        )

    media = content.get("media")
    if isinstance(media, dict) and media.get("id"):
        media_id = str(media["id"])
        if media_id.startswith("urn:li:video:"):
            return MappedPost(post_id, PostKind.VIDEO, commentary, permalink)
        if media_id.startswith("urn:li:image:"):
            return MappedPost(
                post_id, PostKind.IMAGE, commentary, permalink, image_urns=[media_id]
            )
        return MappedPost(post_id, PostKind.SKIP, commentary, permalink)

    return MappedPost(post_id, PostKind.TEXT, commentary, permalink)


def build_message_text(
    commentary: str,
    permalink: str,
    article_url: Optional[str],
    max_chars: int,
) -> str:
    parts: List[str] = []
    if commentary:
        parts.append(commentary)
    if article_url:
        parts.append(article_url)
    parts.append(permalink)
    text = "\n\n".join(parts)
    if len(text) <= max_chars:
        return text
    # Keep permalink (and article URL if present) intact; truncate commentary.
    suffix_parts: List[str] = []
    if article_url:
        suffix_parts.append(article_url)
    suffix_parts.append(permalink)
    suffix = "\n\n" + "\n\n".join(suffix_parts)
    budget = max_chars - len(suffix)
    if budget < 1:
        return permalink[:max_chars]
    body = (commentary or "")[: max(0, budget - 1)].rstrip()
    if body:
        return body + "…" + suffix
    return suffix.lstrip()[:max_chars]
