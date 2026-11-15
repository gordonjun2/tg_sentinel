#!/usr/bin/env python3
"""Build onboarding animations for the SISC gate bot.

Outputs under assets/onboarding/:
  - sisc-hero.mp4: MiniMax (fal) title card, muted H.264
  - serendip-welcome.mp4: two-stage poster + fal I2V (words locked in frames)

Serendip default source is `_source/serendip-wan.mp4` (already includes title
and body). Mute-encode that clip. Do not fall back to the serendip-tg
welcome-loop.webp unless SERENDIP_LOOP_WEBP is set explicitly.

  SISC_HERO_SOURCE=_source/sisc-hero-fal.mp4
  SERENDIP_VIDEO_SOURCE=_source/serendip-wan.mp4
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "onboarding"
DEFAULT_SISC_SOURCE = OUT_DIR / "_source" / "sisc-hero-fal.mp4"
DEFAULT_SERENDIP_SOURCE = OUT_DIR / "_source" / "serendip-wan.mp4"
DEFAULT_SERENDIP_TEXT_SOURCE = OUT_DIR / "_source" / "serendip-text-fal.mp4"

SERENDIP_TITLE = "The right person, at the right moment."
SERENDIP_BODY = (
    "No swiping. You tell us who you are. The system quietly finds the people who resonate, "
    "then leaves the rest to you."
)
SERENDIP_BODY_A = "No swiping. You tell us who you are."
SERENDIP_BODY_B = "The system quietly finds the people who resonate, then leaves the rest to you."

# Dawn theme (approx from frontend :root tokens)
PAPER = (242, 239, 250)
WASH_V = (232, 224, 245)
WASH_G = (245, 235, 208)
SHEEN = (255, 243, 209)
INK = (44, 36, 56)
INK_SOFT = (92, 85, 104)
ACCENT = (124, 58, 237)


def _require_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required to build onboarding mp4 animations")
    return ffmpeg


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_dir = OUT_DIR / "_fonts"
    brand = [
        font_dir / ("Fraunces-DisplaySoft.ttf" if bold else "PlusJakartaSans-Medium.ttf"),
        font_dir / ("Fraunces-Bold.ttf" if bold else "PlusJakartaSans-Regular.ttf"),
    ]
    system = [
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Avenir Next.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in [*brand, *system]:
        try:
            return ImageFont.truetype(str(path), size=size)
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


def encode_muted_h264(
    source: Path,
    dest: Path,
    scale_filter: str,
    crf: str,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _require_ffmpeg()
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-crf",
        crf,
        "-preset",
        "medium",
        "-vf",
        scale_filter,
        str(dest),
    ]
    subprocess.run(cmd, check=True)


def build_sisc_hero_mp4(source: Path, dest: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(
            f"Missing SISC hero source video: {source}. "
            "Generate with fal MiniMax H3 Max text-to-video, save as that path, "
            "or set SISC_HERO_SOURCE."
        )
    encode_muted_h264(source, dest, "scale=768:768:flags=lanczos", "23")


def build_serendip_locked_mp4(source: Path, dest: Path) -> None:
    """Mute-encode a full I2V clip that already has title/body in-frame."""
    if not source.is_file():
        raise FileNotFoundError(
            f"Missing Serendip locked-text source: {source}. "
            "Compose poster + fal I2V, save as that path, or set SERENDIP_VIDEO_SOURCE."
        )
    # Keep source aspect (poster I2V may already be cropped). Even dims for H.264.
    encode_muted_h264(
        source,
        dest,
        "scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=lanczos",
        "20",
    )


def _build_serendip_text_panel(width: int) -> Image.Image:
    pad_x = 40
    pad_top = 28
    pad_bottom = 36
    title_font = _font(28, bold=True)
    body_font = _font(18, bold=False)
    probe = Image.new("RGB", (width, 200), PAPER)
    draw = ImageDraw.Draw(probe)
    max_w = width - pad_x * 2
    title_lines = _wrap(draw, SERENDIP_TITLE, title_font, max_w)
    body_lines = (
        _wrap(draw, SERENDIP_BODY_A, body_font, max_w)
        + _wrap(draw, SERENDIP_BODY_B, body_font, max_w)
    )
    text_h = pad_top + 8 + len(title_lines) * 34 + 16 + len(body_lines) * 26 + pad_bottom
    if text_h % 2:
        text_h += 1
    panel = Image.new("RGB", (width, text_h), PAPER)
    draw = ImageDraw.Draw(panel)
    draw.rounded_rectangle(
        [(width // 2 - 28), 10, (width // 2 + 28), 16],
        radius=3,
        fill=ACCENT,
    )
    y = pad_top + 8
    for line in title_lines:
        tw = draw.textlength(line, font=title_font)
        draw.text(((width - tw) / 2, y), line, font=title_font, fill=INK)
        y += 34
    y += 10
    for line in body_lines:
        tw = draw.textlength(line, font=body_font)
        draw.text(((width - tw) / 2, y), line, font=body_font, fill=INK_SOFT)
        y += 26
    return panel


def build_serendip_welcome_mp4(
    source: Path,
    dest: Path,
    text_source: Path | None,
) -> None:
    if not source.is_file():
        raise FileNotFoundError(
            f"Missing Serendip motion source: {source}. "
            "Animate mascot still with fal Wan/MiniMax I2V, save as that path, "
            "or set SERENDIP_VIDEO_SOURCE."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _require_ffmpeg()
    width = 720
    video_h = 720
    text_h = 406
    tmp_dir = OUT_DIR / "_tmp_build"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    scaled = tmp_dir / "serendip-scaled.mp4"
    text_scaled = tmp_dir / "serendip-text-scaled.mp4"
    panel_path = tmp_dir / "serendip-text-panel.png"
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source),
                "-an",
                "-vf",
                f"scale={width}:{video_h}:flags=lanczos",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "20",
                "-preset",
                "medium",
                "-r",
                "24",
                str(scaled),
            ],
            check=True,
        )
        if text_source is not None and text_source.is_file():
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(text_source),
                    "-an",
                    "-vf",
                    f"scale={width}:{text_h}:flags=lanczos",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-crf",
                    "20",
                    "-preset",
                    "medium",
                    "-r",
                    "24",
                    str(text_scaled),
                ],
                check=True,
            )
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(scaled),
                    "-i",
                    str(text_scaled),
                    "-filter_complex",
                    "[0:v][1:v]vstack=inputs=2[v]",
                    "-map",
                    "[v]",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    "-crf",
                    "20",
                    "-preset",
                    "medium",
                    "-r",
                    "24",
                    "-t",
                    "5",
                    str(dest),
                ],
                check=True,
            )
            return

        _build_serendip_text_panel(width).save(panel_path)
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(scaled),
                "-loop",
                "1",
                "-t",
                "5",
                "-framerate",
                "24",
                "-i",
                str(panel_path),
                "-filter_complex",
                "[0:v][1:v]vstack=inputs=2[v]",
                "-map",
                "[v]",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-crf",
                "20",
                "-preset",
                "medium",
                "-r",
                "24",
                str(dest),
            ],
            check=True,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _dawn_bg(width: int, height: int) -> Image.Image:
    base = Image.new("RGB", (width, height), PAPER)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    def blob(size: tuple[int, int], color: tuple[int, int, int], soft: int) -> Image.Image:
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([0, 0, size[0] - 1, size[1] - 1], fill=(*color, 255))
        return img.filter(ImageFilter.GaussianBlur(soft))

    violet = blob((980, 760), WASH_V, 140)
    gold = blob((860, 640), WASH_G, 150)
    sheen = blob((720, 420), SHEEN, 110)
    overlay.paste(violet, (-160, -120), violet)
    overlay.paste(gold, (width - 680, 180), gold)
    overlay.paste(sheen, (40, -40), sheen)
    return Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGBA")


def _clean_fringe(mascot: Image.Image) -> Image.Image:
    mascot = mascot.convert("RGBA")
    pixels = mascot.load()
    width, height = mascot.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a < 18:
                pixels[x, y] = (r, g, b, 0)
                continue
            if r > 235 and g > 235 and b > 235 and a < 220:
                pixels[x, y] = (r, g, b, 0)
            elif a < 80 and (r + g + b) / 3 > 200:
                pixels[x, y] = (r, g, b, 0)
    alpha = mascot.split()[-1]
    alpha = alpha.filter(ImageFilter.MinFilter(3))
    alpha = alpha.filter(ImageFilter.GaussianBlur(0.4))
    mascot.putalpha(alpha)
    return mascot


def build_serendip_dawn_from_loop(loop_path: Path, dest: Path) -> None:
    if not loop_path.is_file():
        raise FileNotFoundError(f"Missing Serendip loop WebP: {loop_path}")
    ffmpeg = _require_ffmpeg()
    width, height = 720, 960
    fps = 20
    need = 100
    raw_frames = [frame.convert("RGBA") for frame in ImageSequence.Iterator(Image.open(loop_path))]
    picked: list[Image.Image] = []
    while len(picked) < need:
        picked.extend(raw_frames)
    picked = picked[:need]

    bg = _dawn_bg(width, height)
    probe = _clean_fringe(picked[0])
    max_mw = int(width * 0.78)
    scale = max_mw / probe.width
    mw = max_mw
    mh = int(probe.height * scale)
    mx = (width - mw) // 2
    my = 48
    text_top = my + mh + 16
    title_font = _font(30, bold=True)
    body_font = _font(18, bold=False)

    tmp_dir = OUT_DIR / "_tmp_build"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    try:
        for index, raw in enumerate(picked):
            canvas = bg.copy()
            mascot = _clean_fringe(raw).resize((mw, mh), Image.Resampling.LANCZOS)
            canvas.paste(mascot, (mx, my), mascot)
            draw = ImageDraw.Draw(canvas)
            draw.rounded_rectangle(
                [width // 2 - 28, text_top, width // 2 + 28, text_top + 5],
                radius=3,
                fill=(*ACCENT, 255),
            )
            y = text_top + 24
            for line in _wrap(draw, SERENDIP_TITLE, title_font, width - 64):
                tw = draw.textlength(line, font=title_font)
                draw.text(((width - tw) / 2, y), line, font=title_font, fill=(*INK, 255))
                y += 38
            y += 10
            for line in (SERENDIP_BODY_A, SERENDIP_BODY_B):
                for wrapped in _wrap(draw, line, body_font, width - 64):
                    tw = draw.textlength(wrapped, font=body_font)
                    draw.text(
                        ((width - tw) / 2, y),
                        wrapped,
                        font=body_font,
                        fill=(*INK_SOFT, 255),
                    )
                    y += 26
                y += 4
            canvas.convert("RGB").save(tmp_dir / f"f{index:03d}.png")

        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-framerate",
                str(fps),
                "-i",
                str(tmp_dir / "f%03d.png"),
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-crf",
                "18",
                "-preset",
                "medium",
                str(dest),
            ],
            check=True,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sisc_source = Path(os.environ.get("SISC_HERO_SOURCE", str(DEFAULT_SISC_SOURCE)))
    serendip_source = Path(
        os.environ.get("SERENDIP_VIDEO_SOURCE", str(DEFAULT_SERENDIP_SOURCE))
    )
    serendip_text_source = Path(
        os.environ.get("SERENDIP_TEXT_SOURCE", str(DEFAULT_SERENDIP_TEXT_SOURCE))
    )
    serendip_loop_env = os.environ.get("SERENDIP_LOOP_WEBP")
    serendip_loop = Path(serendip_loop_env) if serendip_loop_env else None
    sisc_path = OUT_DIR / "sisc-hero.mp4"
    serendip_path = OUT_DIR / "serendip-welcome.mp4"

    if sisc_source.is_file():
        build_sisc_hero_mp4(sisc_source, sisc_path)
        print(f"Wrote {sisc_path} ({sisc_path.stat().st_size} bytes)")
    elif sisc_path.is_file():
        print(f"Keeping existing {sisc_path} (no source at {sisc_source})")
    else:
        raise FileNotFoundError(
            f"Missing {sisc_path} and source {sisc_source}. "
            "Generate MiniMax H3 Max video via fal, save source, re-run."
        )

    if serendip_source.is_file():
        # Locked-text I2V already contains title/body; mute-encode only.
        # Optional SERENDIP_TEXT_SOURCE still stacks via the legacy path when set.
        if serendip_text_source.is_file():
            build_serendip_welcome_mp4(
                serendip_source, serendip_path, serendip_text_source
            )
        else:
            build_serendip_locked_mp4(serendip_source, serendip_path)
        print(f"Wrote {serendip_path} ({serendip_path.stat().st_size} bytes)")
    elif serendip_loop is not None and serendip_loop.is_file():
        build_serendip_dawn_from_loop(serendip_loop, serendip_path)
        print(f"Wrote {serendip_path} ({serendip_path.stat().st_size} bytes)")
    elif serendip_path.is_file():
        print(
            f"Keeping existing {serendip_path} "
            f"(no source at {serendip_source}; set SERENDIP_LOOP_WEBP to rebuild from loop)"
        )
    else:
        raise FileNotFoundError(
            f"Missing {serendip_path} and source {serendip_source}. "
            "Save fal I2V to that path, or set SERENDIP_LOOP_WEBP for the legacy Dawn path."
        )


if __name__ == "__main__":
    main()
