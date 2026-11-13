# LinkedIn → Telegram Forwarder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cron-poll LinkedIn Community Management Posts API for the SISC company page and forward new posts (text + images; video/article as text+URLs) to Telegram `TARGET_GROUP_ID`, with seed-only first run.

**Architecture:** A small `linkedin/` package (state, tokens, mapping, API client, Telegram send) plus two entrypoints: `linkedin_oauth.py` (one-shot auth) and `linkedin_forwarder.py` + `linkedin_forwarder.sh` (cron, same shape as Luma discovery). Tokens live in `data/linkedin_tokens.json`; seen IDs in `data/linkedin_forwarder_state.json`.

**Tech Stack:** Python 3, `requests`, Telegram Bot HTTP API, LinkedIn REST (`/rest/posts`, `/rest/images`), `pytest` + `unittest.mock`, existing `BOT_TOKEN` / `TARGET_GROUP_ID` from `config.py`.

**Spec:** `docs/superpowers/specs/2026-11-13-linkedin-to-telegram-forwarder-design.md`

## Global Constraints

- Official LinkedIn Community Management APIs only (Posts + Images); no scraping.
- First run seeds `seen` and sends nothing.
- Forward: text, single image, multi-image, article/link, video (text + permalink only). Skip poll/document/unknown but mark seen.
- Always append permalink `https://www.linkedin.com/feed/update/{postUrn}` as the last line.
- No Markdown/HTML parse mode on Telegram (plain text).
- Never download LinkedIn videos.
- `--dry-run`: no `linkedin_forwarder_state.json` writes; token refresh may update `linkedin_tokens.json`.
- Do **not** add required LinkedIn env vars to `config.py` (would break `bot.py` import); read LinkedIn vars inside the `linkedin` package / entrypoints. Document them in `.env.example` + README.
- Pin `LinkedIn-Version` header in one constant (`LINKEDIN_API_VERSION = "202511"`).
- Cron every 5 minutes via shell wrapper; not in `start_all.sh`.
- Mark seen only after successful send or intentional skip.
- No new third-party packages beyond `requests` (already in `requirements.txt`).

## File structure

| Path | Responsibility |
|---|---|
| `linkedin/__init__.py` | Empty / package marker |
| `linkedin/state.py` | Load/save/prune forwarder seen-state |
| `linkedin/tokens.py` | Load/save token file; refresh access token |
| `linkedin/mapping.py` | Classify post, build body+permalink, extract image URNs |
| `linkedin/client.py` | LinkedIn REST client (posts, images, org acls helper) |
| `linkedin/telegram_out.py` | `sendMessage` / `sendPhoto` / `sendMediaGroup` |
| `linkedin/forward.py` | Pure orchestration helpers: seed, pick new, process one post |
| `linkedin_oauth.py` | CLI OAuth code exchange + write tokens |
| `linkedin_forwarder.py` | Cron entry: lock, argparse, call forward pipeline |
| `linkedin_forwarder.sh` | VPS venv + tee log |
| `linkedin/tests/` | Unit tests + fixtures |
| `.env.example`, `README.md` | Document LinkedIn vars + cron + oauth |

---

### Task 1: State module (seen + seed flag)

**Files:**
- Create: `linkedin/__init__.py`
- Create: `linkedin/state.py`
- Create: `linkedin/tests/test_state.py`
- Create: `linkedin/tests/conftest.py`

**Interfaces:**
- Produces:
  - `DEFAULT_STATE: dict` → `{"version": 1, "seeded": False, "seen": {}}`
  - `load_state(path: str) -> dict`
  - `save_state(path: str, state: dict) -> None` (atomic tmp + replace)
  - `mark_seen(state: dict, post_id: str, meta: dict) -> None`
  - `prune_seen(state: dict, max_entries: int) -> int` (keep newest by `first_seen` ISO; return removed count)

- [ ] **Step 1: Write the failing tests**

```python
# linkedin/tests/conftest.py
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
```

```python
# linkedin/tests/test_state.py
import json
import os

from linkedin.state import DEFAULT_STATE, load_state, mark_seen, prune_seen, save_state


def test_load_missing_returns_default(tmp_path):
    path = str(tmp_path / "state.json")
    state = load_state(path)
    assert state["seeded"] is False
    assert state["seen"] == {}
    assert state["version"] == 1


def test_save_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    state = dict(DEFAULT_STATE)
    state["seeded"] = True
    mark_seen(state, "urn:li:share:1", {"first_seen": "2026-11-13T00:00:00+08:00"})
    save_state(path, state)
    loaded = load_state(path)
    assert loaded["seeded"] is True
    assert "urn:li:share:1" in loaded["seen"]
    assert not os.path.exists(path + ".tmp")


def test_prune_keeps_newest(tmp_path):
    state = dict(DEFAULT_STATE)
    state["seen"] = {
        "a": {"first_seen": "2026-01-01T00:00:00+00:00"},
        "b": {"first_seen": "2026-06-01T00:00:00+00:00"},
        "c": {"first_seen": "2026-11-01T00:00:00+00:00"},
    }
    removed = prune_seen(state, max_entries=2)
    assert removed == 1
    assert set(state["seen"]) == {"b", "c"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest linkedin/tests/test_state.py -v`  
