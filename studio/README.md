# TrackMyRupee Instagram Content Studio

A local, single-user tool for planning, producing and exporting Instagram Reels and carousels for TrackMyRupee.
It is **independent of the production Django app** (own settings, JSON-file storage, binds to 127.0.0.1).

## Quick start
```bash
pip install -r studio/requirements.txt      # Django, Pillow, numpy, requests ; ffmpeg must be installed
python studio/server.py                      # http://127.0.0.1:8765
python studio/build_reel_001.py              # (re)render Reel 001 assets into studio/output/
```
First run seeds `studio/data/` with: 30 Reel ideas, 30 carousel ideas, 7 series, a 30-day calendar (43 slots: 30 Reels + 13 carousels), 50+ hooks, 20+ CTAs, caption/hashtag templates and brand settings. Everything is editable in the UI. Delete `studio/data/` to reset.

## Workflow (one piece at a time)
Ideas → **Generate content** → review/edit (hooks, script, storyboard, caption, CTA) → preview video/cover → **Approve** (records style defaults) → export ZIP → mark Published → enter metrics.
* The library holds **ideas only**. A full package exists only for pieces Claude has authored on request (currently **Reel 001**). "Generate content" on an un-authored idea returns a ready-to-paste request instead of silently producing low-quality filler.
* "Regenerate hooks/caption" draw from your own hook/CTA banks (no hidden model).

## What is real vs. not
| Capability | Status |
|---|---|
| Dashboard, ideas CRUD/filter, calendar, status workflow, asset upload, ZIP export, QC, performance maths | Working, tested |
| Reel 001 motion-graphics video (1080×1920, H.264/AAC) + cover | Rendered by `render/reel_001.py` (Pillow + FFmpeg) |
| Synthesised SFX bed | Generated in code (`render/sfx.py`) |
| Voiceover | Generated offline with Kokoro (`render/tts_kokoro.py`, model not committed: `assets/models/`). Clips in `assets/vo/<en|en_hf|hi>/`; `render/mix.py` places/ducks them. **Not human-grade — review by ear before publishing** |
| Music | Not included — add a licensed track |
| OpenAI / Gemini image + OpenAI TTS adapters (`core/providers.py`) | Written to public API docs, **not live-tested** (no keys) |
| AI video generation | Not integrated; image-to-video prompts are exported for manual use |
| Instagram analytics | **No integration**; you type the numbers in |
| Carousel renderer (`render/carousel.py`, PNG 1080×1350 + PDF) | Implemented; first used when you request a carousel |

## API keys (optional)
Copy `.env.example` → `studio/.env`. `OPENAI_API_KEY`, `GEMINI_API_KEY`. Keys never reach the browser.

## Brand
Tokens come from `templates/landing.html` (`--tmr-*`), logo from `static/img/logo.svg`, fonts Instrument Serif + DM Sans (downloaded to `assets/fonts`, Google Fonts, OFL). Product UI in Reels is only ever the real screenshot `static/img/mobile-2.png`.

## Verification notes
trackmyrupee.com returned 403 to the build sandbox, so product claims were verified against the repo (`FEATURES.md`, `llms.txt`, `guide-src/`, routes) — see `core/library.py → BRAND.verified_claims`. Re-check pricing (₹49/₹499) before quoting.
