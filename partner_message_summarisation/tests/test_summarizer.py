"""Tests for transcript building, chunking, merge, attribution."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from partner_message_summarisation import summarizer
from partner_message_summarisation.summarizer import (
    PartnerMessageSummary,
    GroupSummary,
    UnansweredSummary,
    UnansweredPoint,
    build_chat_block,
    build_unanswered_block,
    chunk_transcripts,
    merge_summaries,
    sanitize_summary,
    summarize_unanswered,
)

def _msg(message_id: int, text: str, **over) -> dict:
    row = {
        "message_id": message_id,
        "chat_title": "SISC <> AI Builders",
        "sender_display_name": "Alice",
        "sender_username": "alice",
        "message_text": text,
        "message_date": datetime(2026, 9, 18, 1, 14, tzinfo=timezone.utc),
        "reply_to_message_id": None,
        "is_forward": False,
        "forward_from_name": None,
        "forward_from_chat_title": None,
        "media_type": None,
        "media_file_name": None,
    }
    row.update(over)
    return row


# -- build_chat_block ----------------------------------------------------------


def test_transcript_chronological_with_sender_and_time() -> None:
    msgs = [
        _msg(2, "second", message_date=datetime(2026, 9, 18, 1, 20, tzinfo=timezone.utc)),
        _msg(1, "first"),
    ]
    block = build_chat_block("SISC <> AI Builders", msgs)
    lines = block.split("\n")
    assert lines[0] == "=== GROUP: SISC <> AI Builders ==="
    assert "Alice (@alice):" in lines[1] and "first" in lines[1]
    assert "second" in lines[2]
    assert lines[1].startswith("[09:14]")  # UTC 01:14 → SGT 09:14
    assert lines[2].startswith("[09:20]")


def test_transcript_message_truncated_to_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(summarizer, "SENTINEL_MAX_MSG_CHARS", 10)
    block = build_chat_block("G", [_msg(1, "x" * 100)])
    line = block.split("\n")[1]
    assert "…" in line
    assert len(line) < 100


def test_transcript_reply_forward_media_annotations() -> None:
    msgs = [
        _msg(1, "root message"),
        _msg(
            2,
            "look",
            reply_to_message_id=1,
            is_forward=True,
            forward_from_name="News Source",
            media_type="DOCUMENT",
            media_file_name="report.pdf",
        ),
    ]
    block = build_chat_block("G", msgs)
    assert "(↪ replying to Alice (@alice))" in block
    assert "News Source:" in block  # original author becomes the speaker
    assert "(⤵ forwarded by Alice (@alice))" in block
    assert "[report.pdf]" in block


def test_transcript_forward_without_known_origin_keeps_sender() -> None:
    msgs = [_msg(1, "look", is_forward=True)]
    block = build_chat_block("G", msgs)
    assert "Alice (@alice):" in block
    assert "(⤵ forwarded, original author hidden)" in block


# -- chunking --------------------------------------------------------------------


def test_chunking_respects_group_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(summarizer, "SENTINEL_MAX_TRANSCRIPT_CHARS", 300)
    chats = [
        ("SISC <> A", [_msg(i, "x" * 60) for i in range(1, 4)]),  # ~small block
        ("SISC <> B", [_msg(i, "y" * 60) for i in range(10, 13)]),
    ]
    chunks = chunk_transcripts(chats)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 300
    # a chunk never mixes two groups mid-block
    for chunk in chunks:
        headers = [ln for ln in chunk.split("\n") if ln.startswith("=== GROUP:")]
        assert len({h for h in headers}) == len(headers)  # no duplicate headers


def test_chunking_oversized_single_group_split_with_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(summarizer, "SENTINEL_MAX_TRANSCRIPT_CHARS", 150)
    chats = [("SISC <> Big", [_msg(i, "z" * 80) for i in range(1, 6)])]
    chunks = chunk_transcripts(chats)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.startswith("=== GROUP: SISC <> Big ===")
        assert len(chunk) <= 150 + 12  # "(cont.)" suffix slack


# -- merge -----------------------------------------------------------------------


def test_merge_unions_groups_across_chunks_and_concats_same_group() -> None:
    d1 = PartnerMessageSummary(
        groups=[
            GroupSummary(group_name="SISC <> A", summary=["part one"], has_signal=True),
            GroupSummary(group_name="SISC <> B", summary=["B happened"], has_signal=True),
        ],
    )
    d2 = PartnerMessageSummary(
        groups=[
            GroupSummary(group_name="SISC <> A", summary=["part two"], has_signal=True),
            GroupSummary(group_name="SISC <> C", summary=["C happened"], has_signal=True),
        ],
    )
    merged = merge_summaries([d1, d2])
    names = [g.group_name for g in merged.groups]
    assert names == ["SISC <> A", "SISC <> B", "SISC <> C"]  # first-seen order
    a = merged.groups[0]
    assert a.summary == ["part one", "part two"]  # chunk parts concatenated


def test_merge_single_summary_passthrough() -> None:
    d = PartnerMessageSummary(
        groups=[GroupSummary(group_name="G", summary=["s1", "s2"], has_signal=True)],
    )
    merged = merge_summaries([d])
    assert merged.groups[0].summary == ["s1", "s2"]


# -- attribution sanitization ------------------------------------------------------


def test_sanitize_drops_untrusted_group_names() -> None:
    summary = PartnerMessageSummary(
        groups=[
            GroupSummary(group_name="SISC <> A", summary=["real"], has_signal=True),
            GroupSummary(group_name="Invented Group", summary=["made up"], has_signal=True),
        ],
    )
    clean = sanitize_summary(summary, {"SISC <> A", "SISC <> B"})
    assert [g.group_name for g in clean.groups] == ["SISC <> A"]
    assert clean.groups[0].summary == ["real"]


def test_sanitize_normalizes_group_name_casing() -> None:
    summary = PartnerMessageSummary(
        groups=[GroupSummary(group_name="sisc <> ai builders", summary=["d"], has_signal=True)],
    )
    clean = sanitize_summary(summary, {"SISC <> AI Builders"})
    assert clean.groups[0].group_name == "SISC <> AI Builders"


# -- fake-LLM injection seam --------------------------------------------------------


def test_generate_summary_merges_chunk_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate_summary() calls the injected summarizer per chunk and merges."""

    def fake_summarize_chunk(transcript: str):
        if "SISC <> A" in transcript:
            marker = "SISC <> A"
        else:
            marker = "SISC <> B"
        return (
            PartnerMessageSummary(
                groups=[
                    GroupSummary(
                        group_name=marker,
                        summary=[f"point in {marker}"],
                        has_signal=True,
                    )
                ],
            ),
            "fake-provider",
            12,
        )

    monkeypatch.setattr(summarizer, "_summarize_chunk", fake_summarize_chunk)
    import asyncio

    summary, provider, latency = asyncio.run(
        summarizer.generate_summary(
            [
                "=== GROUP: SISC <> A ===\n[09:14] Alice: hello",
                "=== GROUP: SISC <> B ===\n[09:15] Bob: hi",
            ]
        )
    )
    assert provider == "fake-provider"
    assert latency == 24
    assert len(summary.groups) == 2