Expected: FAIL (module not found / import error)

- [ ] **Step 3: Implement `linkedin/state.py` and empty `__init__.py`**

```python
# linkedin/__init__.py
```

```python
# linkedin/state.py
import json
import os
from typing import Dict

DEFAULT_STATE: Dict = {"version": 1, "seeded": False, "seen": {}}


def load_state(path: str) -> dict:
    try:
        with open(path) as f:
            state = json.load(f)
        if not isinstance(state.get("seen"), dict):
            state["seen"] = {}
        if "seeded" not in state:
            state["seeded"] = bool(state["seen"])
        if "version" not in state:
            state["version"] = 1
        return state
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 1, "seeded": False, "seen": {}}


def save_state(path: str, state: dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, path)


def mark_seen(state: dict, post_id: str, meta: dict) -> None:
    state.setdefault("seen", {})[post_id] = meta


def prune_seen(state: dict, max_entries: int) -> int:
    seen = state.get("seen") or {}
    if len(seen) <= max_entries:
        return 0
    ordered = sorted(
        seen.items(),
        key=lambda item: item[1].get("first_seen") or "",
        reverse=True,
    )
    keep = dict(ordered[:max_entries])
    removed = len(seen) - len(keep)
    state["seen"] = keep
    return removed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest linkedin/tests/test_state.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add linkedin/__init__.py linkedin/state.py linkedin/tests/conftest.py linkedin/tests/test_state.py
git commit -m "$(cat <<'EOF'
feat(linkedin): add forwarder seen-state helpers

EOF
)"
```

---

### Task 2: Token store + refresh

**Files:**
- Create: `linkedin/tokens.py`
- Create: `linkedin/tests/test_tokens.py`

**Interfaces:**
- Consumes: none from Task 1
- Produces:
  - `TokenBundle` dataclass: `access_token: str`, `refresh_token: str`, `expires_at: float` (unix), `refresh_expires_at: float`
  - `load_tokens(path: str) -> TokenBundle` (raises `FileNotFoundError` / `ValueError`)
  - `save_tokens(path: str, bundle: TokenBundle) -> None` (atomic)
  - `bundle_from_oauth_response(payload: dict, now: float) -> TokenBundle`
  - `needs_refresh(bundle: TokenBundle, now: float, skew_seconds: float) -> bool`
  - `refresh_access_token(client_id: str, client_secret: str, refresh_token: str, session) -> dict` (POST LinkedIn; returns raw JSON)
  - `ensure_access_token(path: str, client_id: str, client_secret: str, session, now: float, skew_seconds: float) -> str` (refresh+save if needed; return access token)

- [ ] **Step 1: Write the failing tests**

```python
# linkedin/tests/test_tokens.py
from unittest.mock import MagicMock

from linkedin.tokens import (
    TokenBundle,
    bundle_from_oauth_response,
    ensure_access_token,
    load_tokens,
    needs_refresh,
    save_tokens,
)


def test_bundle_from_oauth_response():
    bundle = bundle_from_oauth_response(
        {
            "access_token": "a",
            "expires_in": 3600,
            "refresh_token": "r",
            "refresh_token_expires_in": 86400,
        },
        now=1_000_000.0,
    )
    assert bundle.access_token == "a"
    assert bundle.refresh_token == "r"
    assert bundle.expires_at == 1_000_000.0 + 3600
    assert bundle.refresh_expires_at == 1_000_000.0 + 86400


def test_needs_refresh_true_near_expiry():
    bundle = TokenBundle("a", "r", expires_at=1_000_100.0, refresh_expires_at=2_000_000.0)
    assert needs_refresh(bundle, now=1_000_000.0, skew_seconds=300.0) is True


def test_needs_refresh_false_when_fresh():
    bundle = TokenBundle("a", "r", expires_at=1_100_000.0, refresh_expires_at=2_000_000.0)
    assert needs_refresh(bundle, now=1_000_000.0, skew_seconds=300.0) is False


def test_save_load_roundtrip(tmp_path):
    path = str(tmp_path / "tokens.json")
    bundle = TokenBundle("a", "r", 1.0, 2.0)
    save_tokens(path, bundle)
    loaded = load_tokens(path)
    assert loaded == bundle


def test_ensure_access_token_refreshes(tmp_path):
    path = str(tmp_path / "tokens.json")
    save_tokens(path, TokenBundle("old", "r", expires_at=1_000_000.0, refresh_expires_at=9_000_000.0))
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "access_token": "new",
        "expires_in": 3600,
        "refresh_token": "r2",
        "refresh_token_expires_in": 86400,
    }
    session.post.return_value = resp
    token = ensure_access_token(
        path,
        client_id="id",
        client_secret="secret",
        session=session,
        now=1_000_050.0,
        skew_seconds=300.0,
    )
    assert token == "new"
    assert load_tokens(path).access_token == "new"
    assert load_tokens(path).refresh_token == "r2"
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `venv/bin/pytest linkedin/tests/test_tokens.py -v`

- [ ] **Step 3: Implement `linkedin/tokens.py`**

```python
# linkedin/tokens.py
import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict

TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_at: float
    refresh_expires_at: float


def bundle_from_oauth_response(payload: Dict[str, Any], now: float) -> TokenBundle:
    access = payload.get("access_token")
    if not access:
        raise ValueError("OAuth response missing access_token")
    refresh = payload.get("refresh_token") or ""
    expires_in = float(payload.get("expires_in") or 0)
    refresh_expires_in = float(payload.get("refresh_token_expires_in") or 0)
    return TokenBundle(
        access_token=str(access),
        refresh_token=str(refresh),
        expires_at=now + expires_in,
        refresh_expires_at=now + refresh_expires_in if refresh else 0.0,
    )


def needs_refresh(bundle: TokenBundle, now: float, skew_seconds: float) -> bool:
    return now >= (bundle.expires_at - skew_seconds)


def save_tokens(path: str, bundle: TokenBundle) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(asdict(bundle), f, indent=1)
    os.replace(tmp, path)


def load_tokens(path: str) -> TokenBundle:
    with open(path) as f:
        raw = json.load(f)
    for key in ("access_token", "refresh_token", "expires_at", "refresh_expires_at"):
        if key not in raw:
            raise ValueError(f"token file missing {key}")
    return TokenBundle(
        access_token=str(raw["access_token"]),
        refresh_token=str(raw["refresh_token"]),
        expires_at=float(raw["expires_at"]),
        refresh_expires_at=float(raw["refresh_expires_at"]),
    )


def refresh_access_token(
    client_id: str, client_secret: str, refresh_token: str, session
) -> dict:
    if not refresh_token:
        raise ValueError("No refresh_token available; re-run linkedin_oauth.py")
    resp = session.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"LinkedIn token refresh failed HTTP {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def ensure_access_token(
    path: str,
    client_id: str,
    client_secret: str,
    session,
    now: float,
    skew_seconds: float,
) -> str:
    bundle = load_tokens(path)
    if not needs_refresh(bundle, now, skew_seconds):
        return bundle.access_token
    payload = refresh_access_token(
        client_id, client_secret, bundle.refresh_token, session
    )
    if "refresh_token" not in payload:
        payload = dict(payload)
        payload["refresh_token"] = bundle.refresh_token
        payload.setdefault(
            "refresh_token_expires_in",
            max(0.0, bundle.refresh_expires_at - now),
        )
    new_bundle = bundle_from_oauth_response(payload, now)
    save_tokens(path, new_bundle)
    return new_bundle.access_token
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `venv/bin/pytest linkedin/tests/test_tokens.py -v`

- [ ] **Step 5: Commit**

```bash
git add linkedin/tokens.py linkedin/tests/test_tokens.py
git commit -m "$(cat <<'EOF'
feat(linkedin): add OAuth token persistence and refresh

EOF
)"
```

---

### Task 3: Post mapping (classify, caption, image URNs)

**Files:**
- Create: `linkedin/mapping.py`
- Create: `linkedin/tests/test_mapping.py`
- Create: `linkedin/tests/fixtures/posts_samples.json`

**Interfaces:**
- Produces:
  - `PostKind` enum: `TEXT`, `IMAGE`, `MULTI_IMAGE`, `ARTICLE`, `VIDEO`, `SKIP`
  - `MappedPost` dataclass: `post_id: str`, `kind: PostKind`, `commentary: str`, `permalink: str`, `article_url: str | None`, `image_urns: list[str]`
  - `permalink_for(post_id: str) -> str`
  - `build_message_text(commentary: str, permalink: str, article_url: str | None, max_chars: int) -> str`
  - `classify_and_map(post: dict) -> MappedPost`  
    Post id from `post["id"]`. Commentary from `post.get("commentary")` (string) or nested text if present. Detect content via `post.get("content")` keys: `media` (image), `multiImage`, `article`, `media` with video URN prefix `urn:li:video:`, else text. Unknown → `SKIP`.

- [ ] **Step 1: Write fixtures + failing tests**

```json
{
  "text": {
    "id": "urn:li:share:111",
    "commentary": "Hello SISC",
    "content": {}
  },
  "image": {
    "id": "urn:li:ugcPost:222",
    "commentary": "Photo day",
    "content": {
      "media": { "id": "urn:li:image:ABC" }
    }
  },
  "multi": {
    "id": "urn:li:ugcPost:333",
    "commentary": "Album",
    "content": {
      "multiImage": {
        "images": [
          { "id": "urn:li:image:A" },
          { "id": "urn:li:image:B" }
        ]
      }
    }
  },
  "article": {
    "id": "urn:li:share:444",
    "commentary": "Read this",
    "content": {
      "article": { "source": "https://example.com/post" }
    }
  },
  "video": {
    "id": "urn:li:ugcPost:555",
    "commentary": "Watch",
    "content": {
      "media": { "id": "urn:li:video:XYZ" }
    }
  },
  "poll": {
    "id": "urn:li:ugcPost:666",
    "commentary": "Vote",
    "content": { "poll": { "question": "A?" } }
  }
}
```

