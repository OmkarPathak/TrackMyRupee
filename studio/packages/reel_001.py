"""Fully developed production package for Reel 001.
Plain data (JSON-serialisable). The studio loads it into the content store; the
exporter turns it into the production folder."""

SLUG = "trackmyrupee_reel_001_salary_disappears"

PACKAGE = {
    "id": "reel-001",
    "version": 3,
    "status": "review",  # awaiting the creative director's feedback
    "title": "Where does your ₹50,000 salary disappear?",
    "strategy": {
        "pillar": "A — Relatable money problems",
        "audience": "Salaried professionals 22–35, IT professionals, young working professionals",
        "objective": "Shares and saves first (relatability), profile visits and demo taps second. Establish the TrackMyRupee look.",
        "duration": "35 seconds (30–35s sweet spot; every beat earns its place)",
        "format": "Calm paper-style motion graphic (modelled on the director's reference animation): warm off-white background, bold rounded type with coral accents, white cards with soft shadows, Rupee Minis mascot in the corner, dark subtitle pills, slow fade-through transitions, 60 fps. One salary drawn as 50 tiles (1 tile = ₹1,000) named group by group; a crop of the real app screenshot in the solution beat.",
        "emotional_angle": "Recognition → mild shock ('₹8,000 with no name') → relief ('there is a simple fix')",
        "viewer_reaction": "'This is literally me' → send to a friend / save for salary day",
        "feature": "Quick-add: type an expense like a message (documented example: “swiggy 320”) + the Top Categories dashboard (real screenshot)",
        "conversion": "Tap 'Free demo' link in bio → trackmyrupee.com/demo (no registration needed to explore)",
        "why_first": "It is the broadest relatable hook in the library, needs no product explanation before delivering value, and the tile-grid device becomes a reusable brand visual for the Salary Diaries series.",
    },
    "hooks": {
        "selected": 0,
        "options": [
            "₹50,000 in. Gone by the 20th. Where?",
            "Your salary doesn't vanish. It leaks — ₹200 at a time.",
            "Be honest: can you account for last month's ₹50,000?",
            "Salary on the 1st. Panic on the 20th.",
            "A ₹50,000 salary. ₹8,000 nobody can explain.",
            "₹50,000 came in. Can you name where it went?",
        ],
        "on_screen_used": "₹50,000 → ₹0 · Gone by the 20th. · Where?",
        "note": "v3: three-line headline reveals line by line (₹50,000 in. / Gone by the 20th. / Where?) while a white 'Salary credited' card drains from ₹50,000 to ₹0 — punchy through copy and rhythm, no screen-shake. Previous v1 hook (slow count-up) kept as option 6.",
    },
    "illustrative_budget": {
        "label": "Illustrative example — not real user data, not advice",
        "salary": 50000,
        "groups": [
            {"name": "Rent", "amount": 15000, "kind": "fixed"},
            {"name": "EMI", "amount": 5000, "kind": "fixed"},
            {"name": "Bills", "amount": 3000, "kind": "fixed"},
            {"name": "Food", "amount": 9000, "kind": "lifestyle"},
            {"name": "Travel", "amount": 4000, "kind": "lifestyle"},
            {"name": "Subscriptions", "amount": 1000, "kind": "lifestyle"},
            {"name": "SIP", "amount": 5000, "kind": "savings"},
            {"name": "Unaccounted", "amount": 8000, "kind": "mystery"},
        ],
        "unaccounted_share": "8,000 / 50,000 = 16%",
    },
    "scenes": [
        {"n": 1, "t": "0.0–3.4", "name": "Hook (v2 — punchier)",
         "visual": "Frame 0 already shows ₹50,000 and a 'SALARY CREDITED · 1ST' pill. The Rupee Minis piggy pops in (squash-and-stretch) and a coin drops into its slot. At 1.0s the number drains to ₹0 in 0.9s (turning coral) while the piggy's eye goes wide and it shakes its head. 1.95s: slam 'Gone by the 20th.' with screen shake. 2.6s: bigger slam 'Where?' in coral. At 3.1s the piggy hops to the corner as the tiles pop in.",
         "camera": "Locked-off; 16px decaying screen-shake on each slam.",
         "background": "TrackMyRupee night gradient with soft teal glow.",
         "lighting": "Flat graphic; mascot has built-in soft shine/blush from the supplied artwork.", "movement": "Number drain (ease-in squared), mascot pop-in 0.45s, slam text scales 1.35–1.45× → 1× in 0.22–0.25s.",
         "on_screen": "SALARY CREDITED · 1ST · ₹50,000 → ₹0 · Gone by the 20th. · Where?",
         "vo_en": "Fifty thousand, credited. By the twentieth… gone. Where?",
         "vo_hinglish": "Pachaas hazaar credit hue. Beeswi tak… gayab. Kahaan?",
         "vo_hi": "पचास हज़ार आए। बीस तारीख़ तक… ग़ायब। कहाँ?",
         "sfx": "Ding at 0.0; boing 0.05; coin clink 0.62; falling sweep 1.0–1.9; slam 1.95; bigger slam 2.6; whoosh 3.1", "transition": "Piggy hops to bottom-left; tiles pop in (stagger)"},
        {"n": 2, "t": "3.4–7.0", "name": "Setup",
         "visual": "50 cream tiles pop into a 10×5 grid. Header '₹50,000 salary'. 'Let's follow one salary.' An 'ILLUSTRATIVE EXAMPLE' pill appears and stays on screen.",
         "camera": "Locked-off.", "background": "Night gradient.", "lighting": "—",
         "movement": "Tiles spring in with a 22ms stagger (ease-back).",
         "on_screen": "Let's follow one salary. · ILLUSTRATIVE EXAMPLE",
         "vo_en": "Let's follow one salary.", "vo_hinglish": "Chalo, ek salary ko follow karte hain.", "vo_hi": "चलिए, एक सैलरी का पीछा करते हैं।",
         "sfx": "Rapid soft pops under the tile stagger", "transition": "Cut-on-action into the first group"},
        {"n": 3, "t": "7.0–10.2", "name": "The fixed costs",
         "visual": "Tiles turn blue in order: Rent (15 tiles), EMI (5), Bills (3). Label swaps each time. 'Not yet named' counter ticks 50,000 → 27,000.",
         "camera": "Locked-off.", "background": "Night gradient.", "lighting": "—",
         "movement": "Colour fills sweep tile-by-tile at 45ms.",
         "on_screen": "Rent ₹15,000 · EMI ₹5,000 · Bills ₹3,000 · Fixed. You saw this coming.",
         "vo_en": "Rent, fifteen thousand. EMI, five. Bills, three. You saw those coming.",
         "vo_hinglish": "Rent, pandrah hazaar. EMI, paanch. Bills, teen. Yeh toh pata tha.",
         "vo_hi": "किराया, पंद्रह हज़ार। EMI, पाँच। बिल, तीन। ये तो पता था।",
         "sfx": "Tick per tile (pitch varies slightly)", "transition": "Direct continuation"},
        {"n": 4, "t": "10.2–14.2", "name": "Lifestyle",
         "visual": "Amber tiles: Food (9), Travel (4), Subscriptions (1). Caption 'Life happens.' Counter ticks down to ₹13,000.",
         "camera": "Locked-off.", "background": "Night gradient.", "lighting": "—",
         "movement": "Same sweep, faster rhythm.",
         "on_screen": "Food ₹9,000 · Travel ₹4,000 · Subscriptions ₹1,000 · Life happens.",
         "vo_en": "Food, travel, one OTT plan. Fourteen thousand more. Life happens.",
         "vo_hinglish": "Khana, travel, ek OTT plan. Chaudah hazaar aur. Zindagi hai.",
         "vo_hi": "खाना, सफ़र, एक OTT प्लान। चौदह हज़ार और। ज़िंदगी है।",
         "sfx": "Ticks", "transition": "Direct continuation"},
        {"n": 5, "t": "14.2–17.4", "name": "The good bit",
         "visual": "Green tiles: SIP (5). Caption 'Future you says thanks.' Counter reaches ₹8,000 (13,000 − 5,000).",
         "camera": "Locked-off.", "background": "Night gradient.", "lighting": "—",
         "movement": "Sweep; a riser begins under the last tiles.",
         "on_screen": "SIP ₹5,000 · Future you says thanks.",
         "vo_en": "A five-thousand rupee SIP. Future you says thanks.",
         "vo_hinglish": "Paanch hazaar ki SIP. Future you bolega thanks.", "vo_hi": "पाँच हज़ार की SIP। भविष्य वाला आप कहेगा — शुक्रिया।",
         "sfx": "Riser 15.2s → impact", "transition": "Riser peaks into the reveal"},
        {"n": 6, "t": "17.4–22.0", "name": "The reveal",
         "visual": "The 8 uncoloured tiles turn coral, pulse with a glow and show '?'. Counter flips to coral ₹8,000 with 'NO NAME · NO CATEGORY', then '16% of your salary'.",
         "camera": "Locked-off (all attention on the pulse).", "background": "Night gradient.", "lighting": "Coral glow on tiles.",
         "movement": "Slow 1.1Hz pulse; counter breathes ±4%.",
         "on_screen": "…and then, this. · NO NAME · NO CATEGORY · ₹8,000 · 16% of your salary",
         "vo_en": "And then… eight thousand rupees. Sixteen percent of your salary. No name. No category.",
         "vo_hinglish": "Aur phir… aath hazaar rupaye. Salary ka solah percent. Na naam, na category.",
         "vo_hi": "और फिर… आठ हज़ार रुपये। सैलरी का सोलह प्रतिशत। न नाम, न कैटेगरी।",
         "sfx": "Low thud at 17.6s (the emotional beat); silence-gap before the VO line", "transition": "Header changes; chips arrive"},
        {"n": 7, "t": "22.0–26.4", "name": "The leaks",
         "visual": "Four coral chips rise in: ₹180 chai · ₹349 late-night order · ₹99 forgotten renewal · ₹250 UPI to a friend. Then '…and many more like these.' Headline: 'It's never one big thing.'",
         "camera": "Locked-off.", "background": "Night gradient.", "lighting": "—",
         "movement": "Chips rise 40px with ease-out, 0.45s stagger.",
         "on_screen": "It's never one big thing. · four chips · …and many more like these.",
         "vo_en": "It's never one big thing. A chai. A late-night order. A renewal you forgot. A UPI tap.",
         "vo_hinglish": "Yeh kabhi ek bada kharcha nahi hota. Chai. Raat ka order. Bhooli hui renewal. Ek UPI tap.",
         "vo_hi": "ये कभी एक बड़ा खर्च नहीं होता। चाय। रात का ऑर्डर। भूली हुई रिन्यूअल। एक UPI टैप।",
         "sfx": "Soft pop per chip", "transition": "Whoosh; tiles slide out, phone rises"},
        {"n": 8, "t": "26.6–31.0", "name": "The fix (real UI)",
         "visual": "'Type it like a message.' A typed quote chip 'swiggy 320' (typographic, not a fake interface). 'Every rupee gets a name.' The REAL app screenshot (demo data) rises from the bottom, tagged 'DEMO DATA · FROM THE REAL APP'.",
         "camera": "Phone screenshot rises with ease-out; no zoom on the data.", "background": "Night gradient.", "lighting": "—",
         "movement": "Typewriter at 12 chars/s; phone slides 900px.",
         "on_screen": "Type it like a message. · “swiggy 320” · Every rupee gets a name. · DEMO DATA · FROM THE REAL APP",
         "vo_en": "Type it like a message — 'swiggy 320' — and every rupee gets a name.",
         "vo_hinglish": "Message ki tarah type karo — 'swiggy 320' — aur har rupaye ko mil jaata hai ek naam.",
         "vo_hi": "मैसेज की तरह टाइप करिए — 'swiggy 320' — और हर रुपये को नाम मिल जाता है।",
         "sfx": "Typewriter clicks, confirm ding", "transition": "Whoosh into end card"},
        {"n": 9, "t": "31.0–35.0", "name": "End card + CTA",
         "visual": "Real TrackMyRupee mark, wordmark in Instrument Serif, tagline, a teal 'Try the free demo' button, trackmyrupee.com/demo, 'No bank login · No SMS access'.",
         "camera": "Locked-off.", "background": "Night gradient.", "lighting": "—",
         "movement": "Staggered fade-up.",
         "on_screen": "TrackMyRupee · Know where every rupee went. · Try the free demo · trackmyrupee.com/demo · No bank login · No SMS access",
         "vo_en": "Try the free demo. Link in bio.", "vo_hinglish": "Free demo try karo. Link bio mein hai.", "vo_hi": "फ़्री डेमो आज़माइए। लिंक बायो में है।",
         "sfx": "Whoosh + soft chime at 31.4s", "transition": "End (hold 3s, loop-friendly)"},
    ],
    "voiceover": {
        "direction": "Warm, slightly amused, conversational — like a friend who's done the maths, never a lecturer. Brief pause after 'where did it go?' and after '…and then.' Drop the pace and energy for 'No name. No category.'",
        "en": "Fifty thousand, credited. By the twentieth… gone. Where?\n\nLet's follow one salary.\n\nRent, fifteen thousand. EMI, five. Bills, three. You saw those coming.\n\nFood, travel, one OTT plan. Fourteen thousand more. Life happens.\n\nA five-thousand rupee SIP. Future you says thanks.\n\nAnd then… eight thousand rupees. Sixteen percent of your salary. No name. No category.\n\nIt's never one big thing. A chai. A late-night order. A renewal you forgot. A UPI tap.\n\nType it like a message — 'swiggy 320' — and every rupee gets a name.\n\nTry the free demo. Link in bio.",
        "hinglish": "Pachaas hazaar credit hue. Beeswi tak… gayab. Kahaan?\n\nChalo, ek salary ko follow karte hain.\n\nRent, pandrah hazaar. EMI, paanch. Bills, teen. Yeh toh pata tha.\n\nKhana, travel, ek OTT plan. Chaudah hazaar aur. Zindagi hai.\n\nPaanch hazaar ki SIP. Future you bolega thanks.\n\nAur phir… aath hazaar rupaye. Salary ka solah percent. Na naam, na category.\n\nYeh kabhi ek bada kharcha nahi hota. Chai. Raat ka order. Bhooli hui renewal. Ek UPI tap.\n\nMessage ki tarah type karo — 'swiggy 320' — aur har rupaye ko mil jaata hai ek naam.\n\nFree demo try karo. Link bio mein hai.",
        "hindi": "पचास हज़ार आए। बीस तारीख़ तक… ग़ायब। कहाँ?\n\nचलिए, एक सैलरी का पीछा करते हैं।\n\nकिराया, पंद्रह हज़ार। EMI, पाँच। बिल, तीन। ये तो पता था।\n\nखाना, सफ़र, एक OTT प्लान। चौदह हज़ार और। ज़िंदगी है।\n\nपाँच हज़ार की SIP। भविष्य वाला आप कहेगा — शुक्रिया।\n\nऔर फिर… आठ हज़ार रुपये। सैलरी का सोलह प्रतिशत। न नाम, न कैटेगरी।\n\nये कभी एक बड़ा खर्च नहीं होता। चाय। रात का ऑर्डर। भूली हुई रिन्यूअल। एक UPI टैप।\n\nमैसेज की तरह टाइप करिए — 'swiggy 320' — और हर रुपये को नाम मिल जाता है।\n\nफ़्री डेमो आज़माइए। लिंक बायो में है।",
        "tts_status": "PENDING — see chat. Drop per-scene clips in studio/assets/vo/<lang>/scene_01…09 (or one vo.wav) and run build_reel_001.py; the mixer ducks SFX and flags overruns.",
    },
    "music": {
        "direction": "Minimal, warm, modern-Indian-urban lo-fi pulse, 96–104 BPM, no vocals. Soft tabla-style or muted-pluck texture is welcome but keep it understated. Low under the VO (-22 LUFS-ish bed), lift by ~3dB in the end card.",
        "structure": "0–3s: just a pad and the notification ding. 3–17s: add a steady light pulse that follows the tile ticks. 15–17.6s: riser then drop out. 17.6–22s: near-silent (single low note). 22–31s: pulse returns, brighter. 31–35s: resolve on a major chord with the chime.",
        "licensing": "Use a track licensed for commercial/business use (Instagram's business-licensed audio library or a licensed royalty-free library). Do not use trending pop tracks on a brand account.",
    },
    "mascot": {
        "name": "Rupee Minis — calm piggy bank (supplied brand artwork: assets/mascot/*.svg)",
        "usage": "Redrawn 1:1 in code from the supplied SVG (same geometry and 'on-teal' colourway) so its parts can animate. Roles: hook (receives the salary coin, reacts as it drains), corner 'watcher' through the tile story (calm → wide-eyed with a '?' bubble at the reveal → looking around during the leaks), swallows a coin when an expense is 'logged', hero on the end card.",
        "moods": "happy (closed smiling eye, from the SVG) · wide (open eye + raised brow, derived — flag if you'd rather keep the SVG eye only)",
        "rules": ["Never alter colours, proportions or the ₹ coin.", "Do not give it a mouth or limbs beyond the SVG.", "Always on dark teal or cream backgrounds."],
    },
    "character": {
        "note": "Optional live-action/AI inserts only; the Reel itself now uses the mascot, not people. These descriptions keep any generated person identical across shots.",
        "name": "Rohan Kulkarni (working name)",
        "bible": "27-year-old software engineer in Pune. Medium-brown skin tone, short neatly-cut black hair, light stubble, slim build. Wears a navy half-sleeve casual shirt over a white tee, black-framed rectangular glasses, a simple steel wristwatch on the left wrist, lanyard with a company ID tucked into the pocket. Expression range: easy-going → mildly puzzled. Realistic proportions, natural skin texture, no beauty-retouch look.",
        "consistency_rules": [
            "Paste the full bible line into EVERY prompt; never paraphrase it.",
            "Use the same seed / reference image for all shots (image-to-image from shot 1).",
            "Same wardrobe, same watch hand, same glasses in every shot.",
            "Phone screens must never be legible — shoot at an angle, face-down or out of focus. Real UI is only ever the supplied screenshot.",
        ],
    },
    "image_prompts": [
        {"id": "IMG-A", "use": "Optional cold-open insert before the number (0.0–1.0s), cut over by the count-up",
         "prompt": "Cinematic commercial photograph, 9:16 vertical. Rohan Kulkarni, 27-year-old Indian software engineer in Pune (medium-brown skin, short black hair, light stubble, black rectangular glasses, navy half-sleeve shirt over white tee, steel watch on left wrist), seated at a clean modern co-working desk at 9:40 pm, face lit by his phone, phone screen angled away from camera and not visible. Warm practical desk lamp, cool teal rim light from a window with city lights bokeh. Shallow depth of field, 50mm, natural skin texture, subtle film grain, teal-and-warm colour grade, premium fintech advertising look.",
         "negative": "distorted hands, extra fingers, readable phone screen, fake app interface, gibberish text, watermark, logo, stock-photo smile, foreign-looking setting, futuristic effects"},
        {"id": "IMG-B", "use": "Optional insert under 'A chai. A late-night order…' (22–26s) — hand + chai stall",
         "prompt": "Cinematic close-up, 9:16. Rohan's left hand (steel watch, same navy sleeve) holding a small steel tumbler of chai at an Indian street chai stall in the early evening, a paper-cup stack and glass jars blurred behind, soft golden-hour light, steam rising, a phone held in the right hand pointing at a QR-code stand (QR is blurred, no readable text). Realistic five-finger hands, natural skin, shallow depth of field, authentic Indian urban setting.",
         "negative": "distorted hands, extra fingers, readable text, fake app UI, watermark, wrong currency symbol, stock-photo look"},
        {"id": "IMG-C", "use": "Optional insert under 'Rent, fifteen thousand…' (7–10s) — Indian rupee reference",
         "prompt": "Top-down product photograph, 9:16. A neat fan of genuine-looking Indian ₹500 and ₹100 notes on a dark teal matte surface beside a modern smartphone lying face-down, soft diffused light from the left, subtle shadow, premium fintech advertising style, crisp focus on the notes' colour and texture. Avoid legible serial numbers.",
         "negative": "incorrect currency, fake currency details, gibberish text, watermark, distorted hands"},
    ],
    "video_prompts": [
        {"id": "VID-A", "from": "IMG-A", "duration": "3s", "prompt": "Slow push-in (dolly 8%), subtle handheld breathing, bokeh lights drift, his thumb scrolls once; no face distortion; keep phone screen unreadable."},
        {"id": "VID-B", "from": "IMG-B", "duration": "3s", "prompt": "Steam drifts upward, hand tilts the tumbler slightly, slow rack focus from the tumbler to the QR stand; no new objects appear."},
        {"id": "VID-C", "from": "IMG-C", "duration": "3s", "prompt": "Slow lateral slide left→right across the notes, notes stay stable (no morphing), light sweeps across the surface."},
    ],
    "editing": {
        "tool": "CapCut (mobile or desktop). Simplest path: import draft video from the export folder, replace the SFX bed with your own if desired, add your VO and music.",
        "canvas": "9:16, 1080×1920, 30 fps",
        "timeline": [
            "Track 1 (video): trackmyrupee_reel_001_draft.mp4 — use as-is; do not re-time scenes (all animations are baked to the 35.0s timeline).",
            "Track 2 (VO): record/import VO; align line starts to: 0.4s, 3.5s, 7.0s, 10.4s, 14.4s, 17.9s, 22.3s, 26.9s, 31.6s.",
            "Track 3 (music): licensed track, volume ~12–18% under VO; duck an extra 4dB during 17.6–19s.",
            "Track 4 (SFX): the draft already contains a synthesised SFX bed. Mute the draft's audio if you replace it with your own.",
            "Captions: import subtitles_en.srt (auto-captions style: DM Sans Bold, 56px, cream #F2F0EB, 3px teal #0F7657 outline, centred at y≈1540px).",
        ],
        "text_placement": "All key text sits inside Instagram's safe zone: 150px from the top, 280px from the bottom, 90px sides. Nothing important below y=1640.",
        "zoom_movement": "None needed — motion is baked in. If you want extra energy add a 3% slow zoom-in on scene 6 only.",
        "transitions": "Hard cuts between scenes; the riser→thud at 17.6s is the only 'big' transition. Do not add CapCut transitions.",
        "export": "1080×1920 · 30 fps · H.264 · High profile · 12–16 Mbps (CRF≈16) · AAC 192 kbps 44.1kHz · MP4 · faststart",
    },
    "cover": {
        "title": "Where does your ₹50,000 salary disappear?",
        "design": "1080×1920. Night gradient; headline in Instrument Serif, with '₹50,000' highlighted in cream and 'disappear?' in coral-200; below it the 50-tile grid with the 8 unnamed '?' tiles lit coral; logo bottom. Everything important is inside the central 1080×1350 crop so the profile-grid 4:5 crop keeps the whole idea.",
        "prompt": "Reproduce with the renderer: python studio/render/cover_001.py. For AI-generated alternatives: 'Minimal fintech poster, deep teal gradient background, 50 rounded square tiles in a 10×5 grid, 8 of them glowing coral with question marks, bold serif headline, generous negative space' — but the exact numerals must be overlaid in the renderer, never trusted to an image model.",
    },
    "publishing": {
        "caption_hook": "₹50,000 credited on the 1st. By the 20th, you're checking your balance twice.",
        "caption": "₹50,000 credited on the 1st. By the 20th, you're checking your balance twice. 👀\n\nThis is an illustrative salary, but the pattern is real for a lot of us: rent, EMI and bills are easy to see. It's the ₹8,000 with no name that quietly shapes the month — the chai, the late-night order, the renewal you forgot, the small UPI taps.\n\nTry this for one month: note every expense the moment it happens. With TrackMyRupee you can just type it like a message ('swiggy 320') and check it before saving.\n\n👉 Try the free demo — link in bio. No bank login. No SMS access.\n\n💾 Save this for next salary day.\n📩 Send it to the friend who's broke by the 20th.\n\nNumbers in this Reel are illustrative. Not financial advice.",
        "cta": "Try the free demo — link in bio",
        "hashtags_core": ["#PersonalFinanceIndia", "#ExpenseTracker", "#SalaryManagement", "#MoneyManagement", "#TrackMyRupee"],
        "hashtags_extended": ["#BudgetingTips", "#FinancialLiteracy", "#IndianFinance", "#MoneyTipsIndia", "#UPI", "#SalaryDay", "#FinanceForYoungProfessionals"],
        "hashtag_note": "Use the 5 core tags + at most 3 extended. Instagram weights relevance over volume.",
        "pinned_comment": "Be honest — where does most of YOUR unexplained spending go: food, shopping or UPI taps? 👇 (All numbers in the Reel are illustrative.)",
        "story_text": [
            "Story 1 (poll, before the Reel): 'Can you name where last month's salary went?' — Yes / Not really",
            "Story 2 (Reel share, with sticker): 'Salary-day reality check 👀'",
            "Story 3 (link sticker → trackmyrupee.com/demo): 'See where every rupee went — free demo, no signup'",
        ],
        "alt_text": "Animated grid of 50 tiles representing a ₹50,000 salary; rent, EMI, bills, food, travel and SIP are coloured, and 8 tiles marked '?' remain unexplained.",
    },
    "asset_checklist": [
        {"item": "Mascot artwork (Rupee Minis SVGs)", "status": "done", "file": "assets/mascot/*.svg"},
        {"item": "Draft video v2 (motion graphics + mascot, with SFX bed)", "status": "done", "file": "trackmyrupee_reel_001_draft.mp4"},
        {"item": "Silent video (for your own audio)", "status": "done", "file": "trackmyrupee_reel_001_silent.mp4"},
        {"item": "Reel cover 1080×1920 (with mascot)", "status": "done", "file": "cover_1080x1920.png"},
        {"item": "Profile-grid crop preview 1080×1350", "status": "done", "file": "cover_grid_crop_1080x1350.png"},
        {"item": "English subtitles (SRT)", "status": "done", "file": "subtitles_en.srt"},
        {"item": "Voiceover recording (EN / Hinglish / Hindi)", "status": "pending", "file": "—", "note": "No TTS available in the build environment. Record or configure a TTS provider."},
        {"item": "Licensed background music", "status": "pending", "file": "—"},
        {"item": "Real app screenshot (used in scene 8)", "status": "done", "file": "from repo: static/img/mobile-2.png (demo data)"},
        {"item": "Optional live-action inserts IMG-A/B/C", "status": "optional", "file": "—", "note": "Prompts provided. Only add if they look authentic; the Reel works without them."},
        {"item": "Fact-check pass", "status": "done", "file": "qc-report.md", "note": "No regulatory/changing facts used; all numbers illustrative."},
    ],
    "fact_check_flags": [],
    "learning_log": {"approved": False, "notes": []},
}


