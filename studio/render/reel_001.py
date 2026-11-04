"""Reel 001 — v4 "calm paper" style (reference: director's Claude-Design animation).
Warm off-white background, bold rounded type with coral accents, white cards with soft
shadows, Rupee Minis mascot in the corner. v4: crisper motion (quart ease-out, 0.2–0.3s
fade-through), a "why a habit" beat, and REAL app captures (assets/app_captures, demo account).

All money figures in the story are illustrative and labelled. Captions are added separately
(captions.py). Timing lives in timeline.py.

Usage:  python studio/render/reel_001.py out.mp4 [--preview 0,120,...]   (frame numbers @60fps)
"""
from __future__ import annotations

import math
import subprocess
import sys
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render import mascot, timeline as T  # noqa: E402
from render.brand import C, Canvas, clamp, fmt_inr, prog, rgb  # noqa: E402

W, H, FPS, DUR = 1080, 1920, 60, T.DUR
LM = 72
CAP = Path(__file__).resolve().parents[1] / "assets" / "app_captures"

GROUPS = [("rent", "Rent", 15000, "blue200"), ("emi", "EMI", 5000, "blue200"), ("bills", "Bills", 3000, "blue200"),
          ("food", "Food", 9000, "amber200"), ("travel", "Travel", 4000, "amber200"), ("subs", "Subscriptions", 1000, "amber200"),
          ("sip", "SIP", 5000, "teal200")]
