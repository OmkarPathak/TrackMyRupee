"""Reel 002 — why your net worth matters (education-led; TMR as the one-place tool).
Plain data. Scene 't' windows are computed from the voiceover clip lengths (render/timeline_002.py)."""

SLUG = "trackmyrupee_reel_006_why_net_worth_matters"   # library idea reel-006 (net worth)

_VO = [
    "Same salary. Five years later, one friend is worth ten lakh more. Why?",
    "Because salary isn't the score. Net worth is: everything you own, minus everything you owe. One number.",
    "You own ten lakh fifty thousand. You owe three lakh forty. Net worth: seven lakh ten thousand.",
    "It shows your direction. It exposes quiet debt. And it gives every decision a scoreboard.",
    "But your money lives in five different places.",
    "So build a habit: log your spends in seconds, and update your balances now and then.",
    "TrackMyRupee puts it in one place: savings, FDs, PPF, EPF, NPS, funds, gold, loans. One number, with its history.",
    "No bank login. No SMS access. Try the free demo — link in bio.",
]

_COMMON = {"camera": "Locked-off. No screen-shake, no hard cuts.", "background": "Warm off-white paper (#FAF8F6), flat.", "lighting": "Flat graphic; soft card shadows only.",
           "transition": "Crisp fade-through (≈0.2s out / 0.3s in)"}

