#!/usr/bin/env python3
"""Build onboarding GIFs for the SISC gate bot.

Outputs (gitignored or committed under assets/onboarding/):
  - sisc-hero.gif: title + tagline only
  - serendip-welcome.gif: welcome-short mascot frames + onboarding title/body below
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "onboarding"
WEBP_SRC = Path(
    "/Users/gordonjun/Desktop/Projects/serendip-tg/frontend/src/assets/videos/"
    "serendip-mascots-welcome-short.webp"
)

SISC_TITLE = "The Super-Individual\nSecret Club"
SISC_TAGLINE = "The 1st Human Alliance for the Agentic Era"

SERENDIP_TITLE = "The right person, at the right moment."
SERENDIP_BODY = (
    "No swiping. You tell us who you are. The system quietly finds "
    "the people who resonate, then leaves the rest to you."
)

PAPER = (250, 247, 242, 255)
INK = (28, 25, 23, 255)
INK_SOFT = (87, 83, 78, 255)
ACCENT = (124, 58, 237, 255)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def build_sisc_hero(path: Path) -> None:
    width, height = 720, 420
    frames: list[Image.Image] = []
    title_font = _font(42, bold=True)
    tag_font = _font(22, bold=False)
    for i in range(24):
        img = Image.new("RGBA", (width, height), PAPER)
        draw = ImageDraw.Draw(img)
        # Soft fade-in of title over first frames
        alpha = min(255, int(40 + i * 12))
        title_fill = (*INK[:3], alpha)
        tag_fill = (*INK_SOFT[:3], min(255, alpha + 20))
        y = 110
        for line in SISC_TITLE.split("\n"):
            tw = draw.textlength(line, font=title_font)
            draw.text(((width - tw) / 2, y), line, font=title_font, fill=title_fill)
            y += 52
        # Accent rule
        pulse = 0.55 + 0.45 * abs(math.sin(i / 24 * math.pi * 2))
        rule_w = int(160 * pulse)
        draw.rounded_rectangle(
            [(width - rule_w) / 2, y + 8, (width + rule_w) / 2, y + 14],
            radius=3,
            fill=(*ACCENT[:3], alpha),
        )
        tag_lines = _wrap(draw, SISC_TAGLINE, tag_font, width - 80)
        y = y + 36
        for line in tag_lines:
            tw = draw.textlength(line, font=tag_font)
            draw.text(((width - tw) / 2, y), line, font=tag_font, fill=tag_fill)
            y += 30
        frames.append(img.convert("P", palette=Image.ADAPTIVE, colors=128))
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=80,
        loop=0,
        optimize=True,
    )


def build_serendip_welcome(path: Path, webp_path: Path) -> None:
    src = Image.open(webp_path)
    canvas_w = 540
    mascot_w = canvas_w
    text_pad = 28
    title_font = _font(26, bold=True)
    body_font = _font(18, bold=False)

    # Measure text block height once
    probe = Image.new("RGBA", (canvas_w, 200), PAPER)
    probe_draw = ImageDraw.Draw(probe)
    title_lines = _wrap(probe_draw, SERENDIP_TITLE, title_font, canvas_w - text_pad * 2)
    body_lines = _wrap(probe_draw, SERENDIP_BODY, body_font, canvas_w - text_pad * 2)
    text_h = text_pad + len(title_lines) * 32 + 12 + len(body_lines) * 26 + text_pad

    out_frames: list[Image.Image] = []
    # Subsample to keep Telegram GIF size reasonable (~30 frames from 90)
    step = 3
    durations: list[int] = []
    for idx, frame in enumerate(ImageSequence.Iterator(src)):
        if idx % step != 0:
            continue
        fr = frame.convert("RGBA")
        scale = mascot_w / fr.width
        mascot_h = int(fr.height * scale)
        fr = fr.resize((mascot_w, mascot_h), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (canvas_w, mascot_h + text_h), PAPER)
        # Center mascot horizontally if needed
        canvas.paste(fr, (0, 0), fr)
        draw = ImageDraw.Draw(canvas)
        y = mascot_h + text_pad
        for line in title_lines:
            tw = draw.textlength(line, font=title_font)
            draw.text(((canvas_w - tw) / 2, y), line, font=title_font, fill=INK)
            y += 32
        y += 8
        for line in body_lines:
            tw = draw.textlength(line, font=body_font)
            draw.text(((canvas_w - tw) / 2, y), line, font=body_font, fill=INK_SOFT)
            y += 26
        out_frames.append(canvas.convert("P", palette=Image.ADAPTIVE, colors=192))
        durations.append(int(src.info.get("duration") or 60) * step)

    if not out_frames:
        raise RuntimeError(f"No frames read from {webp_path}")

    out_frames[0].save(
        path,
        save_all=True,
        append_images=out_frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sisc_path = OUT_DIR / "sisc-hero.gif"
    serendip_path = OUT_DIR / "serendip-welcome.gif"
    build_sisc_hero(sisc_path)
    if not WEBP_SRC.is_file():
        raise FileNotFoundError(f"Missing Serendip source WebP: {WEBP_SRC}")
    build_serendip_welcome(serendip_path, WEBP_SRC)
    print(f"Wrote {sisc_path} ({sisc_path.stat().st_size} bytes)")
    print(f"Wrote {serendip_path} ({serendip_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
