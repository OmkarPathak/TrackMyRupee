# TrackMyRupee Instagram — approved creative defaults
Recorded on approval of **Reel 001 "Where does your ₹50,000 salary disappear?"** (v4, 47.5s). These are the *default reference* for new content, not a template to repeat blindly: a different topic may need a different treatment — say so when it does.

1. **Visual style** — "Calm paper": warm off-white background (#FAF8F6), flat, no gradients/glows. White cards with soft shadows, rounded 22–36px; ✓ badges; colour-coded tiles/bars. Calm, uncluttered, content in the upper two-thirds; nothing important below y=1640. No screen-shake, no hard cuts.
2. **Typography** — Plus Jakarta Sans: ExtraBold for headlines/numbers, SemiBold/Medium for labels and body; DM Mono for typed input. Headlines left-aligned at x=72, 2–3 short lines, one line coral for emphasis. (Instrument Serif/night-teal look from v1–v2 is retired for Reels.) Devanagari: Noto Sans Devanagari.
3. **Colour palette** — ink #1A1A19 · coral #C23E27 (numbers, emphasis) · teal #0F7657 (CTA, positive) · blue #85B7EB (fixed costs) · amber #F4B44E (lifestyle) · teal-200 #5DCAA5 (savings) · coral-200 #F49C8C (unexplained) · grey #8A8A86 (secondary text) · caption pill #2B2B2B.
4. **Hook style** — Short declarative lines that rise one at a time: number → consequence → question ("₹50,000 in. / Gone by the 20th. / Where?"), with a card that visibly drains. Punchy through copy and rhythm, not shaking.
5. **Voiceover** — English (Indian audience), warm, conversational, short sentences. Kokoro `af_heart` for now (draft quality). Spell initialisms for the TTS: E M I, S I P, O T T, U P I, nine P M (render/tts_kokoro.py `spoken()`). Hindi/Hinglish only when requested.
6. **Editing** — 1080×1920, 60 fps. Crisp fade-through 0.2–0.3s, quart ease-out, 12–30ms staggers. Burned-in dark caption pills (#2B2B2B, white, y≈1370), split at sentence ends. Synth SFX bed (ticks, pops, soft thuds, coin clink); gentle, low music to be added (licensed).
7. **Duration** — 45–48s for story + education + product-proof Reels; simple single-idea Reels can be shorter (20–35s). Don't pad.
8. **Storytelling format** — Relatable problem with illustrative numbers → the insight/"why" (e.g. habit) → real product proof → free-demo CTA. Value before selling: product first appears in the last quarter.
9. **Quality bar** — Real app UI only from real captures (local app, demo account, 390×844 @3×; `assets/app_captures`) — never fake or generated UI. Label illustrative numbers ("ILLUSTRATIVE EXAMPLE") and real captures ("DEMO ACCOUNT · REAL APP"). Rupee Minis mascot (supplied SVG, light colourway) in the top-right corner; swallows a coin when something is "logged"; wide-eyed at the "?" moments; hero on the end card.
10. **Other preferences** — Calm background; smooth but crisp pacing; show how the product *actually* works and *why* the behaviour matters (habit) rather than only describing features. Commit/push work as it's approved. One content piece at a time, wait for approval.

## Narrating mascot (approved direction)
- **Mouth design: "Line"** — a small dark mouth on the face just under the snout (closed = a gentle smile line; opens to a small oval on louder/wider sounds). Code: `render/mascot.py` `NARRATOR_MSTYLE = 1`; shapes X/B/C/D/F/G (closed/small/medium/wide/round/thin).
- **Motion** — audio-driven (`render/lipsync.py`, no external tools): mouth from loudness + brightness + consonant noise; body bounce/sway from speech energy; blinks every ~3.3s; "talk" eye (open eye, slightly raised brow) while narrating.
- **Languages** — English now; Hindi voice tested in the proof; Hinglish to be written in Devanagari with English terms in Latin. Funnel intent: Instagram is the customer-acquisition funnel; primary focus is personal-finance *education* with soft, specific CTAs (free demo / free resource).
- Reel 001 (approved) stays mouthless; narrating Reels use the Line mouth.
- Open items: free/open-source stack only for now; Rhubarb (external lip-sync binary) was not used; richer voice (non-Kokoro) is a later upgrade.

## Hook & retention learnings (from Reel 006 approval)
- **Context:** Reel 001's average watch time was 5s (~10% of its length). Reel 006 is the first built around that: the hook must hold a viewer inside the first ~3 seconds.
- **What was approved:** an open loop with an immediate visual payoff — "Same salary. 5 years later. Why?" with two bars diverging (illustrative, labelled) — then the *answer in the next scene* ("Because salary isn't the score. Net worth is…").
- **Hook toolkit (tested as previews):** A comparison/puzzle (approved) · B timed challenge (3-second countdown; risky because of the silence) · C first-person mascot ("I'm a piggy bank…"; the best brand piece, kept for later).
- **Rules:** content on frame 0 (no slow intro); voice starts at 0.1s; one question, no setup; numbers visible and labelled illustrative; caption pill must not cover key visuals (keep bars/cards above y≈1340).
- **Narrated Reels:** mascot top-right (~190px), mouth from the voice audio; real product proof comes from local app captures (demo account), never mocked UI.
- **Measurement:** log each post's stats in the (git-ignored) tracker. Compare average watch time and % of length against Reel 001 (5s, 10.4%) before drawing conclusions; one post is not a trend.
