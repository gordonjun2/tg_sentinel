# Serendip Welcome Animation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a new muted portrait `assets/onboarding/serendip-welcome.mp4` via two-stage poster + fal I2V (2 drafts), without reusing the old webp loop.

**Architecture:** Compose a 9:16 Dawn poster still with keyed mascots and locked welcome copy, animate it twice with Wan 3.0 Prime I2V under a text-lock prompt, mute-encode the winner to the path `onboarding_media.SERENDIP_WELCOME_ANIM` already uses.

**Tech Stack:** Pillow, ffmpeg, fal MCP (`alibaba/wan-3.0-prime/image-to-video`), existing `scripts/build_onboarding_gifs.py` / `onboarding_media.py`.

## Global Constraints

- Exact copy from welcome-step (no em dashes, no semicolons, no contractions beyond existing source strings).
- Portrait 9:16; muted H.264 `yuv420p` `+faststart`.
- Do not regenerate `sisc-hero.mp4`.
- Do not default Serendip to `serendip-mascots-welcome-loop.webp`.
- Two I2V drafts before shipping one winner.
- Primary model: `alibaba/wan-3.0-prime/image-to-video`; fallback `minimax/h3-max/image-to-video`.

## File map

| File | Responsibility |
|------|----------------|
| `assets/onboarding/_source/serendip-poster.png` | Locked poster still |
| `assets/onboarding/_source/serendip-wan-a.mp4` / `…-b.mp4` | Two fal drafts |
| `assets/onboarding/serendip-welcome.mp4` | Shipped Telegram animation |
| `scripts/build_onboarding_gifs.py` | Prefer `_source` Serendip path; drop loop default for Serendip |
| `onboarding_media.py` | Unchanged API |
| `tests/test_onboarding_media.py` | Assert shipped file exists |

---

### Task 1: Compose poster still

**Files:**
- Create: `assets/onboarding/_source/serendip-poster.png`
- Modify (optional helper): `scripts/build_onboarding_gifs.py` (poster builder reuse)

**Interfaces:**
- Consumes: `serendip-tg/frontend/src/assets/images/serendip_mascots.png`
- Produces: 720×1280 RGB PNG with exact title + full body

- [ ] **Step 1:** Key black bg from mascot PNG → alpha; composite on Dawn wash; draw accent bar + title + body.
- [ ] **Step 2:** Save `_source/serendip-poster.png`; visually check spelling and layout.

---

### Task 2: Generate two I2V drafts

**Files:**
- Create: `assets/onboarding/_source/serendip-wan-a.mp4`
- Create: `assets/onboarding/_source/serendip-wan-b.mp4`

**Interfaces:**
- Consumes: poster PNG via fal `upload_file`
- Produces: two 5s 9:16 muted-capable clips

- [ ] **Step 1:** Upload poster; submit Wan I2V twice with different seeds, `audio=false`, `aspect_ratio=9:16`, `duration=5`, text-lock prompt.
- [ ] **Step 2:** Download both drafts; inspect for text morph / mascot identity.
- [ ] **Step 3:** Pick winner (ask user if ambiguous).

---

### Task 3: Ship + script defaults

**Files:**
- Create: `assets/onboarding/serendip-welcome.mp4`
- Modify: `scripts/build_onboarding_gifs.py`
- Test: `tests/test_onboarding_media.py`

- [ ] **Step 1:** ffmpeg mute-encode winner → `serendip-welcome.mp4`.
- [ ] **Step 2:** Update build script so Serendip prefers `_source` video / poster path, not welcome-loop.
- [ ] **Step 3:** Run `./venv/bin/pytest tests/test_onboarding_media.py -v` — expect PASS.

---

### Task 4: Commit (only if user asks)

- [ ] Stage media + script + docs; commit with message focused on why (new locked-text Serendip welcome animation for Telegram).
