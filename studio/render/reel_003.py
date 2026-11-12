"""Reel 003 — The ₹99 subscription trap. Calm-paper style, Rupee Minis narrates (Line mouth,
audio-driven), real app captures. Reuses helpers from reel_001. Timing: timeline_003.py."""
from __future__ import annotations

import json
import math
import subprocess
import sys
from multiprocessing import Pool
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render import mascot, timeline_003 as T  # noqa: E402
from render.brand import C, Canvas, clamp, fmt_inr, prog, rgb  # noqa: E402
from render.reel_001 import env, io, lerp, mix, pill, put_app, q, rise, txt  # noqa: E402

W, H, FPS, DUR = 1080, 1920, 60, T.DUR
LM, S = 72, T.SCENES
CUES_PATH = Path(__file__).resolve().parents[1] / "assets" / "vo" / "reel_003" / "cues.json"
_cues = None
PRICE, PLANS = 99, 5
MONTHLY, YEARLY = PRICE * PLANS, PRICE * PLANS * 12
assert (MONTHLY, YEARLY) == (495, 5940)


def cue(n):
    global _cues
    if _cues is None:
        _cues = json.loads(CUES_PATH.read_text()) if CUES_PATH.exists() else []
    return _cues[n] if n < len(_cues) else {"shape": "X", "open": 0.0, "energy": 0.0}


def draw_mascot(c, t, n):
    corner, end = (925, 345, 190), (540, 700, 300)
    if t < S[5][0]:
        cx, yb, sz = corner
    else:
        k = io(prog(t, S[5][0], 0.6)); cx, yb, sz = (lerp(a, b, k) for a, b in zip(corner, end))
    cu = cue(n); en = cu["energy"]
    if t < 0.5:
        sz *= max(0.01, min(1.06, q(prog(t, 0.05, 0.4)) * 1.03))
    wide = T.beat(0, "a year?") - 0.15 <= t < S[0][1] or S[2][0] <= t < S[3][0]
    mood = "wide" if wide else "talk"
    if t >= S[5][0] + 0.3:
        mood = "happy" if cu["shape"] == "X" else "talk"
    rot = math.sin(t * 2.1) * 1.6 + en * 2.2 * math.sin(t * 9)
    if T.beat(0, "a year?") <= t < S[0][1]:
        rot = math.sin((t - T.beat(0, "a year?")) * 7) * 5
    mascot.draw(c, cx, yb, sz, t=t, mood=mood, rot=rot, squash=0.05 * en, pal=mascot.LIGHT, wiggle=0.6,
                mouth=(cu["shape"], cu["open"]), mstyle=mascot.NARRATOR_MSTYLE, blink=1.0 if (t % 3.3) < 0.12 else 0.0)


def hdr(c, t, a0, l1, l2=None, size=76, e=1.0, col2="coral400", y1=330, y2=430):
    a, dy = rise(t, a0); txt(c, LM, y1 + dy, l1, "pjxb", size, C["ink900"], a * e)
    if l2:
        a, dy = rise(t, a0 + 0.3); txt(c, LM, y2 + dy, l2, "pjxb", size, C[col2], a * e)


def scene1(c, t):
    a0, b0 = S[0]; e = env(t, a0, b0, 0.1, 0.25)
    def pop(t0, dur=0.3):
        p = q(prog(t, t0, dur)); return max(p, 0.0), 0.9 + 0.1 * p
    # giant maths, visible on frame 0
    a1 = max(0.9, q(prog(t, 0.0, 0.25)))
    s1 = 260 * (0.92 + 0.08 * q(prog(t, 0.0, 0.3)))
    txt(c, LM, 760, "₹99", "pjxb", int(s1), C["ink900"], a1 * e)
    txt(c, LM + 4, 830, "a month, per plan", "pjsb", 36, C["grey"], q(prog(t, 0.3, 0.4)) * e)
    w99 = c.text_width("₹99", "pjxb", 260, 0)
    t5 = T.beat(0, "Five plans") + 0.25
    a2, sc2 = pop(t5)
    txt(c, LM + w99 + 36, 760, "× 5", "pjxb", int(260 * sc2), C["coral400"], a2 * e)
    tq = T.beat(0, "So what's") - 0.05
    a3, sc3 = pop(tq, 0.35)
    txt(c, LM, 1080, "= ?", "pjxb", int(260 * (1 + .2 * (1 - q(prog(t, tq + 0.1, 0.35))))), C["ink900"], a3 * e)
    txt(c, LM + 4, 1150, "in a year", "pjsb", 36, C["grey"], q(prog(t, tq + 0.4, 0.4)) * e)


MONTHS = "JFMAMJJASOND"