PACKAGE = {
    "id": "reel-006", "version": 1, "status": "review", "slug": SLUG,
    "title": "Why seeing your net worth matters",
    "strategy": {
        "pillar": "B — Financial education (with a Pillar C product-proof close)",
        "audience": "Salaried professionals 22–35, SIP/mutual fund investors, people with money spread across several apps",
        "objective": "Teach one idea clearly (net worth = own − owe, and why it matters), then show the habit + one-place tracking that makes it effortless. Drive demo taps. Education first; the product appears in the last third.",
        "duration": "≈48s (hook 4 · answer 7 · example 7 · why 6 · problem 3 · habit 5 · TrackMyRupee 10 · CTA 5)",
        "format": "Calm-paper explainer, mascot narrating (Line mouth, audio-driven), real app captures (demo account), illustrative example labelled",
        "emotional_angle": "Curiosity ('am I really richer?') → clarity (one simple formula) → relief ('one place, tiny habit')",
        "viewer_reaction": "'I've never added this up' → save it / send to a friend → try the demo",
        "feature": "Net worth (accounts list + net worth history, real), 34 Indian account types (real list), quick-add habit (real capture)",
        "conversion": "Free demo (link in bio → trackmyrupee.com/demo, no registration)",
        "why_this_idea": "Net worth is the reframe that makes every other money habit matter. It is the clearest education→product bridge: the product's central number, explained before it is sold.",
        "reference_note": "Instagram reel referenced by the director could not be opened (instagram.com is blocked in this session). Structure follows standard high-retention explainers: curiosity hook → one-line definition → worked example → why it matters → problem → solution. Re-check against the reference once the video file is shared.",
    },
    "hooks": {"selected": 0, "options": [
        "Same salary. Five years later, one friend is worth ten lakh more. Why?",
        "I'm a piggy bank, and I know exactly what's inside me: ₹5,000. Do you know what you're worth? (hook C)",
        "Quick test. What's your net worth? You've got three seconds. (hook B)",
        "Your salary went up. But are you actually richer? (v1 hook)",
        "Earning more ≠ owning more. Here's the number that tells the truth.",
        "You track your spending. Do you know what you're worth?",
        "Your bank balance is not your net worth.",
    ], "on_screen_used": "Same salary. / 5 years later. / Why? (Riya ₹14,00,000 vs Aman ₹4,00,000 — illustrative)",
        "note": "v2: chosen by the director from three tested concepts (A same-salary comparison, B 3-second test, C piggy-bank first-person). Built for a 5s average watch time: open loop + visual payoff inside 3 seconds; the answer arrives in scene 2."},
    "illustrative_networth": {
        "label": "Illustrative example — not real user data, not advice",
        "assets": [{"name": "Savings account", "amount": 120000}, {"name": "Fixed deposit", "amount": 200000}, {"name": "Mutual funds", "amount": 350000},
                   {"name": "EPF", "amount": 280000}, {"name": "Gold", "amount": 100000}],
        "liabilities": [{"name": "Car loan", "amount": 300000}, {"name": "Credit card due", "amount": 40000}],
        "net_worth": 710000,
    },
    "scenes": [
        {**_COMMON, "n": 1, "t": "0.0–4.3", "name": "Hook (A — same salary, different net worth)",
         "visual": "Frame 0: headline 'Same salary.' + two cards, Riya and Aman, both ₹80,000/month. 'Five years later' — two bars grow: Riya ₹14,00,000 (teal), Aman ₹4,00,000 (coral); a coral '₹10,00,000 gap' tag lands as the voice says 'ten lakh more'; 'Why?' pops. 'ILLUSTRATIVE EXAMPLE' pill. The mascot narrates, goes wide-eyed on 'Why?'.",
         "movement": "Lines rise 0.4s ease-out; bars grow 1.1s; gap tag pops; everything fades through 0.25s before scene 2.",
         "on_screen": "Same salary. · 5 years later. · Why? · Riya ₹14,00,000 vs Aman ₹4,00,000 · ₹10,00,000 gap",
         "vo_en": _VO[0], "sfx": "Soft ding, pop on the cards, whoosh as bars grow, thud on the gap tag"},
        {**_COMMON, "n": 2, "t": "4.3–11.3", "name": "The answer: define net worth",
         "visual": "Headline 'Salary isn't the score.' → coral 'Net worth is.' Then three blocks: a tall teal block 'What you own' − a shorter coral block 'What you owe' = a block 'Net worth' that grows to the exact difference.",
         "movement": "Blocks grow 0.55s ease-out in sequence with the voice; operators fade in.", "on_screen": "Salary isn't the score. Net worth is. = what you own − what you owe",
         "vo_en": _VO[1], "sfx": "Tick per block, soft thud on '='"},
        {**_COMMON, "n": 3, "t": "9.5–19.5", "name": "Worked example (illustrative)",
         "visual": "'ILLUSTRATIVE EXAMPLE'. Asset cards rise one by one — Savings ₹1,20,000 · FD ₹2,00,000 · Mutual funds ₹3,50,000 · EPF ₹2,80,000 · Gold ₹1,00,000 — then collapse to 'You own ₹10,50,000'. Two liability cards — Car loan ₹3,00,000 · Credit card due ₹40,000 — collapse to 'You owe ₹3,40,000'. The result card 'Net worth ₹7,10,000' lands in teal.",
         "movement": "Cards 0.35s ease-out at 0.4s stagger; collapse is a 0.3s cross-fade; result pops with a soft thud.", "on_screen": "Savings · FD · Funds · EPF · Gold = ₹10,50,000 · Loans & dues = ₹3,40,000 · Net worth ₹7,10,000",
         "vo_en": _VO[2], "sfx": "Tick per card, thud on the result"},
        {**_COMMON, "n": 4, "t": "19.5–28.0", "name": "Why it matters",
         "visual": "Headline 'Why it matters.' Three cards rise as each point is spoken: ① Shows direction — salary can rise while net worth stalls ② Exposes quiet debt — a loan can quietly cancel savings ③ A scoreboard for decisions — watch the trend, not just the number.",
         "movement": "Cards 0.4s ease-out, staggered to the voice.", "on_screen": "Shows direction · Exposes quiet debt · A scoreboard for decisions",
         "vo_en": _VO[3], "sfx": "Soft pop per card"},
        {**_COMMON, "n": 5, "t": "28.0–32.0", "name": "The problem: scattered",
         "visual": "Five white cards scatter in at slight angles: Bank app · Demat app · Mutual fund app · EPF passbook · Loan portal. Big coral '5 places. 0 totals.' (generic names only — no competitors).",
         "movement": "Cards pop with 0.12s stagger; slight drift.", "on_screen": "Bank app · Demat app · MF app · EPF passbook · Loan portal · 5 places. 0 totals.",
         "vo_en": _VO[4], "sfx": "Rapid pops"},
        {**_COMMON, "n": 6, "t": "32.0–37.5", "name": "Build the habit",
         "visual": "Headline 'Net worth needs a habit.' Two cards: Daily — 'Log what you spend, in seconds' (with the REAL quick-add input typing 'swiggy 320') · Monthly — 'Update your balances'.",
         "movement": "Real capture plays: typed text; cards slide up.", "on_screen": "Daily: log spends · Monthly: update balances · DEMO ACCOUNT · REAL APP",
         "vo_en": _VO[5], "sfx": "Key ticks"},
        {**_COMMON, "n": 7, "t": "37.5–47.5", "name": "TrackMyRupee: one place (real app)",
         "visual": "'One place.' REAL accounts screen (Net worth ₹81,77,750 with Cash/Fixed-income/Investments/Physical assets split — demo data) → chip cloud of real account types from the app (Savings · FD · RD · PPF · EPF · VPF · NPS · SSY · Demat · Mutual funds · ETF · SGB · Gold · Credit card · Home loan · Real estate · Insurance …'30+ account types') → REAL net worth history chart. 'One number, with its history.' Labelled DEMO DATA · REAL APP.",
         "movement": "Three real captures cross-fade sequentially (no overlap); chips pop with 25ms stagger.", "on_screen": "One place. · 30+ account types · One number, with its history. · DEMO ACCOUNT · REAL APP",
         "vo_en": _VO[6], "sfx": "Soft whoosh, pops, chime on the chart"},
        {**_COMMON, "n": 8, "t": "47.5–52.0", "name": "CTA",
         "visual": "Mascot centre (happy), logo + wordmark, coral 'See your net worth in one place.', teal 'Try the free demo' button, trackmyrupee.com/demo, 'No bank login · No SMS access'.",
         "movement": "Staggered fade-up.", "on_screen": "See your net worth in one place. · Try the free demo · trackmyrupee.com/demo · No bank login · No SMS access",
         "vo_en": _VO[7], "sfx": "Whoosh + chime"},
    ],
    "voiceover": {
        "direction": "Warm, curious, conversational — a friend explaining, not a lecturer. Slight lift on 'actually richer?'; slow down on 'One number.' and on the result. Initialisms are spelled for the TTS (F D, E P F, P P F, N P S, S M S).",
        "en": "\n\n".join(_VO), "hinglish": "(English-only for now.)", "hindi": "(English-only for now.)",
        "tts_status": "Generated with Kokoro af_heart (draft quality). Hindi/Hinglish when requested.",
    },
    "mascot": {"role": "Narrator (top-right corner, ≈190px). Mouth: approved 'Line' design, driven by the voiceover audio (render/lipsync.py); blinks; open 'talk' eye; wide-eyed on '?' and 'scattered'; happy on the CTA.",
               "rules": ["Never alter the artwork beyond the approved mouth/eye states."]},
    "music": {"direction": "Calm, minimal, warm: soft plucks/keys, very low under the voice (≈ −24 LUFS). No drops. Lift gently on the 'one place' reveal.",
              "structure": "0–4 pad · 4–20 light pulse (explain) · 20–28 tension-lite · 28–32 sparse · 32–47 resolve · 47–52 chime",
              "licensing": "Licensed/royalty-free or Instagram business-library audio only."},
    "editing": {"tool": "CapCut or ready-made export", "canvas": "9:16, 1080×1920, 60 fps",
                "timeline": ["Track 1: video (silent) — motion baked in.", "Track 2: narration (per-scene clips placed at scene start + 0.1s).", "Track 3: music, low.", "Captions: burned-in pills (clean no-caption export available)."],
                "text_placement": "Headlines left at x=72; mascot top-right; captions y≈1370–1500; nothing important below y=1640.",
                "zoom_movement": "None; motion is baked in.", "transitions": "Crisp fade-through only.",
                "export": "1080×1920 · 60 fps · H.264 High · CRF≈17 · AAC 192 kbps · MP4 faststart"},
    "cover": {"title": "Same salary. Why?", "design": "Paper background; 'Same salary. / 5 years later. / Why?' headline; two bars (₹14,00,000 vs ₹4,00,000, illustrative); mascot wide-eyed; logo top-left. Key content inside the central 1080×1350 crop.",
              "prompt": "Render with studio/render/cover_002.py (typography must come from the renderer, never an image model)."},
    "publishing": {
        "caption_hook": "Same salary. Five years later, one friend is worth ten lakh more. Why? 🤔",
        "caption": "Same salary. Five years later, one friend is worth ten lakh more. Why? 🤔\n\n(Illustrative example.) Net worth is the one number that answers it: everything you own, minus everything you owe. (The example in the Reel is illustrative.)\n\nWhy it matters:\n1️⃣ It shows your direction — salary can rise while net worth stalls\n2️⃣ It exposes quiet debt\n3️⃣ It gives every money decision a scoreboard\n\nThe hard part isn't the formula, it's keeping it up to date when your money sits in savings, FDs, PPF, EPF, NPS, funds, gold and loans. TrackMyRupee puts it in one place — log spends in seconds, update balances now and then, and watch one net worth number (with its history).\n\n👉 Try the free demo — link in bio. No bank login. No SMS access.\n\n💾 Save this for your next month-end check-in.\n📩 Send it to a friend who's earning more but feels the same.\n\nIllustrative numbers. Not financial advice.",
        "cta": "Try the free demo — link in bio",
        "hashtags_core": ["#NetWorth", "#PersonalFinanceIndia", "#FinancialLiteracy", "#MoneyManagement", "#TrackMyRupee"],
        "hashtags_extended": ["#FinanceForYoungProfessionals", "#ExpenseTracker", "#MoneyHabits", "#IndianFinance"],
        "hashtag_note": "Use the 5 core tags + at most 2 extended.",
        "pinned_comment": "What's one account you'd have to dig up to calculate your net worth today — FD, EPF, demat, loan? 👇 (Numbers in the Reel are illustrative.)",
        "story_text": ["Story 1 (poll): 'Same salary, different net worth — why?' — Spending / Debt / Investing / All of it", "Story 2 (Reel share): 'The one number most of us never add up'", "Story 3 (link sticker → trackmyrupee.com/demo): 'See a net worth in one place — free demo, no signup'"],
        "alt_text": "Animated explainer: net worth equals what you own minus what you owe, with an illustrative example, why it matters, and TrackMyRupee showing net worth in one place.",
    },
    "image_prompts": [], "video_prompts": [],
    "character": {"name": "Rupee Minis (narrator)", "note": "Uses the approved mascot; no people on screen.", "bible": "See assets/mascot/*.svg and STYLE_GUIDE.md", "consistency_rules": []},
    "asset_checklist": [
        {"item": "Draft video (captions + voiceover)", "status": "done", "file": "trackmyrupee_reel_002_draft_vo_en.mp4"},
        {"item": "Clean no-caption export", "status": "done", "file": "trackmyrupee_reel_002_clean_nocaptions_vo_en.mp4"},
        {"item": "Cover 1080×1920", "status": "done", "file": "cover_1080x1920.png"},
        {"item": "Real app captures (demo account)", "status": "done", "file": "assets/app_captures/*.png"},
        {"item": "Licensed music", "status": "pending", "file": "—"},
        {"item": "Hindi / Hinglish voiceover", "status": "pending", "file": "—", "note": "English first, as requested."},
        {"item": "Fact-check", "status": "done", "file": "qc-report.md", "note": "Definition only; numbers illustrative; product claims from app/FEATURES.md."},
    ],
    "fact_check_flags": [],
    "learning_log": {"approved": False, "notes": []},
}


