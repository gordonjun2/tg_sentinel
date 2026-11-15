# Serendip Welcome Animation (Two-Stage) Design

**Date:** 2026-11-15  
**Status:** Approved (design dialogue)  
**Repo:** `tg_sentinel`  
**Primary path:** `assets/onboarding/serendip-welcome.mp4`  
**Wiring:** `onboarding_media.SERENDIP_WELCOME_ANIM` → `bot.py` post-approval `send_animation`

## Problem

The Serendip onboarding animation for the SISC gate bot is missing or must not reuse the prior Dawn+`welcome-loop.webp` composite. We need a new muted portrait clip with the full Mini App welcome copy baked into the frames, suitable for Telegram `sendAnimation`.

## Goals

- Ship one Telegram-safe muted H.264 MP4 at `assets/onboarding/serendip-welcome.mp4`.
- Keep readable, exact welcome copy inside the video (not caption-only).
- Preserve mascot identity from `serendip-tg` still art.
- Produce **two** I2V drafts and select one winner.
- Leave `sisc-hero.mp4` and bot send timing unchanged.

## Non-goals

- Regenerating the SISC hero clip.
- Changing when Serendip media is sent (post-approval / resend invite only).
- Using `serendip-mascots-welcome-loop.webp` or `welcome-short.webp` as the motion source.
- Pure model-invented typography without a locked poster still.

## Approach

**Two-stage “words locked, motion free” with 2 drafts.**

1. Compose a 9:16 poster still locally (Dawn wash + keyed mascots + locked typography).
2. Animate that still with fal I2V under a text-lock prompt. Run two seeds.
3. Encode the chosen draft to muted Telegram-safe MP4 and point existing media helpers at it.

## Copy (exact)

- **Title:** `The right person, at the right moment.`
- **Body:** `No swiping. You tell us who you are. The system quietly finds the people who resonate, then leaves the rest to you.`

Source of truth: `serendip-tg/frontend/src/features/onboarding/components/welcome-step.tsx`.

## Pipeline

### Stage A — Poster still

- **Input:** `serendip-tg/frontend/src/assets/images/serendip_mascots.png` (black background keyed to alpha).
- **Layout:** Portrait ~9:16 (e.g. 720×1280). Mascots in upper half. Accent rule + title + body below on Dawn paper wash (tokens approx: paper `#F2EFFA`, violet wash, soft gold sheen, ink `#2C2438`).
- **Output:** `assets/onboarding/_source/serendip-poster.png`.

### Stage B — I2V drafts

- **Model (primary):** `alibaba/wan-3.0-prime/image-to-video`  
  Native `aspect_ratio=9:16`, `duration=5`, `audio=false`, resolution `720p` or `1080p`.
- **Fallback:** `minimax/h3-max/image-to-video` if Wan fails schema/runtime.
- **Prompt intent:** Keep all on-screen text perfectly sharp and unchanged. Only gentle mascot motion (idle breathe / soft hug). No camera crash-zoom. No new props or wardrobe.
- **Outputs:**  
  - `assets/onboarding/_source/serendip-wan-a.mp4`  
  - `assets/onboarding/_source/serendip-wan-b.mp4`

### Stage C — Ship

- Mute, `libx264`, `yuv420p`, `+faststart`, scale to Telegram-friendly portrait if needed.
- Write `assets/onboarding/serendip-welcome.mp4`.
- `onboarding_media.py` already resolves this path; no bot logic change.

## Script / tooling changes

| Piece | Change |
|--------|--------|
| `scripts/build_onboarding_gifs.py` | Prefer Serendip ship path from `_source` poster/I2V drafts. Stop defaulting Serendip to `welcome-loop.webp` Dawn composite. Keep SISC path intact. |
| `onboarding_media.py` | No API change (`SERENDIP_WELCOME_ANIM` stays `serendip-welcome.mp4`). |
| `tests/test_onboarding_media.py` | Still asserts both animation files exist and are non-empty. |

Optional: document env vars for regenerating (`SERENDIP_VIDEO_SOURCE`, poster path) in the script docstring only.

## QA checklist

- [ ] Poster spelling matches exact title + full body.
- [ ] Text remains readable at phone width; mascots not clipped.
- [ ] Both drafts: text does not warp/morph across frames.
- [ ] Motion is gentle; no aggressive camera push.
- [ ] Winner is muted; `sendAnimation` content-type path for MP4 still works.
- [ ] `pytest tests/test_onboarding_media.py` passes.

## Decisions locked

| Decision | Choice |
|----------|--------|
| Motion source | Fresh two-stage gen (not old webp loop) |
| Words | Inside video frames via locked poster |
| Still source | `serendip_mascots.png` I2V conditioning via poster |
| Aspect | Portrait 9:16 |
| Copy | Full welcome-step text (option A) |
| Draft count | 2 |
| Primary I2V | Wan 3.0 Prime |