# --- v2: voiceover tightened so every line fits its scene at a natural pace --
_VO = {
    3: ("Rent. EMI. Bills. You saw those coming.", "Rent. EMI. Bills. Yeh toh pata tha.", "किराया। EMI। बिल। ये तो पता था।"),
    4: ("Food, travel, one OTT plan. Life happens.", "Khana, travel, ek OTT plan. Zindagi hai.", "खाना, सफ़र, एक OTT प्लान। ज़िंदगी है।"),
    5: ("A SIP. Future you says thanks.", "Ek SIP. Future you bolega thanks.", "एक SIP। आने वाला कल कहेगा — शुक्रिया।"),
    6: ("And then… eight thousand. Sixteen percent. No name. No category.", "Aur phir… aath hazaar. Solah percent. Na naam, na category.", "और फिर… आठ हज़ार। सोलह प्रतिशत। न नाम, न कैटेगरी।"),
    7: ("Never one big thing. Chai. A late-night order. A forgotten renewal.", "Kabhi ek bada kharcha nahi. Chai. Raat ka order. Bhooli hui renewal.", "कभी एक बड़ा खर्च नहीं। चाय। रात का ऑर्डर। भूली हुई रिन्यूअल।"),
    8: ("Just type it — swiggy 320 — and every rupee gets a name.", "Bas type karo — swiggy 320 — aur har rupaye ko naam mil jaata hai.", "बस टाइप करिए — स्विगी तीन सौ बीस — हर रुपये को नाम।"),
}
for _n, (_en, _hg, _hi) in _VO.items():
    _s = PACKAGE["scenes"][_n - 1]; _s["vo_en"], _s["vo_hinglish"], _s["vo_hi"] = _en, _hg, _hi
