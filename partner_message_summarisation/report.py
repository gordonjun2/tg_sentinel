"""Report rendering + splitting under Telegram's 4,096-char limit.

Pure functions: deterministic HTML (Telegram ``parse_mode=HTML`` —
``<b>``/``<i>`` only, every tag confined to a single line and all dynamic
text escaped, so group titles like ``SISC <> …`` are safe). ``split_report``
cuts at section boundaries with ordered ``(Part i/N)`` labels; hard cuts
are tag-balanced so a part is never left with an unclosed tag.
"""

from __future__ import annotations

import html
import re

from .summarizer import PartnerMessageSummary, UnansweredPoint
from .timeutil import fmt_sentinel

TELEGRAM_MESSAGE_LIMIT = 4096
_PART_LABEL_RESERVE = len("(Part 99/99)\n\n")
_CONT_SUFFIX = " …(cont)"

_PARTIAL_TAG_RE = re.compile(r"<[^<>]*$")


def _esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def _balance_tags(fragment: str) -> str:
    """Make a hard-cut fragment safe for Telegram HTML: drop a trailing
    partial tag, strip orphan closers, close tags left open."""
    fragment = _PARTIAL_TAG_RE.sub("", fragment)
    for open_tag, close_tag in (("<b>", "</b>"), ("<i>", "</i>")):
        while fragment.count(close_tag) > fragment.count(open_tag):
            fragment = fragment.replace(close_tag, "", 1)
        missing = fragment.count(open_tag) - fragment.count(close_tag)
        if missing > 0:
            fragment += close_tag * missing
    return fragment


def render_report(
    summary: PartnerMessageSummary,
    window_start,
    window_end,
    chat_counts: dict[str, int],
    unanswered: list[UnansweredPoint] | None = None,
) -> str:
    """HTML report: header, per-group summary, unanswered points, coverage."""
    window_str = (
        f"{fmt_sentinel(window_start, '%d %b %H:%M')} → "
        f"{fmt_sentinel(window_end, '%d %b %H:%M')} SGT"
    )
    total_msgs = sum(chat_counts.values())
    lines = [
        f"📜 <b>SISC Partners Message Summary</b>",
        f"Window: {window_str}",
        f"Groups: {len(chat_counts)} · Messages: {total_msgs}",
        "",
        "💬 <b>Summary</b>",
    ]
    if summary.groups:
        for group in summary.groups:
            lines.append("")
            lines.append(f"<b>{_esc(group.group_name)}</b>")
            for point in group.summary:
                lines.append(f"• {_esc(point)}")
    else:
        lines.append("—")
    lines.extend(["", "❓ <b>Awaiting admin reply</b>"])
    if unanswered:
        by_chat: dict[str, list[UnansweredPoint]] = {}
        for item in unanswered:
            by_chat.setdefault(item.group_name, []).append(item)
        for chat_title, items in by_chat.items():
            lines.extend(["", f"<b>{_esc(chat_title)}</b>"])
            lines.extend(f"• {_esc(item.point)}" for item in items)
    else:
        lines.append("None — every partner message has been addressed ✓")
    lines.extend(["", "🧾 <b>Coverage</b>"])
    coverage = " · ".join(
        f"{_esc(title)} — {count} msgs"
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
    ``(Part 1/N)`` when N > 1. Tags are line-local, so newline splits
    never break markup; hard cuts are tag-balanced.
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
                units.append((piece_joiner, _balance_tags(marked)))

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
    # trim only the oversized ones (tags stay balanced).

    def _fit(p: str) -> str:
        return _balance_tags(p[: limit - len("</b></i>")]) if len(p) > limit else p

    return [_fit(p) for p in labeled]
