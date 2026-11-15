"""Paths and helpers for onboarding GIF animations."""

from __future__ import annotations

from pathlib import Path

_ASSETS = Path(__file__).resolve().parent / "assets" / "onboarding"

SISC_HERO_GIF = _ASSETS / "sisc-hero.gif"
SERENDIP_WELCOME_GIF = _ASSETS / "serendip-welcome.gif"


def require_onboarding_gifs() -> None:
    missing = [p for p in (SISC_HERO_GIF, SERENDIP_WELCOME_GIF) if not p.is_file()]
    if missing:
        names = ", ".join(str(p) for p in missing)
        raise FileNotFoundError(
            f"Missing onboarding GIF(s): {names}. "
            "Run: python scripts/build_onboarding_gifs.py"
        )
