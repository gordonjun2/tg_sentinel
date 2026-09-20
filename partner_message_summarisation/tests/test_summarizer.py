"""Tests for transcript building, chunking, merge dedupe, attribution."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from partner_message_summarisation import summarizer
from partner_message_summarisation.summarizer import (
    DailyDigest,
    Insight,
    build_chat_block,
    chunk_transcripts,
    merge_digests,
    sanitize_digest,
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
    assert "(⤵ forwarded from News Source)" in block
    assert "[report.pdf]" in block


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


def test_merge_dedupes_similar_titles_and_unions_sources() -> None:
    d1 = DailyDigest(
        highlights=["h1", "h2"],
        insights=[
            Insight(
                title="Project X launch",
                detail="detail a",
                source_groups=["SISC <> A"],
                importance=0.9,
            )
        ],
    )
    d2 = DailyDigest(
        highlights=["h3"],
        insights=[
            Insight(
                title="Project X  launch",  # near-identical after normalization
                detail="detail b",
                source_groups=["SISC <> B"],
                importance=0.7,
            ),
            Insight(
                title="Totally different",
                detail="detail c",
                source_groups=["SISC <> B"],
                importance=0.5,
            ),
        ],
    )
    merged = merge_digests([d1, d2])
    assert len(merged.insights) == 2  # near-identical pair merged
    top = merged.insights[0]
    assert top.title == "Project X launch"
    assert top.importance == 0.9  # max kept
    assert set(top.source_groups) == {"SISC <> A", "SISC <> B"}
    # highlights collected across chunks, deduped, capped at 5
    assert set(merged.highlights) == {"h1", "h2", "h3"}
    assert len(merged.highlights) == 3


def test_merge_resorts_by_importance() -> None:
    d = DailyDigest(
        highlights=[],
        insights=[
            Insight(title="low", detail="d", source_groups=["G"], importance=0.2),
            Insight(title="high", detail="d", source_groups=["G"], importance=0.95),
        ],
    )
    merged = merge_digests([d])
    assert [i.title for i in merged.insights] == ["high", "low"]


# -- attribution sanitization ------------------------------------------------------


def test_sanitize_drops_untrusted_group_names() -> None:
    digest = DailyDigest(
        highlights=[],
        insights=[
            Insight(
                title="good",
                detail="d",
                source_groups=["SISC <> A", "Invented Group"],
                importance=0.8,
            ),
            Insight(
                title="all invented",
                detail="d",
                source_groups=["Made Up"],
                importance=0.9,
            ),
        ],
    )
    clean = sanitize_digest(digest, {"SISC <> A", "SISC <> B"})
    assert len(clean.insights) == 1
    assert clean.insights[0].source_groups == ["SISC <> A"]


# -- fake-LLM injection seam --------------------------------------------------------


def test_generate_digest_merges_chunk_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate_digest() calls the injected summarizer per chunk and merges."""

    def fake_summarize_chunk(transcript: str):
        if "SISC <> A" in transcript:
            marker, title = "SISC <> A", "Robot vacuum launch"
        else:
            marker, title = "SISC <> B", "Founders retreat dates"
        return (
            DailyDigest(
                highlights=[f"hl for {marker}"],
                insights=[
                    Insight(
                        title=title,
                        detail="d",
                        source_groups=[marker],
                        importance=0.5,
                    )
                ],
            ),
            "fake-provider",
            12,
        )

    monkeypatch.setattr(summarizer, "_summarize_chunk", fake_summarize_chunk)
    import asyncio

    digest, provider, latency = asyncio.run(
        summarizer.generate_digest(
            [
                "=== GROUP: SISC <> A ===\n[09:14] Alice: hello",
                "=== GROUP: SISC <> B ===\n[09:15] Bob: hi",
            ]
        )
    )
    assert provider == "fake-provider"
    assert latency == 24
    assert len(digest.insights) == 2
