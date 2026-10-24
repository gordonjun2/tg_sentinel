import pytest

import admin_alert
from admin_alert import notify_llm_failure


@pytest.fixture(autouse=True)
def _reset_throttle():
    admin_alert._last_sent.clear()
    admin_alert._suppressed.clear()
    yield
    admin_alert._last_sent.clear()
    admin_alert._suppressed.clear()


@pytest.fixture
def payload_capture(monkeypatch):
    payloads = []

    class FakeResp:
        status_code = 200
        text = ""

    def _fake_post(url, json=None, timeout=None):
        payloads.append({"url": url, "json": json})
        return FakeResp()

    monkeypatch.setattr(admin_alert.requests, "post", _fake_post)
    return payloads


def test_alert_states_provider_model_and_fallback(payload_capture):
    notify_llm_failure(
        provider="gemini",
        model="gemini-3.8-flash",
        task="score_url_content",
        error=RuntimeError("404 NOT_FOUND: model no longer available"),
        fallback="openai",
    )
    assert len(payload_capture) == 1
    text = payload_capture[0]["json"]["text"]
    assert "gemini" in text
    assert "gemini-3.8-flash" in text
    assert "score_url_content" in text
    assert "Falling back to: openai" in text
    assert "404" in text


def test_alert_last_attempt_reports_no_fallback(payload_capture):
    notify_llm_failure(
        provider="openai",
        model="gpt-5-mini",
        task="digest",
        error=RuntimeError("rate limited"),
        fallback=None,
    )
    text = payload_capture[0]["json"]["text"]
    assert "No further provider in chain — task FAILED" in text


def test_alert_targets_admin_group(payload_capture):
    notify_llm_failure(
        provider="gemini",
        model="gemini-3.8-flash",
        task="task",
        error=RuntimeError("boom"),
    )
    url = payload_capture[0]["url"]
    json_body = payload_capture[0]["json"]
    assert url.startswith("https://api.telegram.org/bot")
    assert json_body["chat_id"] == admin_alert.ADMIN_GROUP_ID


def test_alert_throttles_repeated_failures(payload_capture):
    for _ in range(5):
        notify_llm_failure(
            provider="gemini",
            model="gemini-3.8-flash",
            task="task",
            error=RuntimeError("boom"),
        )
    assert len(payload_capture) == 1
    assert admin_alert._suppressed[("gemini", "gemini-3.8-flash")] == 4


def test_suppressed_count_reported_after_throttle_window(payload_capture):
    notify_llm_failure(
        provider="gemini",
        model="gemini-3.8-flash",
        task="task",
        error=RuntimeError("boom"),
    )
    for _ in range(3):
        notify_llm_failure(
            provider="gemini",
            model="gemini-3.8-flash",
            task="task",
            error=RuntimeError("boom"),
        )
    admin_alert._last_sent[("gemini", "gemini-3.8-flash")] = 0.0
    notify_llm_failure(
        provider="gemini",
        model="gemini-3.8-flash",
        task="task",
        error=RuntimeError("boom"),
    )
    assert len(payload_capture) == 2
    assert "(+3 repeat failure(s) suppressed)" in payload_capture[1]["json"]["text"]
    assert ("gemini", "gemini-3.8-flash") not in admin_alert._suppressed


def test_chain_wires_default_listener_to_admin_alert(monkeypatch, payload_capture):
    from llm_chain import reset_provider_health, run_llm_chain, set_failure_listener

    reset_provider_health()
    set_failure_listener(None)

    def _boom():
        raise RuntimeError("404 model no longer available")

    run_llm_chain(
        "task",
        [
            ("gemini", "gemini-3.8-flash", _boom),
            ("openai", "gpt-5-mini", lambda: "ok"),
        ],
    )

    assert len(payload_capture) == 1
    text = payload_capture[0]["json"]["text"]
    assert "gemini-3.8-flash" in text
    assert "Falling back to: openai" in text
    reset_provider_health()