GROUPS = [(k, l, a, c, T.GROUP_START[k]) for k, l, a, c in GROUPS]
MYSTERY, SALARY = 8000, 50000
assert sum(g[2] for g in GROUPS) + MYSTERY == SALARY
COLS, ROWS, TILE, GAP = 10, 5, 80, 12
GX, GY = (W - (COLS * TILE + (COLS - 1) * GAP)) // 2, 440
_tg = []
for g in GROUPS:
    _tg += [g] * (g[2] // 1000)
_tg += [None] * (MYSTERY // 1000)
_first = {}
for i, g in enumerate(_tg):
    if g and g[0] not in _first:
        _first[g[0]] = i


# ---- crisp motion helpers -----------------------------------------------------
def q(p):                      # quart ease-out: fast start, soft landing
    return 1 - (1 - clamp(p)) ** 4


def io(p):                     # ease-in-out (for number tweens)
    p = clamp(p); return 3 * p * p - 2 * p ** 3


def env(t, a, b, fi=0.3, fo=0.22):
    return q(prog(t, a, fi)) * (1 - io(prog(t, b - fo, fo)))


def rise(t, a, dur=0.4, dist=22):
    p = q(prog(t, a, dur)); return p, (1 - p) * dist


def lerp(a, b, k):
    return a + (b - a) * k


def mix(a, b, k):
    ra, rb = rgb(a), rgb(b); return "#%02x%02x%02x" % tuple(int(ra[i] + (rb[i] - ra[i]) * k) for i in range(3))


def txt(c, x, y, s, key, size, fill, a=1.0, anchor="l", tr=0):
    if a <= 0.01:
        return
    col = rgb(fill) if isinstance(fill, str) else fill
    c.text(x, y, s, key, size, col[:3] + (int(255 * clamp(a)),), anchor, tr)


def pill(c, cx, cy, s, fg, bg, size=24, a=1.0):
    if a <= 0.01:
        return
    w = c.text_width(s, "pjsb", size, 2) + 56
    c.rrect(cx - w / 2, cy - 30, w, 60, 30, fill=rgb(bg, a)); txt(c, cx, cy + size * .36, s, "pjsb", size, fg, a, "m", 2)


# ---- real app captures ---------------------------------------------------------
@lru_cache(maxsize=40)
def appimg(name, w, y0, y1):
    """Crop a real 390x844@3x capture to css rows y0..y1, resized to w px (design) at 2x."""
    im = Image.open(CAP / f"{name}.png").convert("RGBA").crop((0, y0 * 3, 1170, y1 * 3))
    h = round(im.height * w / im.width)
    im = im.resize((w * 2, h * 2), Image.LANCZOS)
    m = Image.new("L", im.size, 0); ImageDraw.Draw(m).rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=56, fill=255)
    im.putalpha(m)
    return im, w, h


def put_app(c, name, x, y, w, y0, y1, a=1.0, shadow=True):
    if a <= 0.01:
        return 0
    im, w, h = appimg(name, w, y0, y1)
    if shadow:
        c.shadow(x, y, w, h, 28, alpha=0.12 * a)
    if a < 1:
        im = im.copy(); im.putalpha(im.getchannel("A").point(lambda v: int(v * a)))
    c.img.paste(im, (c._s(x), c._s(y)), im)
    return h


# ---- mascot ---------------------------------------------------------------------
def mascot_pose(t):
    hook, corner, end = (905, 590, 175), (925, 335, 150), (540, 690, 290)
    if t < 3.4:
        return hook
    if t < 3.9:
        k = io(prog(t, 3.4, 0.5)); return tuple(lerp(a, b, k) for a, b in zip(hook, corner))
    if t < T.END_IN:
        return corner
    k = io(prog(t, T.END_IN, 0.6)); return tuple(lerp(a, b, k) for a, b in zip(corner, end))


def draw_mascot(c, t):
    cx, yb, sz = mascot_pose(t)
    mood, rot, squash, coin_y, front = "happy", 0.0, 0.0, None, True
    if t < 0.7:
        sz *= max(0.01, min(1.08, q(prog(t, 0.1, 0.45)) * 1.04)); squash = 0.08 * (1 - prog(t, 0.45, 0.25))
    if t < 3.9:
        if 0.6 <= t < 1.1:
            coin_y = 32 + 30 * io(prog(t, 0.6, 0.45)); front = coin_y < 40
        elif 1.1 <= t < 3.6:
            coin_y, front = 70, False
        elif t >= 3.6:
            coin_y = 60 - 28 * q(prog(t, 3.6, 0.4)); front = coin_y < 42
        if t >= 1.15:
            mood = "wide"
        if 1.2 <= t < 1.9:
            rot = math.sin((t - 1.2) * 16) * 4 * (1 - prog(t, 1.2, 0.7))
    if T.REVEAL <= t < T.HABIT:
        mood = "wide"
        if t >= T.LEAKS:
            rot = math.sin(t * 3.4) * 5
    if T.HABIT + 2.0 <= t < T.HABIT + 7.0:
        rot = math.sin(t * 4.0) * 3                    # nodding along
    if T.ADD_TAP <= t < T.ADD_TAP + 1.1:               # "Add" -> coin into the piggy
        if t < T.ADD_TAP + 0.4:
            coin_y = 32 + 30 * io(prog(t, T.ADD_TAP, 0.4)); front = coin_y < 40
        else:
            coin_y = 62 - 30 * q(prog(t, T.ADD_TAP + 0.4, 0.5)); front = coin_y < 42
        squash = 0.07 * math.sin(math.pi * prog(t, T.ADD_TAP + 0.3, 0.4))
    if T.END_IN + 0.6 <= t < T.END_IN + 1.2:
        squash = 0.08 * math.sin(math.pi * prog(t, T.END_IN + 0.6, 0.6))
    mascot.draw(c, cx, yb, sz, t=t, mood=mood, rot=rot, squash=squash, coin_y=coin_y, coin_front=front, pal=mascot.LIGHT, wiggle=0.8)
    if T.REVEAL + 0.4 <= t < T.LEAKS and prog(t, T.REVEAL + 0.4, 0.3) > 0.1:
        a = q(prog(t, T.REVEAL + 0.4, 0.3)) * (1 - prog(t, T.LEAKS - 0.3, 0.3))
        bx, by = cx - 105, yb - 150
        c.ellipse(bx, by, 30 * a, fill=rgb(C["coral200"])); txt(c, bx, by + 14 * a, "?", "pjxb", int(40 * a) + 1, C["coral400"], 1, "m")


# ---- scenes ------------------------------------------------------------------------
def scene_hook(c, t):
    if t >= 3.5:
        return
    out = 1 - io(prog(t, 3.1, 0.3))
    for s, y, col, a0 in (("₹50,000 in.", 330, C["ink900"], 0.1), ("Gone by the 20th.", 440, C["coral400"], 1.2), ("Where?", 550, C["ink900"], 2.2)):
        a, dy = rise(t, a0); txt(c, LM, y + dy, s, "pjxb", 94, col, a * out)
    a, dy = rise(t, 0.25, 0.45, 30); a *= out
    y0 = 720 + dy
    c.card(LM, y0, W - 2 * LM, 132, 30, alpha=a)
    c.check(LM + 62, y0 + 66, 24, C["amber200"], a)
    drain = io(prog(t, 0.9, 0.9)); sw = io(prog(t, 1.7, 0.3))
    txt(c, LM + 112, y0 + 82, "Salary credited", "pjsb", 40, C["ink900"], a * (1 - sw))
    txt(c, LM + 112, y0 + 82, "Left on the 20th", "pjsb", 40, C["ink900"], a * sw)
    txt(c, W - LM - 40, y0 + 84, fmt_inr(int(round(SALARY * (1 - drain), -2))), "pjxb", 46, mix(C["ink900"], C["coral400"], drain), a, "r")


def spent(t):
    return sum(g[2] * io(prog(t, g[4] + 0.15, 0.5)) for g in GROUPS)


def draw_grid(c, t, vis):
    for i in range(COLS * ROWS):
        r, k = divmod(i, COLS); x, y = GX + k * (TILE + GAP), GY + r * (TILE + GAP)
        a = q(prog(t, T.STORY_IN + 0.15 + i * 0.012, 0.3)) * vis
        if a <= 0.01:
            continue
        sz = TILE * (0.92 + 0.08 * a); ox, oy = x + (TILE - sz) / 2, y + (TILE - sz) / 2
        g = _tg[i]
        if g:
            col = mix(C["tile"], C[g[3]], io(prog(t, g[4] + (i - _first[g[0]]) * 0.028, 0.3)))
            c.rrect(ox, oy, sz, sz, 16, fill=rgb(col, a))
        else:
            m = io(prog(t, T.REVEAL, 0.5)); pulse = 0.5 + 0.5 * math.sin((t - T.REVEAL) * 3.0)
            col = mix(C["tile"], C["coral200"], m * (0.8 + 0.2 * pulse))
            c.rrect(ox, oy, sz, sz, 16, fill=rgb(col, a))
            if m > 0.5:
                txt(c, ox + sz / 2, oy + sz / 2 + 17, "?", "pjxb", 52, C["coral400"], a * (m - .5) * 2, "m")


def scene_story(c, t):
    vis = env(t, T.STORY_IN, T.STORY_OUT, 0.35, 0.25)
    if vis <= 0.01:
        return
    a, dy = rise(t, T.STORY_IN + 0.15); a *= vis
    txt(c, LM, 305 + dy, "One ₹50,000 salary", "pjxb", 58, C["ink900"], a)
    swap = io(prog(t, T.REVEAL - 0.1, 0.25))
    txt(c, LM, 360 + dy, "How much can you name?", "pjsb", 30, C["coral400"], a * (1 - swap))
    txt(c, LM, 360 + dy, "Who took the rest?", "pjsb", 30, C["coral400"], a * swap)
    draw_grid(c, t, vis)

    for gi, g in enumerate(GROUPS):
        a0 = g[4] + 0.1
        a1 = (GROUPS[gi + 1][4] + 0.1) if gi + 1 < len(GROUPS) else T.REVEAL - 0.1
        a1 = max(a1, a0 + 0.8) if gi + 1 < len(GROUPS) else a1
        e = env(t, a0, a1, 0.25, 0.2) * vis
        if e <= 0.01:
            continue
        y0 = 935 + (1 - q(prog(t, a0, 0.35))) * 20
        c.card(LM, y0, W - 2 * LM, 104, 28, alpha=e)
        c.check(LM + 58, y0 + 52, 22, C[g[3]], e)
        txt(c, LM + 104, y0 + 66, g[1], "pjxb", 38, C["ink900"], e)
        txt(c, W - LM - 40, y0 + 68, fmt_inr(g[2]), "pjxb", 42, C["coral400"], e, "r")

    if t < T.LEAKS:
        ca = q(prog(t, 4.4, 0.35)) * vis * (1 - io(prog(t, T.LEAKS - 0.3, 0.25)))
        col = mix(C["ink900"], C["coral400"], io(prog(t, T.REVEAL, 0.4)))
        sc = 1 + 0.025 * math.sin((t - T.REVEAL) * 3.0) if t > T.REVEAL else 1
        txt(c, W / 2, 1210, fmt_inr(int(round(SALARY - spent(t), -1))), "pjxb", int(132 * sc), col, ca, "m")
        sw = io(prog(t, T.REVEAL - 0.1, 0.25))
        txt(c, W / 2, 1264, "left to name", "pjsb", 32, C["grey"], ca * (1 - sw), "m")
        txt(c, W / 2, 1264, "no name · no category", "pjsb", 32, C["grey"], ca * sw, "m")
        txt(c, W / 2, 1316, "16% of your salary", "pjxb", 34, C["coral400"], q(prog(t, T.REVEAL + 1.5, 0.4)) * ca, "m")

    if T.LEAKS - 0.1 <= t < T.STORY_OUT:
        items = [("Chai", "₹180"), ("Late-night order", "₹349"), ("Forgotten renewal", "₹99"), ("UPI to a friend", "₹250")]
        for j, (lab, amt) in enumerate(items):
            a0 = T.LEAKS + j * 0.4; e = env(t, a0, T.STORY_OUT, 0.3, 0.2) * vis
            if e <= 0.01:
                continue
            y0 = 935 + j * 88 + (1 - q(prog(t, a0, 0.4))) * 24
            c.card(LM, y0, W - 2 * LM, 74, 22, alpha=e)
            c.check(LM + 44, y0 + 37, 16, C["coral200"], e)
            txt(c, LM + 80, y0 + 48, lab, "pjsb", 32, C["ink900"], e)
            txt(c, W - LM - 36, y0 + 49, amt, "pjxb", 36, C["coral400"], e, "r")
        txt(c, W / 2, 1318, "…and many more like these.", "pj", 30, C["grey"], env(t, T.LEAKS + 1.7, T.STORY_OUT, 0.3, 0.2) * vis, "m")
    if t >= 4.2:
        pill(c, W / 2, 1590, "ILLUSTRATIVE EXAMPLE", C["teal800"], C["teal50"], 24, q(prog(t, 4.2, 0.4)) * vis)


def scene_habit(c, t):
    a0, b0 = T.HABIT, T.SCENES[7][1]
    e = env(t, a0, b0, 0.3, 0.25)
    if e <= 0.01:
        return
    a, dy = rise(t, a0 + 0.05); txt(c, LM, 330 + dy, "Not a budget problem.", "pjxb", 68, C["ink900"], a * e)
    a, dy = rise(t, a0 + 0.9); txt(c, LM, 440 + dy, "A habit problem.", "pjxb", 100, C["coral400"], a * e)
    cards = [(a0 + 2.6, "Memory fades", "By month-end, small spends are forgotten.", C["coral200"]),
             (a0 + 4.4, "Tiny habits stick", "A few seconds a day beats a big monthly clean-up.", C["teal200"]),
             (a0 + 5.7, "Patterns appear", "Log daily and your habits show up. Then you decide.", C["amber200"])]
    for j, (ca, title, body, colr) in enumerate(cards):
        ce = env(t, ca, b0, 0.3, 0.25) * e
        if ce <= 0.01:
            continue
        y0 = 540 + j * 172 + (1 - q(prog(t, ca, 0.4))) * 26
        c.card(LM, y0, W - 2 * LM, 150, 30, alpha=ce)
        c.check(LM + 62, y0 + 75, 24, colr, ce)
        txt(c, LM + 112, y0 + 66, title, "pjxb", 40, C["ink900"], ce)
        txt(c, LM + 112, y0 + 112, body, "pj", 28, C["grey"], ce)
    # daily tick strip
    sa = env(t, a0 + 5.0, b0, 0.3, 0.25) * e
    if sa > 0.01:
        n, d, g = 14, 52, 14; x0 = (W - (n * d + (n - 1) * g)) / 2; y0 = 1110
        txt(c, LM, y0 - 24, "14 days. A few seconds each.", "pjsb", 30, C["ink900"], sa)
        for i in range(n):
            fx = q(prog(t, a0 + 5.2 + i * 0.07, 0.25)); xx = x0 + i * (d + g)
            c.ellipse(xx + d / 2, y0 + 26 + d / 2 - 14, d / 2, fill=rgb(C["tile"], sa))
            if fx > 0.01:
                c.check(xx + d / 2, y0 + 26 + d / 2 - 14, d / 2 * (0.6 + 0.4 * fx), C["teal400"], fx * sa)


def scene_app_type(c, t):
    a0, b0 = T.SCENES[8]
    e = env(t, a0, b0, 0.3, 0.25)
    if e <= 0.01:
        return
    a, dy = rise(t, a0 + 0.1); txt(c, LM, 330 + dy, "Type it like", "pjxb", 94, C["ink900"], a * e); txt(c, LM, 430 + dy, "a message.", "pjxb", 94, C["ink900"], a * e)
    subs = [("One line.", a0 + 0.3, T.PARSED + 0.1), ("Filled from your one line.", T.PARSED + 0.2, T.ADD_TAP - 0.5), ("You check it, then add it.", T.ADD_TAP - 0.4, b0)]
    for s, sa, sb in subs:
        txt(c, LM, 500, s, "pjsb", 34, C["coral400"], env(t, sa, sb, 0.2, 0.15) * e)
    # real capture
    if t >= T.PARSED:
        name = "add_parsed"
    elif t >= T.TYPE0:
        name = f"add_t{min(10, int((t - T.TYPE0) / T.TYPE_STEP) + 1):02d}"
    else:
        name = "add_00"
    ca = q(prog(t, a0 + 0.55, 0.4)) * e
    W_CARD = 660; x = (W - W_CARD) / 2; y = 560 + (1 - q(prog(t, a0 + 0.55, 0.4))) * 28
    h = put_app(c, name, x, y, W_CARD, 105, 560, ca)
    txt(c, W / 2, 560 + h + 46, "DEMO ACCOUNT · REAL APP", "pjsb", 22, C["grey"], ca * 0.9, "m", 3)


def scene_app_result(c, t):
    a0, b0 = T.SCENES[9]
    e = env(t, a0, b0, 0.3, 0.25)
    if e <= 0.01:
        return
    one = env(t, a0, T.REMINDER_IN - 0.05, 0.3, 0.2) * e
    two = env(t, T.REMINDER_IN + 0.1, b0, 0.3, 0.25) * e
    a, dy = rise(t, a0 + 0.1)
    txt(c, LM, 330 + dy, "Every rupee", "pjxb", 94, C["ink900"], a * one); txt(c, LM, 430 + dy, "gets a name.", "pjxb", 94, C["ink900"], a * one)
    if one > 0.01:
        y = 560 + (1 - q(prog(t, T.LIST_IN, 0.4))) * 30
        put_app(c, "list_top", (W - 700) / 2, y, 700, 95, 520, one)
    a, dy = rise(t, T.REMINDER_IN + 0.15)
    txt(c, LM, 330 + dy, "Forget a day?", "pjxb", 94, C["ink900"], a * two); txt(c, LM, 430 + dy, "A 9 pm nudge.", "pjxb", 94, C["coral400"], a * two)
    txt(c, LM, 500, "Optional. You choose.", "pjsb", 34, C["coral400"], q(prog(t, T.REMINDER_IN + 0.4, 0.3)) * two)
    if two > 0.01:
        y = 600 + (1 - q(prog(t, T.REMINDER_IN + 0.2, 0.4))) * 30
        put_app(c, "reminder", 80, y, 920, 400, 508, two)
    txt(c, W / 2, 1000 if two > 0.01 and one < 0.01 else 1345, "DEMO ACCOUNT · REAL APP", "pjsb", 22, C["grey"], max(one, two) * 0.9, "m", 3)


def scene_end(c, t):
    if t < T.END_IN:
        return
    t0 = T.END_IN
    a = q(prog(t, t0 + 0.35, 0.4)); b = q(prog(t, t0 + 0.7, 0.4)); d = q(prog(t, t0 + 1.0, 0.4)); f = q(prog(t, t0 + 1.35, 0.4))
    c.logo(W / 2 - 40, 790 - (1 - a) * 18, 80, False)
    txt(c, W / 2, 960, "TrackMyRupee", "pjxb", 92, C["ink900"], a, "m")
    txt(c, W / 2, 1030, "Know where every rupee went.", "pjsb", 36, C["coral400"], b, "m")
    c.card(W / 2 - 320, 1110, 640, 118, 59, fill=C["teal400"], alpha=d)
    txt(c, W / 2, 1186, "Try the free demo", "pjxb", 48, "#FFFFFF", d, "m")
    txt(c, W / 2, 1290, "trackmyrupee.com/demo", "pjsb", 38, C["ink900"], f, "m")
    txt(c, W / 2, 1356, "No bank login  ·  No SMS access", "pj", 30, C["grey"], f, "m")


def frame(n: int) -> bytes:
    t = n / FPS
    c = Canvas(W, H, bg=C["paper"])
    if t < T.END_IN - 0.3:
        c.logo(LM, 110, 46, True, word_color=C["ink900"], word_size=26)
    scene_hook(c, t); scene_story(c, t); scene_habit(c, t); scene_app_type(c, t); scene_app_result(c, t); scene_end(c, t)
    draw_mascot(c, t)
    return c.done().tobytes()


def render(out: Path, preview=None):
    out.parent.mkdir(parents=True, exist_ok=True)
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
    render(Path(args[0]), prev)