def scene2(c, t):
    a0, b0 = S[1]; e = env(t, a0, b0, 0.25, 0.25)
    ta = T.beat(1, "₹495") - 0.05; tb = T.beat(1, "₹5,940") - 0.1
    a, dy = rise(t, ta); txt(c, LM, 330 + dy, "₹495 a month.", "pjxb", 76, C["ink900"], a * e)
    a, dy = rise(t, tb); txt(c, LM, 440 + dy, "₹5,940 a year.", "pjxb", 92, C["coral400"], a * e)
    x0, y0, tw, th, gap = 81, 560, 216, 118, 18
    filled = 0
    for i in range(12):
        r, k = divmod(i, 4); x, y = x0 + k * (tw + gap), y0 + r * (th + gap)
        tf = tb + 0.1 + i * 0.12
        a_ = q(prog(t, tb - 0.1 + i * 0.03, 0.3)) * e
        if a_ <= 0.01:
            continue
        f = io(prog(t, tf, 0.25)); filled += f
        col = rgb("#E9E6E1"); col = tuple(int(lerp(col[j], rgb(C["teal200"])[j], f)) for j in range(3))
        c.rrect(x, y, tw, th, 22, fill=col + (int(255 * a_),))
        txt(c, x + 20, y + 40, MONTHS[i] + [" ", "", "", "", "", "", "", "", "", "", "", ""][i], "pjsb", 26, C["grey"], a_)
        txt(c, x + tw / 2, y + 84, "₹495", "pjxb", 38, C["ink900"], a_ * (0.35 + 0.65 * f), "m")
    tot = int(round(MONTHLY * filled, -1)) if filled < 11.99 else YEARLY
    txt(c, W / 2, 1230, fmt_inr(tot), "pjxb", 150, C["coral400"], q(prog(t, tb, 0.3)) * e, "m")
    txt(c, W / 2, 1290, "12 × ₹495", "pjsb", 32, C["grey"], q(prog(t, tb + 0.3, 0.4)) * e, "m")
    pill(c, W / 2, 1600, "ILLUSTRATIVE EXAMPLE", C["teal800"], C["teal50"], 24, q(prog(t, a0 + 0.3, 0.4)) * e)


PLAN_NAMES = ["Music", "Video", "Cloud storage", "Learning app", "News"]


def scene3(c, t):
    a0, b0 = S[2]; e = env(t, a0, b0, 0.25, 0.25)
    hdr(c, t, a0 + 0.05, "One of them", "you forgot.", 76, e=e)
    th = T.beat(2, "forgotten") - 0.1
    for i, nm in enumerate(PLAN_NAMES):
        ta = a0 + 0.35 + i * 0.12; ce = env(t, ta, b0, 0.3, 0.25) * e
        if ce <= 0.01:
            continue
        y = 540 + i * 116 + (1 - q(prog(t, ta, 0.35))) * 22
        hl = (i == 2) * io(prog(t, th, 0.3))
        col = tuple(int(lerp(255, rgb(C["coral50"])[j], hl)) for j in range(3))
        c.card(LM, y, W - 2 * LM, 96, 28, fill="#%02x%02x%02x" % col, alpha=ce)
        c.check(LM + 56, y + 48, 22, C["coral400"] if (i == 2 and hl > .5) else C["teal200"], ce)
        txt(c, LM + 100, y + 62, nm, "pjxb", 36, C["ink900"], ce)
        txt(c, W - LM - 40, y + 64, "₹99", "pjxb", 40, C["coral400"] if hl > .5 else C["ink900"], ce, "r")
        if i == 2 and hl > 0.02:
            pill(c, W / 2 + 40, y + 48, "still renewing", "#FFFFFF", C["coral400"], 24, hl * ce)


def scene4(c, t):
    a0, b0 = S[3]; e = env(t, a0, b0, 0.25, 0.25)
    hdr(c, t, a0 + 0.05, "Catch them in", "3 steps.", 76, e=e)
    steps = [(T.beat(3, "Scan last") - 0.1, "Scan last month's statement"), (T.beat(3, "List every") - 0.1, "List every recurring charge"), (T.beat(3, "Then note") - 0.1, "Note each renewal date")]
    for j, (ta, label) in enumerate(steps):
        ce = env(t, ta, b0, 0.3, 0.25) * e
        if ce <= 0.01:
            continue
        y = 540 + j * 150 + (1 - q(prog(t, ta, 0.4))) * 24
        c.card(LM, y, W - 2 * LM, 120, 30, alpha=ce)
        c.ellipse(LM + 62, y + 60, 30, fill=rgb(C["teal400"], ce)); txt(c, LM + 62, y + 75, str(j + 1), "pjxb", 38, "#FFFFFF", ce, "m")
        txt(c, LM + 118, y + 76, label, "pjxb", 36, C["ink900"], ce)


