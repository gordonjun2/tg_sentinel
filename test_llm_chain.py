import pytest

import llm_chain
from llm_chain import (
    LLMChainError,
    provider_available,
    reset_provider_health,
    run_llm_chain,
    set_failure_listener,
)


@pytest.fixture(autouse=True)
def _reset_health():
    reset_provider_health()
    yield
    reset_provider_health()


@pytest.fixture(autouse=True)
def _record_alerts():
    alerts = []
    set_failure_listener(lambda **kw: alerts.append(kw))
    yield alerts
    set_failure_listener(None)


def test_first_provider_success():
    outcome = run_llm_chain(
        "task",
        [
            ("gemini", "g-model", lambda: "g"),
            ("openai", "o-model", lambda: "o"),
        ],
    )
    assert outcome.result == "g"
    assert outcome.provider == "gemini"


def test_falls_back_to_next_provider():
    def _boom():
        raise RuntimeError("gemini down")

    outcome = run_llm_chain(
        "task",
        [
            ("gemini", "g-model", _boom),
            ("openai", "o-model", lambda: "o"),
        ],
    )
    assert outcome.result == "o"
    assert outcome.provider == "openai"


def test_partial_failure_does_not_raise():
    def _boom():
        raise RuntimeError("transient blip")

    outcome = run_llm_chain(
        "task",
        [
            ("gemini", "g-model", _boom),
            ("openai", "o-model", lambda: "ok"),
        ],
    )
    assert outcome.result == "ok"


def test_total_failure_only_when_all_providers_fail():
    def _boom(name):
        def _f():
            raise RuntimeError(f"{name} down")

        return _f

    with pytest.raises(LLMChainError) as excinfo:
        run_llm_chain(
            "task",
            [
                ("gemini", "g-model", _boom("gemini")),
                ("openai", "o-model", _boom("openai")),
            ],
        )
    assert "gemini" in str(excinfo.value)
    assert "openai" in str(excinfo.value)


def test_chain_survives_until_last_provider_succeeds():
    def _boom(name):
        def _f():
            raise RuntimeError(f"{name} down")

        return _f

    outcome = run_llm_chain(
        "task",
        [
            ("p1", "m1", _boom("p1")),
            ("p2", "m2", _boom("p2")),
            ("p3", "m3", lambda: "third"),
        ],
    )
    assert outcome.result == "third"
    assert outcome.provider == "p3"


def test_all_providers_skipped_raises_without_calling_thunks():
    from llm_chain import record_failure

    record_failure("gemini", RuntimeError("404 no longer available"))
    record_failure("openai", RuntimeError("404 no longer available"))
    assert provider_available("gemini") is False
    assert provider_available("openai") is False

    called = []

    with pytest.raises(LLMChainError):
        run_llm_chain(
            "task",
            [
                ("gemini", "g-model", lambda: called.append("gemini")),
                ("openai", "o-model", lambda: called.append("openai")),
            ],
        )
    assert called == []


def test_permanent_error_opens_circuit_immediately():
    def _gone():
        raise RuntimeError(
            "404 NOT_FOUND. {'error': {'message': 'This model is no longer available'}}"
        )

    outcome = run_llm_chain(
        "task",
        [
            ("gemini", "g-model", _gone),
            ("openai", "o-model", lambda: "o"),
        ],
    )
    assert outcome.provider == "openai"

    assert provider_available("gemini") is False

    calls = []

    outcome = run_llm_chain(
        "task",
        [
            ("gemini", "g-model", lambda: calls.append("gemini")),
            ("openai", "o-model", lambda: "o"),
        ],
    )
    assert calls == []
    assert outcome.provider == "openai"


def test_transient_failures_open_circuit_after_threshold():
    from llm_chain import FAILURE_THRESHOLD, _health

    def _boom():
        raise RuntimeError("timeout")

    for _ in range(FAILURE_THRESHOLD):
        with pytest.raises(LLMChainError):
            run_llm_chain("task", [("gemini", "g-model", _boom)])

    assert provider_available("gemini") is False
    assert _health["gemini"]["failures"] == FAILURE_THRESHOLD


def test_success_resets_failure_count():
    counter = {"n": 0}

    def _flaky():
        counter["n"] += 1
        if counter["n"] == 1:
            raise RuntimeError("transient")
        return "ok"

    with pytest.raises(LLMChainError):
        run_llm_chain("task", [("gemini", "g-model", _flaky)])

    run_llm_chain(
        "task",
        [
            ("gemini", "g-model", _flaky),
            ("openai", "o-model", lambda: "o"),
        ],
    )
    assert provider_available("gemini") is True
    assert counter["n"] == 2


def test_empty_result_counts_as_success():
    outcome = run_llm_chain(
        "task",
        [
            ("gemini", "g-model", lambda: ""),
            ("openai", "o-model", lambda: "o"),
        ],
    )
    assert outcome.result == ""
    assert outcome.provider == "gemini"


def test_listener_fires_on_each_failure_with_model_and_fallback(_record_alerts):
    def _boom(msg):
        def _f():
            raise RuntimeError(msg)

        return _f

    with pytest.raises(LLMChainError):
        run_llm_chain(
            "score_url_content",
            [
                ("gemini", "gemini-3.8-flash", _boom("404 no longer available")),
                ("openai", "gpt-5-mini", _boom("rate limited")),
            ],
        )

    assert len(_record_alerts) == 2
    first, second = _record_alerts
    assert first["provider"] == "gemini"
    assert first["model"] == "gemini-3.8-flash"
    assert first["task"] == "score_url_content"
    assert "404" in str(first["error"])
    assert first["fallback"] == "openai"
    assert second["provider"] == "openai"
    assert second["model"] == "gpt-5-mini"
    assert second["fallback"] is None


def test_listener_not_fired_on_success(_record_alerts):
    run_llm_chain(
        "task",
        [
            ("gemini", "g-model", lambda: "g"),
            ("openai", "o-model", lambda: "o"),
        ],
    )
    assert _record_alerts == []


def test_listener_exception_does_not_break_chain(_record_alerts):
    def _bad_listener(**kwargs):
        raise RuntimeError("listener exploded")

    set_failure_listener(_bad_listener)

    outcome = run_llm_chain(
        "task",
        [
            ("gemini", "g-model", lambda: "g"),
            ("openai", "o-model", lambda: "o"),
        ],
    )
    assert outcome.result == "g"
