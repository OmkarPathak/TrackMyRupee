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


def _chunks(text):
    """Text pieces split after . ? ! , — as (start, end) character indices."""
    import re
    out, pos = [], 0
    for m in re.finditer(r"[.?!,—]+\s+|$", text):
        end = m.end() if m.group().strip() == "" or m.end() > pos else m.end()
        if end > pos:
            out.append((pos, min(end, len(text)))); pos = end
    return [c for c in out if c[1] > c[0]]


def _assign(text, segs):
    """Monotonic assignment of text chunks to voiced segments so each segment's share of the
    characters is close to its share of the speaking time (dynamic programming). Returns a
    (char_start, char_end) range per segment."""
    ch = _chunks(text); n, m = len(ch), len(segs)
    L = [b - a for a, b in ch]; tot_l = sum(L); D = [b - a for a, b in segs]; tot_d = sum(D)
    pre = [0]
    for x in L: pre.append(pre[-1] + x)
    INF = 1e18
    dp = [[INF] * (n + 1) for _ in range(m + 1)]; bk = [[0] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = 0.0
    for j in range(1, m + 1):
        for e in range(n + 1):
            for b in range(e + 1):
                if dp[j - 1][b] >= INF: continue
                cost = abs((pre[e] - pre[b]) / tot_l - D[j - 1] / tot_d) + dp[j - 1][b]
                if cost < dp[j][e]: dp[j][e] = cost; bk[j][e] = b
    spans, e = [], n
    for j in range(m, 0, -1):
        b = bk[j][e]; spans.append((ch[b][0] if b < n else len(text), ch[e - 1][1] if e > b else (ch[b][0] if b < n else len(text)))); e = b
    return spans[::-1]


def beat(i, phrase, lead=0.0):
    """Time at which `phrase` is spoken. With a human recording, text chunks are matched to the
    voiced segments (pauses respected); otherwise speech is assumed uniform per character."""
    txt = VO_TEXT[i]; k = txt.find(phrase)
    if k < 0:
        raise ValueError(f"{phrase!r} not in scene {i+1}")
    segs = SEGS[i] if SEGS else None
    if segs:
        spans = _assign(txt, segs)
        for (a, b), (c0, c1) in zip(segs, spans):
            if c0 <= k < c1 or (c1 == c0 and False):
                return STARTS[i] + a + (k - c0) / max(c1 - c0, 1) * (b - a) - lead
        a, b = segs[-1]
        return STARTS[i] + b - lead
    return STARTS[i] + DURS[i] * (k / max(1, len(txt))) - lead