PACKAGE["scenes"][1]["vo_hi"] = "चलिए, एक सैलरी का पीछा करते हैं।"
for _k, _f in (("en", "vo_en"), ("hinglish", "vo_hinglish"), ("hindi", "vo_hi")):
    PACKAGE["voiceover"][_k] = "\n\n".join(sc[_f] for sc in PACKAGE["scenes"])


# --- v3: calm paper style (storyboard text updated to match the render) -----------
_BG = "Warm off-white paper (#FAF8F6), flat — no gradients or glows."
_V3 = {
    1: ("Three-line headline rises in one line at a time: '₹50,000 in.' (ink) → 'Gone by the 20th.' (coral) → 'Where?' (ink). A white 'Salary credited ✓ ₹50,000' card slides up and the amount drains smoothly to ₹0 as the label becomes 'Left on the 20th'. The Rupee Minis piggy pops in at right, swallows the salary coin, goes wide-eyed and shakes its head.",
        "Headline lines 0.6s ease-out rise (0.15 / 1.45 / 2.45s); amount drain 1.0–2.0s ease-in-out; everything fades out 3.1–3.55s."),
    2: ("Header 'One ₹50,000 salary' / coral 'How much can you name?'. 50 soft-grey tiles (1 tile = ₹1,000) fade-scale in with a 20ms stagger; big ink counter '₹50,000 left to name'. 'ILLUSTRATIVE EXAMPLE' pill stays on screen. Mascot glides to the top-right corner.",
        "Tiles 0.5s ease-out stagger from 4.0s; counter fades in 5.2s."),
    3: ("Tiles colour blue in order (Rent 15, EMI 5, Bills 3). A white card with a ✓ badge swaps per group ('Rent ₹15,000' …) while the counter eases down to ₹27,000.", "Per-tile colour 0.45s ease-in-out at 50ms stagger; card swap 0.45s."),
    4: ("Amber tiles: Food 9, Travel 4, Subscriptions 1; card swaps; counter eases to ₹13,000.", "Same rhythm."),
    5: ("Teal tiles: SIP 5; counter reaches ₹8,000.", "Same rhythm."),
    6: ("Remaining 8 tiles turn coral with '?' and a slow pulse (2.6 rad/s). Counter turns coral: '₹8,000 · no name · no category' then '16% of your salary'. Mascot goes wide-eyed with a '?' bubble.", "Colour shift 0.9s; pulse amplitude 20%."),
    7: ("Four white cards rise in like a paid-UPI list: Chai ₹180 · Late-night order ₹349 · Forgotten renewal ₹99 · UPI to a friend ₹250, then '…and many more like these.' Mascot looks around (gentle ±5° sway).", "Cards 0.6s ease-out at 0.45s stagger."),
    8: ("Headline 'Type it like a message. / One line.' with a grey input field typing 'swiggy 320' (mono). Chips: ₹320 · Swiggy · category suggested; 'You check it, then add it.' The mascot swallows a coin as it's logged. Then, sequentially (no overlap): headline → 'Every rupee gets a name.' and a crop of the REAL app screenshot (Top Categories, demo data) slides up.", "Typing 1.0s; out 29.6–30.05, in 30.15–30.75."),
    9: ("Mascot centre, TrackMyRupee mark + wordmark, coral tagline 'Know where every rupee went.', teal 'Try the free demo' button, trackmyrupee.com/demo, 'No bank login · No SMS access'.", "Staggered fade-up 0.6s."),
}
for _n, (_vis, _mv) in _V3.items():
    _s = PACKAGE["scenes"][_n - 1]; _s["visual"] = _vis; _s["movement"] = _mv; _s["background"] = _BG
    _s["camera"] = "Locked-off. No screen-shake, no hard cuts: scenes fade through."; _s["lighting"] = "Flat graphic; soft card shadows only."
    _s["transition"] = "Fade-through (≈0.4s out / 0.5s in)"
