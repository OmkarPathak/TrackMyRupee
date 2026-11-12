"""Reel 003 timing from the narration clip lengths (assets/vo/reel_003/durations.json)."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.reel_003 import PACKAGE

import os
VOICE = os.environ.get("REEL3_VO", "synth")          # "user" = the director's recorded voice
VO_DIR = ROOT / "assets" / "vo" / ("reel_003_user" if VOICE == "user" else "reel_003")
MIN_WIN = [4.2, 6.0, 4.6, 7.0, 9.0, 4.8]
BUFFER, VO_OFFSET = (0.4 if VOICE == 'user' else 0.55), 0.1
VO_TEXT = [s["vo_en"] for s in PACKAGE["scenes"]]
DURS = json.loads((VO_DIR / "durations.json").read_text()) if (VO_DIR / "durations.json").exists() else [3.0] * len(VO_TEXT)
SCENES, _t = [], 0.0
for _i, _d in enumerate(DURS):
    _w = max(MIN_WIN[_i], _d + BUFFER); SCENES.append((round(_t, 3), round(_t + _w, 3))); _t += _w
DUR = round(_t, 3)
STARTS = [a + VO_OFFSET for a, _ in SCENES]
ENDS = [b for _, b in SCENES]


SEGS = json.loads((VO_DIR / "segments.json").read_text()) if (VO_DIR / "segments.json").exists() else None


def beat(i, phrase, lead=0.0):
    """Time at which `phrase` is spoken. With a human recording, words are spread over the voiced
    segments (so pauses are respected); otherwise speech is assumed uniform per character."""
    txt = VO_TEXT[i]; k = txt.find(phrase)
    if k < 0:
        raise ValueError(f"{phrase!r} not in scene {i+1}")
    segs = SEGS[i] if SEGS else None
    if segs:
        tot = sum(b - a for a, b in segs); cum = 0.0
        for a, b in segs:
            span = (b - a) / tot * len(txt)
            if k < cum + span or (a, b) == tuple(segs[-1]):
                return STARTS[i] + a + (k - cum) / max(span, 1e-6) * (b - a) - lead
            cum += span
    return STARTS[i] + DURS[i] * (k / max(1, len(txt))) - lead
