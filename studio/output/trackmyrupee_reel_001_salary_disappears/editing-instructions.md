# Editing instructions (CapCut)

**Tool:** CapCut (mobile or desktop). Simplest path: import draft video from the export folder, replace the SFX bed with your own if desired, add your VO and music.

**Canvas:** 9:16, 1080×1920, 60 fps (30 fps also fine)

## Timeline
- Track 1 (video): trackmyrupee_reel_001_silent.mp4 (47.5s, 60 fps) — all motion is baked in; do not re-time.
- Track 2 (VO): per-scene clips are placed at scene start + 0.1s (see render/timeline.py): 0.1s, 3.5s, 6.1s, 9.1s, 12.5s, 15.1s, 19.2s, 23.2s, 30.4s, 37.4s, 43.4s
- Track 3 (music): licensed track, volume ~12–18% under VO; duck an extra 4dB during 17.6–19s.
- Track 4 (SFX): the draft already contains a synthesised SFX bed. Mute the draft's audio if you replace it with your own.
- Captions: already burned in as a dark rounded pill (#2B2B2B, white PJS SemiBold 40px, y≈1370–1500). Use the 'clean_nocaptions' export if you prefer CapCut auto-captions or another language (subtitles_en.srt provided).

## Text placement
Headline left-aligned at x=72; mascot top-right; captions pill y≈1370–1500; nothing important below y=1640 (Instagram UI).

## Zoom & movement
None needed — motion is baked in. If you want extra energy add a 3% slow zoom-in on scene 6 only.

## Transitions
Hard cuts between scenes; the riser→thud at 17.6s is the only 'big' transition. Do not add CapCut transitions.

## Subtitles
See subtitles_en.srt (timed to the scene VO).

## Export
1080×1920 · 60 fps (or 30) · H.264 High · CRF≈17 (~3 Mbps for this flat-colour content is fine) · AAC 192 kbps · MP4 faststart
