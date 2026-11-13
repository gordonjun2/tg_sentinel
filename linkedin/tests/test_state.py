import os

from linkedin.state import default_state, load_state, mark_seen, prune_seen, save_state


def test_load_missing_returns_default(tmp_path):
    path = str(tmp_path / "state.json")
    state = load_state(path)
    assert state["seeded"] is False
    assert state["seen"] == {}
    assert state["version"] == 1


def test_save_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    state = default_state()
    state["seeded"] = True
    mark_seen(state, "urn:li:share:1", {"first_seen": "2026-11-13T00:00:00+08:00"})
    save_state(path, state)
    loaded = load_state(path)
    assert loaded["seeded"] is True
    assert "urn:li:share:1" in loaded["seen"]
    assert not os.path.exists(path + ".tmp")


def test_prune_keeps_newest(tmp_path):
    state = default_state()
    state["seen"] = {
        "a": {"first_seen": "2026-01-01T00:00:00+00:00"},
        "b": {"first_seen": "2026-06-01T00:00:00+00:00"},
        "c": {"first_seen": "2026-11-01T00:00:00+00:00"},
    }
    removed = prune_seen(state, max_entries=2)
    assert removed == 1
    assert set(state["seen"]) == {"b", "c"}


def test_default_state_isolation():
    state_a = default_state()
    state_b = default_state()
    mark_seen(state_a, "urn:li:share:1", {"first_seen": "2026-11-13T00:00:00+08:00"})
    assert "urn:li:share:1" not in state_b["seen"]
    assert state_b["seen"] == {}
