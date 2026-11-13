from unittest.mock import MagicMock, patch

from linkedin.forward import forward_mapped_post, new_posts, process_posts, seed_state
from linkedin.mapping import MappedPost, PostKind
from linkedin.state import default_state, load_state


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


def test_process_posts_unseeded_seeds_without_telegram(tmp_path):
    state_path = str(tmp_path / "state.json")
    state = default_state()
    posts = [
        {"id": "urn:li:share:1", "createdAt": 100},
        {"id": "urn:li:share:2", "createdAt": 200},
    ]
    session = MagicMock()
    handled = process_posts(
        state,
        posts,
        MagicMock(),
        "tok",
        -1001,
        session,
        state_path,
        False,
    )
    assert handled == 0
    session.post.assert_not_called()
    assert state["seeded"] is True
    assert set(state["seen"]) == {"urn:li:share:1", "urn:li:share:2"}
    saved = load_state(state_path)
    assert saved["seeded"] is True
    assert set(saved["seen"]) == {"urn:li:share:1", "urn:li:share:2"}


def test_process_posts_dry_run_unseeded_skips_state_write(tmp_path):
    state_path = str(tmp_path / "state.json")
    state = default_state()
    posts = [{"id": "urn:li:share:1", "createdAt": 100}]
    session = MagicMock()
    handled = process_posts(
        state,
        posts,
        MagicMock(),
        "tok",
        -1001,
        session,
        state_path,
        True,
    )
    assert handled == 0
    assert state["seeded"] is False
    assert not (tmp_path / "state.json").exists()
    session.post.assert_not_called()


def test_process_posts_seeded_skip_marked_seen(tmp_path):
    state_path = str(tmp_path / "state.json")
    state = default_state()
    state["seeded"] = True
    state["seen"] = {"urn:li:share:old": {"first_seen": "2026-01-01T00:00:00+00:00"}}
    posts = [
        {
            "id": "urn:li:share:new-poll",
            "createdAt": 300,
            "commentary": "vote",
            "content": {"poll": {"question": "x"}},
        }
    ]
    session = MagicMock()
    handled = process_posts(
        state,
        posts,
        MagicMock(),
        "tok",
        -1001,
        session,
        state_path,
        False,
    )
    assert handled == 1
    assert "urn:li:share:new-poll" in state["seen"]
    session.post.assert_not_called()
    saved = load_state(state_path)
    assert "urn:li:share:new-poll" in saved["seen"]


@patch("linkedin.forward.telegram_out.send_text", return_value=False)
def test_process_posts_forward_failure_not_marked_seen(mock_send_text, tmp_path):
    state_path = str(tmp_path / "state.json")
    state = default_state()
    state["seeded"] = True
    state["seen"] = {"urn:li:share:old": {"first_seen": "2026-01-01T00:00:00+00:00"}}
    posts = [
        {
            "id": "urn:li:share:new-text",
            "createdAt": 300,
            "commentary": "hello",
        }
    ]
    session = MagicMock()
    handled = process_posts(
        state,
        posts,
        MagicMock(),
        "tok",
        -1001,
        session,
        state_path,
        False,
    )
    assert handled == 0
    assert "urn:li:share:new-text" not in state["seen"]
    mock_send_text.assert_called_once()
    assert not (tmp_path / "state.json").exists()
