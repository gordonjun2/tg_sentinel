"""Tests for render_report and split_report."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from partner_message_summarisation.report import (
    TELEGRAM_MESSAGE_LIMIT,
    render_report,
    split_report,
)
from partner_message_summarisation.summarizer import (
    PartnerMessageSummary,
    GroupSummary,
    UnansweredPoint,
)

END = datetime(2026, 9, 18, 11, 0, tzinfo=timezone.utc)  # 19:00 SGT
START = END - timedelta(days=1)


def _summary() -> PartnerMessageSummary:
    return PartnerMessageSummary(
        groups=[
            GroupSummary(
                group_name="SISC <> AI Builders",
                summary=["Alice demoed the SISC-built vacuum; ships in October."],
            ),
            GroupSummary(
                group_name="SISC <> Founders",
                summary=["Gordon Oh confirmed the retreat dates; 40 going."],
            ),
        ],
    )


def _counts() -> dict[str, int]:
    return {"SISC <> AI Builders": 96, "SISC <> Founders": 58}


def _unanswered() -> list[UnansweredPoint]:
    return [
        UnansweredPoint(
            group_name="SISC <> AI Builders",
            point="Alice wants to clarify what the panel format is",
        ),
        UnansweredPoint(
            group_name="SISC <> Founders",
            point="Bob is still deciding whether he can join the retreat",
        ),
    ]


# -- render_report ----------------------------------------------------------------


def test_render_report_header_title_and_window() -> None:
    report = render_report(_summary(), START, END, _counts())
    lines = report.split("\n")
    assert lines[0] == "📜 <b>SISC Partners Message Summary</b>"
    assert lines[1] == "Window: 17 Sep 19:00 → 18 Sep 19:00 SGT"
    assert lines[2] == "Groups: 2 · Messages: 154"
    # date must not appear in the title (window only)
    assert "Fri 18 Sep 2026" not in lines[0]


def test_render_report_summary_per_group_with_speakers() -> None:
    report = render_report(_summary(), START, END, _counts())
    assert "💬 <b>Summary</b>" in report
    # group titles bolded and angle brackets escaped
    assert "<b>SISC &lt;&gt; AI Builders</b>" in report
    assert "<b>SISC &lt;&gt; Founders</b>" in report
    assert "Alice demoed" in report
    assert "Gordon Oh confirmed" in report


def test_render_report_unanswered_section() -> None:
    report = render_report(_summary(), START, END, _counts(), _unanswered())
    assert "❓ <b>Awaiting admin reply</b>" in report
    assert "<b>SISC &lt;&gt; AI Builders</b>" in report
    assert "• Alice wants to clarify what the panel format is" in report
    assert "• Bob is still deciding whether he can join the retreat" in report
    # points only — no timestamps, no raw quotes
    assert " at " not in report.split("Awaiting admin reply")[1].split("Coverage")[0]
    assert "Any update" not in report


def test_render_report_no_unanswered() -> None:
    report = render_report(_summary(), START, END, _counts(), [])
    assert "None — every partner message has been addressed ✓" in report


def test_render_report_coverage_last_and_sorted() -> None:
    report = render_report(_summary(), START, END, _counts(), _unanswered())
    assert "🧾 <b>Coverage</b>" in report
    assert (
        report.index("SISC &lt;&gt; AI Builders — 96 msgs")
        < report.index("SISC &lt;&gt; Founders — 58 msgs")
    )
    # coverage is the final section
    assert report.rstrip().endswith("SISC &lt;&gt; Founders — 58 msgs")


def test_render_report_escapes_html_in_content() -> None:
    summary = PartnerMessageSummary(
        groups=[GroupSummary(group_name="G", summary=["asked <about> <b>tags</b>"])]
    )
    report = render_report(summary, START, END, {"G": 1}, [])
    assert "&lt;about&gt; &lt;b&gt;tags&lt;/b&gt;" in report


def test_render_report_empty_summary() -> None:
    report = render_report(PartnerMessageSummary(), START, END, {}, [])
    assert "—" in report


# -- split_report -------------------------------------------------------------------


def test_split_short_report_returns_single_part() -> None:
    report = render_report(_summary(), START, END, _counts())
    assert split_report(report) == [report]
    assert len(report) <= TELEGRAM_MESSAGE_LIMIT


def _big_report(min_len: int) -> str:
    summary = PartnerMessageSummary(
        groups=[
            GroupSummary(
                group_name=f"SISC <> Group {i}",
                summary=["Word " * 30 + str(i)],
            )
            for i in range(min_len)
        ],
    )
    return render_report(summary, START, END, _counts())


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
    for report in (_big_report(60), render_report(_summary(), START, END, _counts())):
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


def test_split_oversized_single_line_hard_cut() -> None:
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


def test_split_hard_cut_keeps_tags_balanced() -> None:
    huge_line = "<b>bold start</b> " + "z" * (TELEGRAM_MESSAGE_LIMIT * 2 + 100)
    parts = split_report(huge_line)
    for part in parts:
        assert part.count("<b>") == part.count("</b>")
        assert part.count("<i>") == part.count("</i>")
        # no dangling partial tag fragment
        assert not re.search(r"<[^<>]*$", part)


def test_split_trailing_trim_keeps_tags_balanced() -> None:
    # a hard-cut open bold line sized so the (Part 1/N) label forces the
    # defensive trim without room to keep the closing tag
    line = "<b>" + "z" * (TELEGRAM_MESSAGE_LIMIT + 10) + "</b>"
    parts = split_report(line)
    assert len(parts) > 1
    for part in parts:
        assert len(part) <= TELEGRAM_MESSAGE_LIMIT
        assert part.count("<b>") == part.count("</b>")
        assert not re.search(r"<[^<>]*$", part)


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
