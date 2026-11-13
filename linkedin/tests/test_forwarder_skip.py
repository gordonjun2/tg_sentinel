import sys
from unittest.mock import patch

import linkedin_forwarder


def test_linkedin_env_ready_false_when_missing(monkeypatch):
    for key in linkedin_forwarder._REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    assert linkedin_forwarder.linkedin_env_ready() is False


def test_linkedin_env_ready_true_when_all_set(monkeypatch):
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "id")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "secret")
    monkeypatch.setenv("LINKEDIN_ORGANIZATION_ID", "123")
    assert linkedin_forwarder.linkedin_env_ready() is True


def test_main_skips_when_env_missing(monkeypatch):
    for key in linkedin_forwarder._REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, "argv", ["linkedin_forwarder.py"])
    with patch.object(linkedin_forwarder, "run") as run_mock:
        code = linkedin_forwarder.main()
    assert code == 0
    run_mock.assert_not_called()
