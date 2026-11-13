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
