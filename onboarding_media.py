"""Paths and helpers for onboarding animations (GIF / muted MP4)."""

from __future__ import annotations

from pathlib import Path

_ASSETS = Path(__file__).resolve().parent / "assets" / "onboarding"

# Muted H.264 clips for Telegram sendAnimation
SISC_HERO_ANIM = _ASSETS / "sisc-hero.mp4"
SERENDIP_WELCOME_ANIM = _ASSETS / "serendip-welcome.mp4"


def require_onboarding_gifs() -> None:
    missing = [p for p in (SISC_HERO_ANIM, SERENDIP_WELCOME_ANIM) if not p.is_file()]
    if missing:
        names = ", ".join(str(p) for p in missing)
        raise FileNotFoundError(
            f"Missing onboarding animation(s): {names}. "
            "Run: python scripts/build_onboarding_gifs.py"
        )
