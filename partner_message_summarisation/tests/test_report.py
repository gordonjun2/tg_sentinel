"""Tests for render_report and split_report."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from partner_message_summarisation.report import (
    TELEGRAM_MESSAGE_LIMIT,
    render_report,
    split_report,
)
from partner_message_summarisation.summarizer import DailyDigest, Insight

END = datetime(2026, 9, 18, 11, 0, tzinfo=timezone.utc)  # 19:00 SGT
START = END - timedelta(days=1)


def _digest() -> DailyDigest:
    return DailyDigest(
        highlights=["Robot vacuum ships", "Retreat dates locked"],
        insights=[
            Insight(
                title="Robot vacuum launch",
                detail="Alice demoed the SISC-built vacuum; ships in October.",
                source_groups=["SISC <> AI Builders"],
                importance=0.92,
            ),
            Insight(
                title="Founders retreat",
                detail="Dates confirmed for the annual retreat; 40 going.",
                source_groups=["SISC <> Founders", "SISC <> AI Builders"],
                importance=0.6,
            ),
        ],
    )


def _counts() -> dict[str, int]:
    return {"SISC <> AI Builders": 96, "SISC <> Founders": 58}


# -- render_report ----------------------------------------------------------------


def test_render_report_snapshot() -> None:
    report = render_report(_digest(), START, END, _counts())
    lines = report.split("\n")
    assert lines[0] == "📜 SISC Daily Digest — Fri 18 Sep 2026"
    assert lines[1] == "Window: 17 Sep 19:00 → 18 Sep 19:00 SGT"
    assert lines[2] == "Groups: 2 · Messages: 154"
    assert "⭐ Highlights" in report
    assert "• Robot vacuum ships" in report
    assert "🔍 Insights" in report
    assert "1. Robot vacuum launch  (0.92)" in report
    assert "   Source: SISC <> AI Builders" in report
    # insights sorted by importance desc
    assert report.index("Robot vacuum launch") < report.index("Founders retreat")
    # coverage sorted by count desc
    assert (
        report.index("SISC <> AI Builders — 96 msgs")
        < report.index("SISC <> Founders — 58 msgs")
    )
    assert "🧾 Coverage" in report


def test_render_report_empty_digest() -> None:
    report = render_report(DailyDigest(), START, END, {})
    assert "• —" in report
    assert report.endswith("—")


# -- split_report -------------------------------------------------------------------


def test_split_short_report_returns_single_part() -> None:
    report = render_report(_digest(), START, END, _counts())
    assert split_report(report) == [report]
    assert len(report) <= TELEGRAM_MESSAGE_LIMIT


def _big_report(min_len: int) -> str:
    digest = DailyDigest(
        highlights=[],
        insights=[
            Insight(
                title=f"Insight number {i} about topic {i}",
                detail="Word " * 30 + str(i),
                source_groups=["SISC <> AI Builders"],
                importance=round(0.9 - i * 0.01, 2),
            )
            for i in range(min_len)
        ],
    )
    return render_report(digest, START, END, _counts())


def test_split_long_report_order_and_labels() -> None:
    report = _big_report(60)
    assert len(report) > TELEGRAM_MESSAGE_LIMIT
    parts = split_report(report)
    assert len(parts) > 1
    for part in parts:
        assert len(part) <= TELEGRAM_MESSAGE_LIMIT
    assert "(Part 1/" in parts[0] and parts[0].rstrip().endswith(
        f"(Part 1/{len(parts)})"
    )
    for i, part in enumerate(parts[1:], 2):
        assert part.startswith(f"(Part {i}/{len(parts)})")


def test_split_no_content_loss_on_concatenation() -> None:
    import re

    for report in (_big_report(60), render_report(_digest(), START, END, _counts())):
        parts = split_report(report)
        joined = "\n".join(
            part.replace(f"(Part {i}/{len(parts)})", "").strip()
            for i, part in enumerate(parts, 1)
        )
        # all content present, in order (labels/separators normalized away)
        assert re.sub(r"\s+", " ", joined).strip() == re.sub(
            r"\s+", " ", report
        ).strip()


def test_split_exact_boundary_respected() -> None:
    text = "x" * (TELEGRAM_MESSAGE_LIMIT - 20) + "\n\n" + "y" * 50
    parts = split_report(text)
    assert len(parts) == 2
    assert all(len(p) <= TELEGRAM_MESSAGE_LIMIT for p in parts)
    assert parts[0].startswith("xxx") and "yyy" in parts[1]


def test_split_oversized_single_insight_hard_cut() -> None:
    huge_line = "z" * (TELEGRAM_MESSAGE_LIMIT * 2 + 100)
    parts = split_report(huge_line)
    assert len(parts) >= 3
    assert all(len(p) <= TELEGRAM_MESSAGE_LIMIT for p in parts)
    assert sum(p.count("…(cont)") for p in parts) == len(parts) - 1
    # content preserved (markers excluded)
    assert "".join(
        p.replace("…(cont)", "").replace("(Part", "").strip()
        for p in parts
    ).startswith("zzz")


def test_split_multiple_blank_line_sections() -> None:
    paras = [f"section {i} — " + "word " * 20 for i in range(40)]
    text = "\n\n".join(paras)
    parts = split_report(text)
    assert len(parts) > 1
    # every paragraph appears intact in exactly one part (never split mid-block)
    all_text = "\n".join(parts)
    for para in paras:
        assert para in all_text


@pytest.mark.parametrize("limit", [500, 1000, TELEGRAM_MESSAGE_LIMIT])
def test_split_respects_custom_limit(limit: int) -> None:
    report = _big_report(40)
    parts = split_report(report, limit=limit)
    assert all(len(p) <= limit for p in parts)
    assert len(parts) > 1
