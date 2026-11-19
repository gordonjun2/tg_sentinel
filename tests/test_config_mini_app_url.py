import importlib

import pytest

_REQUIRED_ENV = {
    "BOT_TOKEN": "1:tok",
    "ADMIN_GROUP_ID": "-1001",
    "TARGET_GROUP_ID": "-1002",
    "TARGET_SUB_GROUP_ID": "-1003",
    "GOOGLE_DRIVE_MAIN_FOLDER_ID": "x",
    "GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID": "x",
    "GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID": "x",
    "TELEGRAM_API_KEY": "x",
    "TELEGRAM_HASH": "x",
    "FIRECRAWL_API_KEY": "x",
    "MINI_APP_URL": "https://serendip.sisc.club",
}


def _load_config(monkeypatch: pytest.MonkeyPatch):
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    import config as cfg

    return importlib.reload(cfg)


def test_require_mini_app_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _load_config(monkeypatch)
    with pytest.raises(ValueError, match="MINI_APP_URL"):
        cfg.require_mini_app_url(None)


def test_require_mini_app_url_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _load_config(monkeypatch)
    with pytest.raises(ValueError, match="MINI_APP_URL"):
        cfg.require_mini_app_url("")


def test_require_mini_app_url_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _load_config(monkeypatch)
    with pytest.raises(ValueError, match="MINI_APP_URL"):
        cfg.require_mini_app_url("   ")


def test_require_mini_app_url_strips(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _load_config(monkeypatch)
    assert (
        cfg.require_mini_app_url("  https://serendip.sisc.club  ")
        == "https://serendip.sisc.club"
    )