def scene5(c, t):
    a0, b0 = S[4]; e = env(t, a0, b0, 0.25, 0.25)
    tb = T.beat(4, "You see") - 0.15; tr = T.beat(4, "get a reminder") - 0.2
    p1 = env(t, a0, tb - 0.05, 0.25, 0.2) * e; p2 = env(t, tb + 0.05, tr - 0.05, 0.3, 0.2) * e; p3 = env(t, tr + 0.05, b0, 0.3, 0.25) * e
    if p1 > 0.01:
        hdr(c, t, a0 + 0.05, "Add each", "one once.", 80, e=p1)
        y = 540 + (1 - q(prog(t, a0 + 0.3, 0.4))) * 28
        put_app(c, "recurring_top", (W - 700) // 2, y, 700, 60, 500, p1)
        txt(c, W / 2, 540 + 790 + 46, "DEMO ACCOUNT · REAL APP", "pjsb", 22, C["grey"], p1 * .9, "m", 3)
    if p2 > 0.01:
        hdr(c, t, tb + 0.05, "See the yearly", "total.", 80, e=p2)
        y = 600 + (1 - q(prog(t, tb + 0.2, 0.4))) * 26
        put_app(c, "recurring_top", 80, y, 920, 112, 166, p2)
        txt(c, W / 2, y + 150, "Monthly · daily · yearly — automatically", "pjsb", 30, C["grey"], q(prog(t, tb + 0.6, 0.4)) * p2, "m")
        txt(c, W / 2, y + 330, "DEMO ACCOUNT · REAL APP", "pjsb", 22, C["grey"], p2 * .9, "m", 3)
    if p3 > 0.01:
        hdr(c, t, tr + 0.05, "Know before", "it renews.", 80, e=p3)
        for k, lab in enumerate(("Reminder 3 days before", "Renewing Soon list")):
            ta = tr + 0.3 + k * 0.3; ce = q(prog(t, ta, 0.35)) * p3
            if ce > 0.01:
                y = 540 + k * 108 + (1 - q(prog(t, ta, 0.35))) * 20
                c.card(LM, y, W - 2 * LM, 88, 28, alpha=ce); c.check(LM + 52, y + 44, 20, C["teal400"], ce); txt(c, LM + 92, y + 58, lab, "pjxb", 34, C["ink900"], ce)
        put_app(c, "recurring_top", 100, 800 + (1 - q(prog(t, tr + 0.4, 0.4))) * 24, 880, 285, 500, p3)
        txt(c, W / 2, 1345, "DEMO ACCOUNT · REAL APP", "pjsb", 22, C["grey"], p3 * .9, "m", 3)


def scene6(c, t):
    t0 = S[5][0]
    if t < t0:
        return
    a = q(prog(t, t0 + 0.35, 0.4)); b = q(prog(t, t0 + 0.7, 0.4)); d = q(prog(t, t0 + 1.0, 0.4)); f = q(prog(t, t0 + 1.35, 0.4))
    c.logo(W / 2 - 40, 790 - (1 - a) * 18, 80, False)
    txt(c, W / 2, 960, "TrackMyRupee", "pjxb", 92, C["ink900"], a, "m")
    txt(c, W / 2, 1030, "See every renewal in one place.", "pjsb", 36, C["coral400"], b, "m")
    c.card(W / 2 - 320, 1110, 640, 118, 59, fill=C["teal400"], alpha=d)
    txt(c, W / 2, 1186, "Try the free demo", "pjxb", 48, "#FFFFFF", d, "m")
    txt(c, W / 2, 1290, "trackmyrupee.com/demo", "pjsb", 38, C["ink900"], f, "m")
    txt(c, W / 2, 1356, "No bank login  ·  No SMS access", "pj", 30, C["grey"], f, "m")


def frame(n):
    t = n / FPS
    c = Canvas(W, H, bg=C["paper"])
    if t < S[5][0] - 0.2:
        c.logo(LM, 110, 46, True, word_color=C["ink900"], word_size=26)
    scene1(c, t); scene2(c, t); scene3(c, t); scene4(c, t); scene5(c, t); scene6(c, t)
    draw_mascot(c, t, n)
    return c.done().tobytes()


def render(out, preview=None):
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    if preview:
        for n in preview:
            Image.frombytes("RGB", (W, H), frame(n)).save(out.parent / f"preview_{n:05d}.png")
        return
    total = int(DUR * FPS)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an",
           "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with Pool(4) as pool:
        for i, buf in enumerate(pool.imap(frame, range(total), chunksize=8)):
            p.stdin.write(buf)
            if i % 300 == 0:
                print(f"  frame {i}/{total}", flush=True)
    p.stdin.close(); p.wait()


if __name__ == "__main__":
    args = sys.argv[1:]; prev = None
    if "--preview" in args:
        i = args.index("--preview"); prev = [int(x) for x in args[i + 1].split(",")]; args = args[:i]
    render(args[0], prev)