```python
# linkedin/tests/test_mapping.py
import json
import os

from linkedin.mapping import (
    PostKind,
    build_message_text,
    classify_and_map,
    permalink_for,
)

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "posts_samples.json")


def _samples():
    with open(_FIXTURES) as f:
        return json.load(f)


def test_permalink():
    assert permalink_for("urn:li:share:111") == (
        "https://www.linkedin.com/feed/update/urn:li:share:111"
    )


def test_classify_kinds():
    samples = _samples()
    assert classify_and_map(samples["text"]).kind == PostKind.TEXT
    assert classify_and_map(samples["image"]).kind == PostKind.IMAGE
    assert classify_and_map(samples["image"]).image_urns == ["urn:li:image:ABC"]
    multi = classify_and_map(samples["multi"])
    assert multi.kind == PostKind.MULTI_IMAGE
    assert multi.image_urns == ["urn:li:image:A", "urn:li:image:B"]
    art = classify_and_map(samples["article"])
    assert art.kind == PostKind.ARTICLE
    assert art.article_url == "https://example.com/post"
    assert classify_and_map(samples["video"]).kind == PostKind.VIDEO
    assert classify_and_map(samples["poll"]).kind == PostKind.SKIP


def test_build_message_appends_permalink_last():
    text = build_message_text("Hello", "https://www.linkedin.com/feed/update/urn:li:share:1", None, 4096)
    assert text.endswith("https://www.linkedin.com/feed/update/urn:li:share:1")
    assert text.startswith("Hello")


def test_build_message_includes_article_url():
    text = build_message_text(
        "Read",
        "https://www.linkedin.com/feed/update/urn:li:share:1",
        "https://example.com/x",
        4096,
    )
    assert "https://example.com/x" in text
    assert text.splitlines()[-1] == "https://www.linkedin.com/feed/update/urn:li:share:1"


def test_build_message_truncates_body_keeps_permalink():
    permalink = "https://www.linkedin.com/feed/update/urn:li:share:1"
    body = "x" * 5000
    text = build_message_text(body, permalink, None, max_chars=100)
    assert len(text) <= 100
    assert text.endswith(permalink)
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `venv/bin/pytest linkedin/tests/test_mapping.py -v`

- [ ] **Step 3: Implement `linkedin/mapping.py`**

```python
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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `venv/bin/pytest linkedin/tests/test_mapping.py -v`

- [ ] **Step 5: Commit**

```bash
git add linkedin/mapping.py linkedin/tests/test_mapping.py linkedin/tests/fixtures/posts_samples.json
git commit -m "$(cat <<'EOF'
feat(linkedin): classify posts and format Telegram captions

EOF
)"
```

---

### Task 4: LinkedIn REST client

**Files:**
- Create: `linkedin/client.py`
- Create: `linkedin/tests/test_client.py`

**Interfaces:**
- Produces:
  - `LINKEDIN_API_VERSION = "202511"`
  - `LinkedInClient(session, access_token: str)`
  - `list_organization_posts(org_id: str, count: int = 50) -> list[dict]`  
    `GET https://api.linkedin.com/rest/posts?author=urn%3Ali%3Aorganization%3A{org_id}&q=author&count={count}&sortBy=CREATED`  
    Headers: `Authorization`, `LinkedIn-Version`, `X-Restli-Protocol-Version: 2.0.0`  
    Return `elements` list (or empty). On HTTP 429: sleep once (2s) and retry once; still failing → raise `RuntimeError`.
  - `get_image_download_url(image_urn: str) -> str`  
    `GET /rest/images/{url-encoded urn}` → `downloadUrl` field; raise if missing.
  - `download_bytes(url: str) -> bytes`
  - `list_administered_orgs() -> list[dict]` (for oauth helper): `GET /rest/organizationAcls?q=roleAssignee` → elements (best-effort; parse org URN from `organization` field)

- [ ] **Step 1: Write failing tests with mocked session**

```python
# linkedin/tests/test_client.py
from unittest.mock import MagicMock

from linkedin.client import LINKEDIN_API_VERSION, LinkedInClient


def test_list_organization_posts_parses_elements():
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"elements": [{"id": "urn:li:share:1"}]}
    session.get.return_value = resp
    client = LinkedInClient(session, "tok")
    posts = client.list_organization_posts("123", count=10)
    assert posts == [{"id": "urn:li:share:1"}]
    args, kwargs = session.get.call_args
    assert "https://api.linkedin.com/rest/posts" in args[0]
    assert kwargs["headers"]["LinkedIn-Version"] == LINKEDIN_API_VERSION
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


def test_get_image_download_url():
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"downloadUrl": "https://media.example/img.jpg"}
    session.get.return_value = resp
    client = LinkedInClient(session, "tok")
    assert client.get_image_download_url("urn:li:image:ABC") == "https://media.example/img.jpg"


def test_list_posts_retries_once_on_429():
    session = MagicMock()
    bad = MagicMock(status_code=429, text="slow down")
    good = MagicMock(status_code=200)
    good.json.return_value = {"elements": []}
    session.get.side_effect = [bad, good]
    client = LinkedInClient(session, "tok")
    assert client.list_organization_posts("123") == []
    assert session.get.call_count == 2
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `venv/bin/pytest linkedin/tests/test_client.py -v`

- [ ] **Step 3: Implement `linkedin/client.py`**

```python
# linkedin/client.py
import time
from typing import Any, Dict, List
from urllib.parse import quote

