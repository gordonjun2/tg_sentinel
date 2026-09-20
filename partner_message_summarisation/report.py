"""Report rendering + splitting under Telegram's 4,096-char limit.

Pure functions: deterministic plain text (no parse mode — immune to
Markdown-breaking group titles, consistent with the gmail_calendar
formatter style), split at section boundaries with ordered
``(Part i/N)`` labels.
"""

from __future__ import annotations

import pytz

from .config import SENTINEL_TIMEZONE
from .summarizer import DailyDigest

TELEGRAM_MESSAGE_LIMIT = 4096
_PART_LABEL_RESERVE = len("(Part 99/99)\n\n")
_CONT_SUFFIX = " …(cont)"


def render_report(
    digest: DailyDigest,
    window_start,
    window_end,
    chat_counts: dict[str, int],
) -> str:
    """Plain-text digest: header, highlights, insights, coverage (§10.1)."""
    tz = pytz.timezone(SENTINEL_TIMEZONE)
    date_str = window_end.astimezone(tz).strftime("%a %d %b %Y")
    window_str = (
        f"{window_start.astimezone(tz).strftime('%d %b %H:%M')} → "
        f"{window_end.astimezone(tz).strftime('%d %b %H:%M')} SGT"
    )
    total_msgs = sum(chat_counts.values())
    lines = [
        f"📜 SISC Daily Digest — {date_str}",
        f"Window: {window_str}",
        f"Groups: {len(chat_counts)} · Messages: {total_msgs}",
        "",
        "⭐ Highlights",
    ]
    if digest.highlights:
        lines.extend(f"• {h}" for h in digest.highlights)
    else:
        lines.append("• —")
    lines.extend(["", "🔍 Insights"])
    if digest.insights:
        for i, insight in enumerate(digest.insights, start=1):
            lines.append(f"{i}. {insight.title}  ({insight.importance:.2f})")
            lines.append(f"   {insight.detail}")
            lines.append(f"   Source: {', '.join(insight.source_groups)}")
    else:
        lines.append("—")
    lines.extend(["", "🧾 Coverage"])
    coverage = " · ".join(
        f"{title} — {count} msgs"
        for title, count in sorted(
            chat_counts.items(), key=lambda kv: kv[1], reverse=True
        )
    )
    lines.append(coverage or "—")
    return "\n".join(lines)


def split_report(
    text: str, limit: int = TELEGRAM_MESSAGE_LIMIT
) -> list[str]:
    """Split into ordered parts ≤ ``limit`` chars (§10.2).

    Split-point priority: blank lines (section boundaries) → single
    newlines → hard cut with a ``…(cont)`` marker. Every part after the
    first is prefixed ``(Part i/N)`` and the first is suffixed
    ``(Part 1/N)`` when N > 1. No characters are dropped or reordered —
    the original text equals the units concatenated with their joiners.
    """
    if len(text) <= limit:
        return [text]

    effective = limit - _PART_LABEL_RESERVE

    # Atomic units as (joiner, text): the original text is exactly
    # "".join(joiner + unit) over all units.
    units: list[tuple[str, str]] = []
    for idx, para in enumerate(text.split("\n\n")):
        para_joiner = "\n\n" if idx else ""
        if len(para) <= effective:
            units.append((para_joiner, para))
            continue
        for j, line in enumerate(para.split("\n")):
            line_joiner = "\n" if j else para_joiner
            if len(line) <= effective:
                units.append((line_joiner, line))
                continue
            step = effective - len(_CONT_SUFFIX)
            pieces = [line[i : i + step] for i in range(0, len(line), step)]
            for k, piece in enumerate(pieces):
                piece_joiner = line_joiner if k == 0 else ""
                marked = piece + _CONT_SUFFIX if k < len(pieces) - 1 else piece
                units.append((piece_joiner, marked))

    parts: list[str] = []
    current = ""
    for joiner, unit in units:
        if current and len(current) + len(joiner) + len(unit) > effective:
            parts.append(current)
            current = joiner + unit
        else:
            current += joiner + unit
    if current:
        parts.append(current)

    n = len(parts)
    if n == 1:
        return parts
    labeled = [parts[0] + f"\n\n(Part 1/{n})"]
    labeled.extend(f"(Part {i}/{n})\n\n{part}" for i, part in enumerate(parts[1:], 2))
    # Defensive: a label can push a boundary-hugging part past the limit;
    # trim rather than let Telegram reject the send.
    return [p[:limit] for p in labeled]