PACKAGE["scenes"][0]["name"] = "Hook (v3 — punchy copy, calm motion)"
PACKAGE["scenes"][0]["sfx"] = "Soft ding at 0.0; boing 0.05; coin clink 0.62; falling sweep 1.0–1.9; soft thuds on 'Gone…' and 'Where?'; whoosh 3.1"
PACKAGE["editing"]["canvas"] = "9:16, 1080×1920, 60 fps (30 fps also fine)"
PACKAGE["editing"]["timeline"][4] = "Captions: already burned in as a dark rounded pill (#2B2B2B, white PJS SemiBold 40px, y≈1370–1500). Use the 'clean_nocaptions' export if you prefer CapCut auto-captions or another language (subtitles_en.srt provided)."
PACKAGE["editing"]["text_placement"] = "Headline left-aligned at x=72; mascot top-right; captions pill y≈1370–1500; nothing important below y=1640 (Instagram UI)."
PACKAGE["editing"]["export"] = "1080×1920 · 60 fps (or 30) · H.264 High · CRF≈17 (~3 Mbps for this flat-colour content is fine) · AAC 192 kbps · MP4 faststart"
PACKAGE["cover"]["design"] = "1080×1920 calm paper background; three-line headline '₹50,000 in. / Gone by the 20th. / Where?' (coral middle line); the 50-tile grid with 8 coral '?' tiles; mascot (wide-eyed) bottom-right; logo top-left. Everything important sits inside the central 1080×1350 crop."
PACKAGE["cover"]["title"] = "₹50,000 in. Gone by the 20th. Where?"
PACKAGE["music"]["direction"] = "Calm, minimal, warm — soft plucks/keys, very low in the mix (about −24 LUFS bed under the voice), no drops. The visual style is gentle, so keep the music gentle."
for _a in PACKAGE["asset_checklist"]:
    if "Draft video" in _a["item"]:
        _a["item"] = "Draft videos v3 (60fps, captioned: EN / EN-via-Hindi-voice / Hindi) + clean no-caption versions"; _a["file"] = "trackmyrupee_reel_001_draft_vo_*.mp4"
    if "Silent video" in _a["item"]:
        _a["item"] = "Base video (no audio, no captions)"