LINKEDIN_API_VERSION = "202511"
API_BASE = "https://api.linkedin.com/rest"


class LinkedInClient:
    def __init__(self, session, access_token: str):
        self.session = session
        self.access_token = access_token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "LinkedIn-Version": LINKEDIN_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def list_organization_posts(self, org_id: str, count: int = 50) -> List[Dict[str, Any]]:
        author = quote(f"urn:li:organization:{org_id}", safe="")
        url = (
            f"{API_BASE}/posts?author={author}&q=author&count={count}&sortBy=CREATED"
        )
        resp = self.session.get(url, headers=self._headers(), timeout=30)
        if resp.status_code == 429:
            time.sleep(2)
            resp = self.session.get(url, headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(
                f"list posts failed HTTP {resp.status_code}: {resp.text[:300]}"
            )
        payload = resp.json()
        elements = payload.get("elements")
        return elements if isinstance(elements, list) else []

    def get_image_download_url(self, image_urn: str) -> str:
        encoded = quote(image_urn, safe="")
        url = f"{API_BASE}/images/{encoded}"
        resp = self.session.get(url, headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(
                f"get image failed HTTP {resp.status_code}: {resp.text[:300]}"
            )
        download_url = (resp.json() or {}).get("downloadUrl")
        if not download_url:
            raise RuntimeError(f"image {image_urn} missing downloadUrl")
        return str(download_url)

    def download_bytes(self, url: str) -> bytes:
        resp = self.session.get(url, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"download failed HTTP {resp.status_code}")
        return resp.content

    def list_administered_orgs(self) -> List[Dict[str, Any]]:
        url = f"{API_BASE}/organizationAcls?q=roleAssignee"
        resp = self.session.get(url, headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(
                f"organizationAcls failed HTTP {resp.status_code}: {resp.text[:300]}"
            )
        elements = (resp.json() or {}).get("elements")
        return elements if isinstance(elements, list) else []
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `venv/bin/pytest linkedin/tests/test_client.py -v`

- [ ] **Step 5: Commit**

```bash
git add linkedin/client.py linkedin/tests/test_client.py
git commit -m "$(cat <<'EOF'
feat(linkedin): add Community Management REST client

EOF
)"
```

---

### Task 5: Telegram outgoing helpers

**Files:**
- Create: `linkedin/telegram_out.py`
- Create: `linkedin/tests/test_telegram_out.py`

**Interfaces:**
- Produces:
  - `TG_MESSAGE_MAX = 4096`
  - `TG_CAPTION_MAX = 1024`
  - `send_text(bot_token: str, chat_id: int, text: str, session) -> bool`
  - `send_photo(bot_token: str, chat_id: int, photo_bytes: bytes, caption: str, session) -> bool`
  - `send_media_group(bot_token: str, chat_id: int, photos: list[bytes], caption: str, session) -> bool`  
    Multipart `sendMediaGroup` with `media` JSON listing `InputMediaPhoto`; attach files as `file0`, `file1`, …; put `caption` only on first item. No `parse_mode`.

- [ ] **Step 1: Write failing tests**

```python
# linkedin/tests/test_telegram_out.py
from unittest.mock import MagicMock

from linkedin.telegram_out import send_media_group, send_photo, send_text


def test_send_text_ok():
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=200, text="ok")
    assert send_text("tok", -1001, "hi", session) is True
    kwargs = session.post.call_args.kwargs
    assert kwargs["json"]["parse_mode"] is None or "parse_mode" not in kwargs["json"]
    assert kwargs["json"]["text"] == "hi"


def test_send_photo_ok():
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=200, text="ok")
    assert send_photo("tok", -1001, b"img", "cap", session) is True


def test_send_media_group_ok():
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=200, text="ok")
    assert send_media_group("tok", -1001, [b"a", b"b"], "cap", session) is True
    args, kwargs = session.post.call_args
    assert "sendMediaGroup" in args[0]
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `venv/bin/pytest linkedin/tests/test_telegram_out.py -v`

- [ ] **Step 3: Implement `linkedin/telegram_out.py`**

```python
# linkedin/telegram_out.py
import json
from typing import List

TG_MESSAGE_MAX = 4096
TG_CAPTION_MAX = 1024
API = "https://api.telegram.org/bot{token}/{method}"


def send_text(bot_token: str, chat_id: int, text: str, session) -> bool:
    resp = session.post(
        API.format(token=bot_token, method="sendMessage"),
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": False,
        },
        timeout=30,
    )
    return resp.status_code == 200


def send_photo(
    bot_token: str, chat_id: int, photo_bytes: bytes, caption: str, session
) -> bool:
    resp = session.post(
        API.format(token=bot_token, method="sendPhoto"),
        data={"chat_id": str(chat_id), "caption": caption},
        files={"photo": ("image.jpg", photo_bytes, "image/jpeg")},
        timeout=60,
    )
    return resp.status_code == 200


def send_media_group(
    bot_token: str,
    chat_id: int,
    photos: List[bytes],
    caption: str,
    session,
) -> bool:
    if not photos:
        return False
    media = []
    files = {}
    for i, blob in enumerate(photos):
        name = f"file{i}"
        item = {"type": "photo", "media": f"attach://{name}"}
        if i == 0 and caption:
            item["caption"] = caption
        media.append(item)
        files[name] = (f"{name}.jpg", blob, "image/jpeg")
    resp = session.post(
        API.format(token=bot_token, method="sendMediaGroup"),
        data={"chat_id": str(chat_id), "media": json.dumps(media)},
        files=files,
        timeout=120,
    )
    return resp.status_code == 200
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `venv/bin/pytest linkedin/tests/test_telegram_out.py -v`

- [ ] **Step 5: Commit**

```bash
git add linkedin/telegram_out.py linkedin/tests/test_telegram_out.py
git commit -m "$(cat <<'EOF'
feat(linkedin): add Telegram plain-text photo send helpers

EOF
)"
```

---

### Task 6: Forward orchestration helpers

**Files:**
- Create: `linkedin/forward.py`
- Create: `linkedin/tests/test_forward.py`

**Interfaces:**
- Consumes: `classify_and_map`, `build_message_text`, `PostKind`, telegram senders, client image download, `mark_seen`
- Produces:
  - `new_posts(posts: list[dict], seen: dict) -> list[dict]` — filter unseen; sort by `createdAt` ascending (missing → 0)
  - `seed_state(state: dict, posts: list[dict], now_iso: str) -> dict` — mark all ids seen, `seeded=True`
  - `forward_mapped_post(mapped, li_client, bot_token, chat_id, session) -> bool` — dispatch by kind; `SKIP` returns `True` (intentional); VIDEO/ARTICLE/TEXT use `send_text` with caption limits; IMAGE/MULTI download then photo/album
  - `process_posts(...)` — for each new post: map → forward → mark_seen + save if not dry_run; on forward False leave unseen

- [ ] **Step 1: Write failing tests**

```python
# linkedin/tests/test_forward.py
from unittest.mock import MagicMock

from linkedin.forward import forward_mapped_post, new_posts, seed_state
from linkedin.mapping import MappedPost, PostKind
from linkedin.state import DEFAULT_STATE


def test_seed_state_marks_all():
    state = dict(DEFAULT_STATE)
    posts = [{"id": "urn:li:share:1"}, {"id": "urn:li:share:2"}]
    seed_state(state, posts, "2026-11-13T00:00:00+00:00")
    assert state["seeded"] is True
    assert set(state["seen"]) == {"urn:li:share:1", "urn:li:share:2"}


def test_new_posts_sorted_oldest_first():
    seen = {"urn:li:share:old": {}}
    posts = [
        {"id": "urn:li:share:b", "createdAt": 200},
        {"id": "urn:li:share:a", "createdAt": 100},
        {"id": "urn:li:share:old", "createdAt": 50},
    ]
    out = new_posts(posts, seen)
    assert [p["id"] for p in out] == ["urn:li:share:a", "urn:li:share:b"]


def test_forward_skip_returns_true_without_telegram():
    mapped = MappedPost("urn:li:share:1", PostKind.SKIP, "x", "https://permalink")
    session = MagicMock()
    ok = forward_mapped_post(mapped, MagicMock(), "tok", -1001, session)
    assert ok is True
    session.post.assert_not_called()


def test_forward_video_sends_text_only():
    mapped = MappedPost(
        "urn:li:ugcPost:1",
        PostKind.VIDEO,
        "Watch",
        "https://www.linkedin.com/feed/update/urn:li:ugcPost:1",
    )
    session = MagicMock()
    session.post.return_value = MagicMock(status_code=200)
    li = MagicMock()
    assert forward_mapped_post(mapped, li, "tok", -1001, session) is True
    li.get_image_download_url.assert_not_called()
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `venv/bin/pytest linkedin/tests/test_forward.py -v`

- [ ] **Step 3: Implement `linkedin/forward.py`**

```python
# linkedin/forward.py
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
    return True


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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `venv/bin/pytest linkedin/tests/test_forward.py -v`

- [ ] **Step 5: Commit**

```bash
git add linkedin/forward.py linkedin/tests/test_forward.py
git commit -m "$(cat <<'EOF'
feat(linkedin): add seed/diff/forward orchestration

EOF
)"
```

---

### Task 7: Cron entrypoint + shell wrapper

**Files:**
- Create: `linkedin_forwarder.py`
- Create: `linkedin_forwarder.sh`
- Create: `linkedin/tests/test_forwarder_cli.py` (optional light test of arg parsing / lock skip via extracting `run`)

**Interfaces:**
- `main() -> int` with `--dry-run`, `--seed-only`
- Paths: `data/linkedin_forwarder_state.json`, `data/linkedin_tokens.json`, `data/linkedin_forwarder.lock`
- Env: `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, `LINKEDIN_ORGANIZATION_ID`; `BOT_TOKEN` + `TARGET_GROUP_ID` from `config`
- Flow: lock → load tokens via `ensure_access_token` → `LinkedInClient.list_organization_posts` → if `--seed-only` force `state["seeded"]=False` before `process_posts` → else normal `process_posts`

- [ ] **Step 1: Implement `linkedin_forwarder.py`**

```python
#!/usr/bin/env python3
"""LinkedIn company-page → Telegram forwarder cron job."""

import argparse
import fcntl
import logging
import os
import sys
import time

import requests

from config import BOT_TOKEN, TARGET_GROUP_ID
from linkedin.client import LinkedInClient
from linkedin.forward import process_posts
from linkedin.state import load_state
from linkedin.tokens import ensure_access_token

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_ROOT, "data", "linkedin_forwarder_state.json")
TOKEN_FILE = os.path.join(_ROOT, "data", "linkedin_tokens.json")
LOCK_FILE = os.path.join(_ROOT, "data", "linkedin_forwarder.lock")
REFRESH_SKEW = 300.0


def run(args) -> int:
    client_id = os.getenv("LINKEDIN_CLIENT_ID") or ""
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET") or ""
    org_id = os.getenv("LINKEDIN_ORGANIZATION_ID") or ""
    if not client_id or not client_secret or not org_id:
        logger.error("LINKEDIN_CLIENT_ID/SECRET/ORGANIZATION_ID required")
        return 1
    if not os.path.exists(TOKEN_FILE):
        logger.error("Missing %s — run linkedin_oauth.py first", TOKEN_FILE)
        return 1

    session = requests.Session()
    try:
        access = ensure_access_token(
            TOKEN_FILE,
            client_id,
            client_secret,
            session,
            now=time.time(),
            skew_seconds=REFRESH_SKEW,
        )
    except Exception as exc:
        logger.error("Token ensure failed: %s", exc)
        return 1

    li = LinkedInClient(session, access)
    try:
        posts = li.list_organization_posts(org_id, count=50)
    except Exception as exc:
        logger.error("List posts failed: %s", exc)
        return 1

    state = load_state(STATE_FILE)
    if args.seed_only:
        state["seeded"] = False
        state["seen"] = {}

    handled = process_posts(
        state=state,
        posts=posts,
        li_client=li,
        bot_token=BOT_TOKEN,
        chat_id=TARGET_GROUP_ID,
        session=session,
        state_path=STATE_FILE,
        dry_run=args.dry_run,
    )
    logger.info("Run complete; handled=%d dry_run=%s", handled, args.dry_run)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LinkedIn → Telegram forwarder")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()

    os.makedirs(os.path.join(_ROOT, "data"), exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.info("Another linkedin_forwarder run is active; exiting")
        return 0
    try:
        return run(args)
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Implement `linkedin_forwarder.sh`**

```bash
#!/bin/bash

cd /root/tg_sentinel || exit 1

exec > >(tee -a /root/tg_sentinel/linkedin_forwarder_output.log) 2>&1

source venv/bin/activate || { echo "Failed to activate virtual environment"; exit 1; }
echo "Activated virtual environment"

python linkedin_forwarder.py "$@"
```

```bash
chmod +x linkedin_forwarder.sh
```

- [ ] **Step 3: Smoke-import check**

Run: `venv/bin/python -c "import linkedin_forwarder; print('ok')"`  
Expected: `ok` (requires normal `.env` already used by the project)

- [ ] **Step 4: Run full unit suite**

Run: `venv/bin/pytest linkedin/tests -v`  
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add linkedin_forwarder.py linkedin_forwarder.sh
git commit -m "$(cat <<'EOF'
feat(linkedin): add cron entrypoint and shell wrapper

EOF
)"
```

---

### Task 8: OAuth CLI + docs + env example

**Files:**
- Create: `linkedin_oauth.py`
- Modify: `.env.example` (append LinkedIn section)
- Modify: `README.md` (services table + setup + crontab)

**Interfaces:**
- CLI prints authorize URL, reads pasted redirect URL or `code=`, exchanges token, saves `data/linkedin_tokens.json`, optionally lists orgs via `LinkedInClient.list_administered_orgs`.
- Scopes string constant: `r_organization_social rw_organization_admin` (space-delimited; adjust if portal shows different granted names after CMA approval).
- Default redirect: `LINKEDIN_REDIRECT_URI` or `https://www.linkedin.com/developers/tools/oauth/redirect`

- [ ] **Step 1: Implement `linkedin_oauth.py`**

```python
#!/usr/bin/env python3
"""One-shot LinkedIn OAuth for the company-page forwarder."""

import argparse
import logging
import os
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv

from linkedin.client import LinkedInClient
from linkedin.tokens import bundle_from_oauth_response, save_tokens

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(_ROOT, "data", "linkedin_tokens.json")
AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
SCOPES = "r_organization_social rw_organization_admin"


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": SCOPES,
        }
    )
    return f"{AUTH_URL}?{query}"


def extract_code(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("http"):
        qs = parse_qs(urlparse(raw).query)
        code = (qs.get("code") or [None])[0]
        if not code:
            raise ValueError("Redirect URL missing code=")
        return code
    return raw


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str, session) -> dict:
    resp = session.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed HTTP {resp.status_code}: {resp.text[:400]}")
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-orgs", action="store_true", help="after auth, list administered orgs")
    args = parser.parse_args()

    client_id = os.getenv("LINKEDIN_CLIENT_ID") or ""
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET") or ""
    redirect_uri = (
        os.getenv("LINKEDIN_REDIRECT_URI")
        or "https://www.linkedin.com/developers/tools/oauth/redirect"
    )
    if not client_id or not client_secret:
        logger.error("Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in .env")
        return 1

    state = "tg-sentinel-linkedin"
    url = build_authorize_url(client_id, redirect_uri, state)
    print("\n1) Open this URL as a LinkedIn Page admin:\n")
    print(url)
    print("\n2) Paste the full redirect URL (or just the code) here:\n")
    raw = input("> ").strip()
    code = extract_code(raw)

    session = requests.Session()
    payload = exchange_code(client_id, client_secret, redirect_uri, code, session)
    bundle = bundle_from_oauth_response(payload, now=time.time())
    if not bundle.refresh_token:
        logger.warning(
            "No refresh_token in response — LinkedIn may not have issued "
            "programmatic refresh for this app yet; you will need to re-auth when access expires"
        )
    os.makedirs(os.path.join(_ROOT, "data"), exist_ok=True)
    save_tokens(TOKEN_FILE, bundle)
    logger.info("Wrote %s", TOKEN_FILE)

    if args.list_orgs:
        li = LinkedInClient(session, bundle.access_token)
        try:
            orgs = li.list_administered_orgs()
        except Exception as exc:
            logger.error("Could not list orgs (CMA pending?): %s", exc)
            return 0
        print("\nAdministered orgs / ACLs:")
        for row in orgs:
            print(row)
        print("\nSet LINKEDIN_ORGANIZATION_ID to the numeric id from urn:li:organization:{id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Append to `.env.example`**

```bash
# LinkedIn → Telegram forwarder (Community Management API)
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_ORGANIZATION_ID=
# Must match an Authorized redirect URL on the LinkedIn app Auth tab
LINKEDIN_REDIRECT_URI=https://www.linkedin.com/developers/tools/oauth/redirect
```

- [ ] **Step 3: Update README.md**

Add a services-table row:

| **LinkedIn forwarder** | Cron: poll company page posts → Telegram `TARGET_GROUP_ID` | `python linkedin_forwarder.py` | crontab (`linkedin_forwarder.sh`) | Pending CMA approval |

Add setup bullets:

1. Fill `LINKEDIN_*` in `.env`.
2. After Community Management API approval, run `python linkedin_oauth.py --list-orgs` as a page admin; set `LINKEDIN_ORGANIZATION_ID`.
3. First cron/manual run seeds state (`python linkedin_forwarder.py` or `--seed-only`).
4. Crontab: `*/5 * * * * /root/tg_sentinel/linkedin_forwarder.sh`

- [ ] **Step 4: Add a tiny unit test for authorize URL / code extract**

```python
# linkedin/tests/test_oauth_helpers.py
from linkedin_oauth import build_authorize_url, extract_code


def test_build_authorize_url_contains_scopes():
    url = build_authorize_url("cid", "https://example.com/cb", "st")
    assert "client_id=cid" in url
    assert "r_organization_social" in url


def test_extract_code_from_redirect():
    assert (
        extract_code("https://example.com/cb?code=ABC&state=st") == "ABC"
    )
```

Note: importing `linkedin_oauth` loads dotenv; should be fine. If `config` is not involved, OK.

Run: `venv/bin/pytest linkedin/tests -v`

- [ ] **Step 5: Commit**

```bash
git add linkedin_oauth.py .env.example README.md linkedin/tests/test_oauth_helpers.py
git commit -m "$(cat <<'EOF'
feat(linkedin): add OAuth CLI and document cron setup

EOF
)"
```

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|---|---|
| Cron poller like Luma | Task 7 |
| OAuth + token file + refresh | Tasks 2, 8 |
| Seed-only first run | Task 6 `process_posts` / `seed_state` |
| Text / image / multi / article / video / skip | Task 3 + 6 |
| No video download | Task 6 video branch |
| Always permalink last line | Task 3 `build_message_text` |
| Images via Images API | Tasks 4–6 |
| File lock | Task 7 |
| `--dry-run` / `--seed-only` | Task 7 |
| Seen after success only | Task 6 |
| Prune ~500 | Task 6 `SEEN_CAP` |
| `.env.example` + README | Task 8 |
| Offline tests | Tasks 1–6, 8 |
| Not in `start_all.sh` | honored (no task adds it) |
| CMA pending OK | docs + oauth warnings |

## Execution notes

- End-to-end live smoke waits on Community Management API approval.
- After approval: oauth → set org id → one forwarder run (seed) → publish test LinkedIn post → wait ≤5 min → confirm Telegram.
- If LinkedIn’s live post JSON shape differs from fixtures (commentary/content nesting), adjust `classify_and_map` with a new fixture captured from a real `--dry-run` response — do not scrape HTML.