# -- unanswered summarization -----------------------------------------------------


def _unanswered_item(title: str, sender: str, excerpt: str):
    from partner_message_summarisation.unanswered import UnansweredMessage

    return UnansweredMessage(
        chat_title=title,
        sender=sender,
        message_date=datetime(2026, 9, 18, 9, 0, tzinfo=timezone.utc),
        excerpt=excerpt,
    )


def test_build_unanswered_block_lists_chat_sender_text() -> None:
    block = build_unanswered_block(
        [
            _unanswered_item("SISC <> A", "Alice (@alice)", "What's the format?"),
            _unanswered_item("SISC <> B", "Bob", "Can I join?"),
        ]
    )
    lines = block.split("\n")
    assert lines[0] == "[SISC <> A] Alice (@alice): What's the format?"
    assert lines[1] == "[SISC <> B] Bob: Can I join?"


def test_summarize_unanswered_empty_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    def boom(block):
        raise AssertionError("LLM must not be called for empty input")

    monkeypatch.setattr(summarizer, "_summarize_unanswered_block", boom)
    assert asyncio.run(summarize_unanswered([])) == []


def test_summarize_unanswered_calls_llm_and_filters_unknown_chats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def fake_llm(block: str):
        captured.append(block)
        return (
            UnansweredSummary(
                points=[
                    UnansweredPoint(
                        group_name="SISC <> A",
                        point="Alice wants to clarify what the panel format is",
                    ),
                    UnansweredPoint(
                        group_name="Invented Chat",
                        point="Ghost asks something",
                    ),
                ]
            ),
            "fake-provider",
            7,
        )

    monkeypatch.setattr(summarizer, "_summarize_unanswered_block", fake_llm)
    import asyncio

    points = asyncio.run(
        summarize_unanswered(
            [_unanswered_item("SISC <> A", "Alice (@alice)", "What's the format?")]
        )
    )
    assert "[SISC <> A] Alice (@alice): What's the format?" in captured[0]
    assert points == [
        UnansweredPoint(
            group_name="SISC <> A",
            point="Alice wants to clarify what the panel format is",
        )
    ]  # unknown-chat point dropped


def test_response_schema_has_no_gemini_unsupported_defaults() -> None:
    """Gemini's structured-output API rejects schemas containing `default`.

    Scalar pydantic defaults emit a "default" key into the JSON schema;
    ``default_factory`` does not. This pins every LLM-facing model to stay
    free of scalar defaults (regression guard for provider compatibility).
    """
    import json

    from partner_message_summarisation.summarizer import UnansweredPoint

    for model in (PartnerMessageSummary, GroupSummary, UnansweredPoint):
        schema = json.dumps(model.model_json_schema())
        assert '"default"' not in schema, f"{model.__name__} has a scalar default"


def test_gemini_client_mode_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vertex AI mode when SENTINEL_VERTEX_PROJECT is set, AI Studio otherwise."""
    import partner_message_summarisation.config as cfg
    import partner_message_summarisation.summarizer as sm

    calls: list[dict] = []

    class _FakeClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("google.genai.Client", _FakeClient)

    monkeypatch.setattr(cfg, "SENTINEL_VERTEX_PROJECT", "sisc-465108")
    monkeypatch.setattr(cfg, "SENTINEL_VERTEX_LOCATION", "global")
    monkeypatch.setattr(sm, "_gemini_client", None)
    sm._get_gemini()
    assert calls[-1] == {
        "vertexai": True,
        "project": "sisc-465108",
        "location": "global",
    }

    monkeypatch.setattr(cfg, "SENTINEL_VERTEX_PROJECT", "")
    monkeypatch.setattr(sm, "_gemini_client", None)
    monkeypatch.setattr(sm, "GEMINI_API_KEY", "test-key")
    sm._get_gemini()
    assert calls[-1] == {"api_key": "test-key"}