# scene windows from the narration clip lengths (same rule as render/timeline_002.py)
def _windows():
    import json
    from pathlib import Path
    f = Path(__file__).resolve().parents[1] / "assets" / "vo" / "reel_002" / "durations.json"
    if not f.exists():
        return
    mins = [4.0, 6.0, 6.8, 6.0, 3.2, 5.4, 9.8, 5.2]; t = 0.0
    for sc, d, m in zip(PACKAGE["scenes"], json.loads(f.read_text()), mins):
        w = max(m, d + 0.55); sc["t"] = f"{t:.1f}–{t + w:.1f}"; t += w
    PACKAGE["strategy"]["duration"] = f"≈{t:.0f} seconds (8 scenes; windows follow the narration)"


_windows()


# ======================= APPROVED by the creative director (Reel 006 v2, hook A) =======================
PACKAGE["version"] = 3
PACKAGE["status"] = "approved"
PACKAGE["learning_log"] = {"approved": True, "notes": [
    "Hook A (same salary, different net worth) chosen from 3 tested concepts; built for a 5s average watch time",
    "Open loop + visual payoff inside ~3s; the answer lands in the very next scene",
    "Mascot narrates (approved 'Line' mouth, audio-driven) top-right; real app captures (demo account) for proof",
    "Education first (own - owe, worked illustrative example, why it matters), product in the last third",
    "Full detail: studio/STYLE_GUIDE.md"]}
