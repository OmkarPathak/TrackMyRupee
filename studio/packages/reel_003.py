"""Reel 003 — The ₹99 subscription trap (English). Library idea reel-003.
Short cut (~35s) built around the 5s average-watch learning: number-led hook, answer within 4s."""

SLUG = "trackmyrupee_reel_003_99_subscription_trap"

_VO = [
    "Five plans. ₹99 each. So what's that a year?",
    "₹495 a month. ₹5,940 a year. All quietly renewing.",
    "And one of them? You've probably forgotten it's still running.",
    "Catch them in three steps. Scan last month's statement. List every recurring charge. Then note each renewal date.",
    "In TrackMyRupee, add each one once. You see the monthly and yearly total, and get a reminder three days before it renews.",
    "No bank login. No SMS access. Try the free demo — link in bio.",
]
_COMMON = {"camera": "Locked-off. No screen-shake.", "background": "Warm off-white paper (#FAF8F6), flat.", "lighting": "Flat graphic; soft card shadows only.", "transition": "Crisp fade-through"}

PACKAGE = {
    "id": "reel-003", "version": 1, "status": "review", "slug": SLUG,
    "title": "The ₹99 subscription trap",
    "strategy": {
        "pillar": "A — Relatable money problems (with a Pillar C product close)",
        "audience": "Salaried professionals 22–35",
        "objective": "Shares/saves via a shocking-but-simple number; then product proof (recurring + reminders). Funnel: relatable → tip → free demo. Pairs with Friday's Hinglish carousel '10 expenses you might be forgetting'.",
        "duration": "≈36s — deliberately shorter than Reels 1–2 (5s average watch time)",
        "format": "Calm-paper explainer, mascot narrating (Line mouth, audio-driven), real app captures (demo account), illustrative maths labelled",
        "feature": "Subscriptions page (monthly/daily/yearly totals, Renewing Soon tab) · reminder three days before renewal — verified (real capture + FEATURES.md)",
        "conversion": "Free demo (link in bio, no registration)",
    },
    "hooks": {"selected": 0, "options": [
        "Five plans. ₹99 each. So what's that a year?",
        "You pay ₹99 a month for something you forgot. Ever added it up?",
        "₹99 is nothing. ₹5,940 is something. Same money.",
        "How many ₹99 plans are still billing you?",
        "The ₹99 trap: it's never one plan.",
    ], "on_screen_used": "₹99 × 5 = ? (giant, on frame 0)",
        "note": "Number-led hook: the maths is the visual; payoff ('₹5,940 a year') lands inside 5 seconds."},
    "illustrative_maths": {"label": "Illustrative example", "plans": 5, "price": 99, "monthly": 495, "yearly": 5940},
    "scenes": [
        {**_COMMON, "n": 1, "t": "0–4", "name": "Hook (number-led)",
         "visual": "Giant '₹99' on frame 0, then '× 5' pops (coral), then '= ?' lands with the voice. Mascot narrates, wide-eyed on '?'.",
         "movement": "Pop-in 0.3s quart ease-out; '?' pulses.", "on_screen": "₹99 × 5 = ?", "vo_en": _VO[0], "sfx": "Ding, pop per symbol, thud on '?'"},
        {**_COMMON, "n": 2, "t": "4–10", "name": "The reveal",
         "visual": "'₹495 a month.' then coral '₹5,940 a year.' A 4×3 grid of month tiles (each ₹495) fills in turn while the total counts up to ₹5,940. 'ILLUSTRATIVE EXAMPLE' pill.",
         "movement": "Tiles fill at 110ms stagger; number counts with ease-in-out.", "on_screen": "₹495 a month · ₹5,940 a year · 12 × ₹495", "vo_en": _VO[1], "sfx": "Tick per tile, thud on the total"},
        {**_COMMON, "n": 3, "t": "10–15", "name": "One you forgot",
         "visual": "'One of them / you forgot.' Five generic plan cards (Music · Video · Cloud storage · Learning app · News, ₹99 each); one card turns coral with a 'still renewing' tag.",
         "movement": "Cards rise 0.35s; the highlighted card pulses softly.", "on_screen": "One of them you forgot. · Cloud storage ₹99 · still renewing", "vo_en": _VO[2], "sfx": "Soft pops; low note on the highlight"},
        {**_COMMON, "n": 4, "t": "15–22", "name": "Catch them in 3 steps",
         "visual": "'Catch them in 3 steps.' Numbered steps appear with the voice: ① Scan last month's statement ② List every recurring charge ③ Note each renewal date.",
         "movement": "Steps rise 0.4s ease-out, staggered to the voice.", "on_screen": "1 Scan last month's statement · 2 List every recurring charge · 3 Note each renewal date", "vo_en": _VO[3], "sfx": "Tick per step"},
        {**_COMMON, "n": 5, "t": "22–31", "name": "TrackMyRupee (real app)",
         "visual": "'Add each one once.' REAL Subscriptions page capture (demo account): totals line '₹1,179 monthly · ₹39 daily · ₹14,148 yearly', Renewing Soon tab, entries with renewal countdown. Then 'Know before it renews.' with chips 'Reminder 3 days before' and 'Renewing Soon list'. Labelled DEMO ACCOUNT · REAL APP.",
         "movement": "Capture slides up 0.4s; sequential cross-fade (no overlap).", "on_screen": "Add each one once. · Monthly + yearly total · Know before it renews.", "vo_en": _VO[4], "sfx": "Whoosh, pops, chime"},
        {**_COMMON, "n": 6, "t": "31–36", "name": "CTA",
         "visual": "Mascot centre (happy), logo + wordmark, coral 'See every renewal in one place.', teal 'Try the free demo' button, trackmyrupee.com/demo, 'No bank login · No SMS access'.",
         "movement": "Staggered fade-up.", "on_screen": "See every renewal in one place. · Try the free demo · trackmyrupee.com/demo", "vo_en": _VO[5], "sfx": "Whoosh + chime"},
    ],
    "voiceover": {"direction": "Quick, wry, conversational. Land '₹5,940 a year' with a beat of silence before it. Initialisms and rupee amounts are expanded for the TTS (render/tts_kokoro.py).",
                  "en": "\n\n".join(_VO), "hinglish": "(English for the Reel; Hinglish lives in the carousel.)", "hindi": "(not requested)",
                  "tts_status": "Kokoro af_heart (draft quality)."},
    "mascot": {"role": "Narrator top-right (~190px), approved 'Line' mouth, audio-driven; wide-eyed on '?' and the forgotten plan; happy on the CTA."},
    "music": {"direction": "Calm, minimal, warm; very low under the voice. Add licensed audio in Instagram.", "structure": "Pad → light pulse on the reveal → resolve on the CTA chime.", "licensing": "Licensed/royalty-free or Instagram business library only."},
    "editing": {"tool": "Ready-made export", "canvas": "9:16, 1080×1920, 60 fps", "timeline": ["Video baked with motion; narration per scene; captions burned in (clean export also provided)."],
                "text_placement": "Headlines left x=72; mascot top-right; captions y≈1370–1500; nothing important below y=1640.", "zoom_movement": "None.", "transitions": "Crisp fade-through.",
                "export": "1080×1920 · 60 fps · H.264 · AAC 192 kbps"},
    "cover": {"title": "₹99 × 5 = ?", "design": "Paper background; giant '₹99 × 5 = ?'; 'The subscription trap' line; mascot wide-eyed; logo top-left. Key content inside the central 1080×1350 crop.", "prompt": "render/cover_003.py"},
    "publishing": {
        "caption_hook": "Five plans. ₹99 each. So what's that a year? 🤔",
        "caption": "Five plans. ₹99 each. So what's that a year? 🤔\n\n5 × ₹99 = ₹495 a month. × 12 = ₹5,940 a year. (Illustrative example.) And that's before the one you forgot is still running.\n\nHow to catch them:\n1️⃣ Scan last month's statement\n2️⃣ List every recurring charge\n3️⃣ Note each renewal date\n\nIn TrackMyRupee you add each one once — it shows the monthly and yearly total and reminds you three days before it renews. No bank login. No SMS access.\n\n👉 Try the free demo — link in bio.\n💾 Save this for your next month-end check.\n📩 Send it to a friend with 'just one more subscription'.\n\n(Friday's carousel has the full list of 10 expenses people forget — check your feed.)\n\nIllustrative numbers. Not financial advice.",
        "cta": "Try the free demo — link in bio",
        "hashtags_core": ["#PersonalFinanceIndia", "#ExpenseTracker", "#Subscriptions", "#MoneyManagement", "#TrackMyRupee"],
        "hashtags_extended": ["#BudgetingTips", "#IndianFinance", "#MoneyHabits"],
        "hashtag_note": "Use the 5 core tags + at most 2 extended.",
        "pinned_comment": "Be honest: how many subscriptions are running on your phone right now? 👇 (Numbers in the Reel are illustrative.)",
        "story_text": ["Story 1 (poll): 'Subscriptions running right now?' — 1–3 / 4–6 / lost count", "Story 2 (Reel share): '₹99 × 5 = ?'", "Story 3 (link sticker → trackmyrupee.com/demo): 'See every renewal in one place — free demo, no signup'"],
        "alt_text": "Animated explainer: five ₹99 plans add up to ₹495 a month and ₹5,940 a year; three steps to catch forgotten subscriptions; TrackMyRupee shows monthly and yearly totals and renewal reminders.",
    },
    "image_prompts": [], "video_prompts": [],
    "character": {"name": "Rupee Minis (narrator)", "note": "Approved mascot; no people on screen.", "bible": "assets/mascot/*.svg", "consistency_rules": []},
    "asset_checklist": [{"item": "Draft video (captions + voiceover)", "status": "done", "file": "trackmyrupee_reel_003_draft_vo_en.mp4"}, {"item": "Clean no-caption export", "status": "done", "file": "trackmyrupee_reel_003_clean_nocaptions_vo_en.mp4"},
                        {"item": "Cover 1080×1920", "status": "done", "file": "cover_1080x1920.png"}, {"item": "Real app captures (demo account)", "status": "done", "file": "assets/app_captures/recurring_*.png"},
                        {"item": "Licensed music", "status": "pending", "file": "—"}],
    "fact_check_flags": [],
    "learning_log": {"approved": False, "notes": []},
}


def _windows():
    import json
    from pathlib import Path
    import os
    f = Path(__file__).resolve().parents[1] / "assets" / "vo" / ("reel_003_user" if os.environ.get("REEL3_VO") == "user" else "reel_003") / "durations.json"
    if not f.exists():
        return
    mins = [4.2, 6.0, 4.6, 7.0, 9.0, 4.8]; t = 0.0
    for sc, d, m in zip(PACKAGE["scenes"], json.loads(f.read_text()), mins):
        w = max(m, d + (0.4 if os.environ.get("REEL3_VO") == "user" else 0.55)); sc["t"] = f"{t:.1f}–{t + w:.1f}"; t += w
    PACKAGE["strategy"]["duration"] = f"≈{t:.0f} seconds (6 scenes; deliberately short)"


_windows()
