"""Reel 003 timing from the narration clip lengths (assets/vo/reel_003/durations.json)."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.reel_003 import PACKAGE

VO_DIR = ROOT / "assets" / "vo" / "reel_003"
MIN_WIN = [4.2, 6.0, 4.6, 7.0, 9.0, 4.8]
BUFFER, VO_OFFSET = 0.55, 0.1
VO_TEXT = [s["vo_en"] for s in PACKAGE["scenes"]]
DURS = json.loads((VO_DIR / "durations.json").read_text()) if (VO_DIR / "durations.json").exists() else [3.0] * len(VO_TEXT)
SCENES, _t = [], 0.0
for _i, _d in enumerate(DURS):
    _w = max(MIN_WIN[_i], _d + BUFFER); SCENES.append((round(_t, 3), round(_t + _w, 3))); _t += _w
DUR = round(_t, 3)
STARTS = [a + VO_OFFSET for a, _ in SCENES]
ENDS = [b for _, b in SCENES]


def beat(i, phrase, lead=0.0):
    txt = VO_TEXT[i]; k = txt.find(phrase)
    if k < 0:
        raise ValueError(f"{phrase!r} not in scene {i+1}")
    return STARTS[i] + DURS[i] * (k / max(1, len(txt))) - lead
