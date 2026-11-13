from unittest.mock import MagicMock

from linkedin.forward import forward_mapped_post, new_posts, seed_state
from linkedin.mapping import MappedPost, PostKind
from linkedin.state import default_state


def test_seed_state_marks_all():
    state = default_state()
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
