"""Reel 001 — v3 "calm paper" style (modelled on the creative director's reference
animation: warm off-white background, bold rounded-geometric type with coral
accents, white cards with soft shadows, Rupee Minis mascot in the corner, slow
fade-through transitions). 60 fps for smooth motion.

All money figures are illustrative and labelled on screen. The only product UI is a
crop of the real app screenshot (static/img/mobile-2.png, demo data).
Captions are NOT baked in here (see captions.py) so the base video stays reusable.

Usage:  python studio/render/reel_001.py out.mp4 [--preview 0,120,...]   (frame numbers @60fps)
"""
from __future__ import annotations

import math
import subprocess
import sys
from multiprocessing import Pool
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render import mascot  # noqa: E402
from render.brand import C, REPO, Canvas, clamp, ease_back, ease_in_out, ease_out, fmt_inr, prog, rgb  # noqa: E402

W, H, FPS, DUR = 1080, 1920, 60, 35.0
LM = 72  # left margin

GROUPS = [  # key, label, rupees, colour, start
    ("rent", "Rent", 15000, "blue200", 7.0), ("emi", "EMI", 5000, "blue200", 8.2), ("bills", "Bills", 3000, "blue200", 9.2),
    ("food", "Food", 9000, "amber200", 10.6), ("travel", "Travel", 4000, "amber200", 11.8), ("subs", "Subscriptions", 1000, "amber200", 12.8),
    ("sip", "SIP", 5000, "teal200", 14.4),
]
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


# ---- helpers ----------------------------------------------------------------
def env(t, a, b, fi=0.5, fo=0.4):
    return ease_out(prog(t, a, fi)) * (1 - ease_in_out(prog(t, b - fo, fo)))


def rise(t, a, dur=0.6, dist=28):
    p = ease_out(prog(t, a, dur)); return p, (1 - p) * dist


def lerp(a, b, k):
    return a + (b - a) * k


def mix(a, b, k):
    ra, rb = rgb(a), rgb(b); return "#%02x%02x%02x" % tuple(int(ra[i] + (rb[i] - ra[i]) * k) for i in range(3))


def txt(c, x, y, s, key, size, fill, a=1.0, anchor="l", tr=0):
    if a <= 0.01:
        return
    col = rgb(fill) if isinstance(fill, str) else fill
    c.text(x, y, s, key, size, col[:3] + (int(255 * clamp(a)),), anchor, tr)


def pill(c, cx, cy, s, fg, bg, size=26, a=1.0):
    w = c.text_width(s, "pjsb", size, 2) + 56
    c.rrect(cx - w / 2, cy - 30, w, 60, 30, fill=rgb(bg, a)); txt(c, cx, cy + size * .36, s, "pjsb", size, fg, a, "m", 2)


_ui = None


def ui_crop():
    global _ui
    if _ui is None:
        im = Image.open(REPO / "static/img/mobile-2.png").convert("RGBA").crop((150, 1545, 1270, 2125))
        w = 880; h = int(im.height * w / im.width); im = im.resize((w * 2, h * 2), Image.LANCZOS)
        from PIL import ImageDraw
        m = Image.new("L", im.size, 0); ImageDraw.Draw(m).rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=56, fill=255)
        im.putalpha(Image.composite(im.getchannel("A"), Image.new("L", im.size, 0), m)); _ui = im, w, h
    return _ui


def spent(t):
    return sum(g[2] * ease_in_out(prog(t, g[4] + 0.3, 0.7)) for g in GROUPS)


# ---- mascot choreography -----------------------------------------------------
def mascot_pose(t):
    if t < 3.4:
        cx, yb, sz = 905, 590, 175
    elif t < 26.6:
        k = ease_in_out(prog(t, 3.4, 0.9)); cx, yb, sz = lerp(905, 925, k), lerp(590, 335, k), lerp(175, 150, k)
    elif t < 31.4:
        k = ease_in_out(prog(t, 26.6, 0.8)); cx, yb, sz = lerp(925, 905, k), lerp(335, 600, k), lerp(150, 170, k)
    else:
        k = ease_in_out(prog(t, 31.4, 0.9)); cx, yb, sz = lerp(905, 540, k), lerp(600, 690, k), lerp(170, 290, k)
    return cx, yb, sz


def draw_mascot(c, t):
    cx, yb, sz = mascot_pose(t)
    mood, rot, squash, coin_y, front = "happy", 0.0, 0.0, None, True
    if t < 0.7:
        sz *= max(0.01, ease_back(prog(t, 0.15, 0.55))); squash = 0.08 * (1 - prog(t, 0.55, 0.3))
    if t < 3.9:                                     # salary coin goes in, then pops back
        if 0.6 <= t < 1.1:
            coin_y = 32 + 30 * ease_in_out(prog(t, 0.6, 0.5)); front = coin_y < 40
        elif 1.1 <= t < 3.6:
            coin_y, front = 70, False
        elif t >= 3.6:
            coin_y = 60 - 28 * ease_out(prog(t, 3.6, 0.6)); front = coin_y < 42
        if t >= 1.15:
            mood = "wide"
        if 1.2 <= t < 2.0:
            rot = math.sin((t - 1.2) * 14) * 4 * (1 - prog(t, 1.2, 0.8))
    if 4.6 <= t < 17.4:
        mood = "happy"
    if 17.4 <= t < 26.6:
        mood = "wide"
        if t >= 22.0:
            rot = math.sin(t * 3.2) * 5
    if 29.2 <= t < 30.4:                            # logged → coin into slot
        if t < 29.6:
            coin_y = 32 + 30 * ease_in_out(prog(t, 29.2, 0.4)); front = coin_y < 40
        else:
            coin_y = 62 - 30 * ease_out(prog(t, 29.6, 0.7)); front = coin_y < 42
        squash = 0.07 * math.sin(math.pi * prog(t, 29.5, 0.4))
    if 31.9 <= t < 32.5:
        squash = 0.08 * math.sin(math.pi * prog(t, 31.9, 0.6))
    mascot.draw(c, cx, yb, sz, t=t, mood=mood, rot=rot, squash=squash, coin_y=coin_y, coin_front=front, pal=mascot.LIGHT, wiggle=0.8)
    if 17.9 <= t < 22.0 and prog(t, 17.9, 0.4) > 0.1:        # thought bubble in the reveal
        a = ease_back(prog(t, 17.9, 0.4)) * (1 - prog(t, 21.6, 0.4))
        bx, by = cx - 105, yb - 150
        c.ellipse(bx, by, 30 * a, fill=rgb(C["coral200"])); txt(c, bx, by + 14 * a, "?", "pjxb", int(40 * a) + 1, C["coral400"], 1, "m")


# ---- scenes -----------------------------------------------------------------
def scene_hook(c, t):
    if t >= 3.6:
        return
    out = 1 - ease_in_out(prog(t, 3.1, 0.45))
    for s, y, col, a0 in (("₹50,000 in.", 330, C["ink900"], 0.15), ("Gone by the 20th.", 440, C["coral400"], 1.45), ("Where?", 550, C["ink900"], 2.45)):
        a, dy = rise(t, a0); txt(c, LM, y + dy, s, "pjxb", 94, col, a * out)
    a, dy = rise(t, 0.35, 0.7, 36); a *= out
    y0 = 720 + dy
    c.card(LM, y0, W - 2 * LM, 132, 30, alpha=a)
    c.check(LM + 62, y0 + 66, 24, C["amber200"], a)
    drain = ease_in_out(prog(t, 1.0, 1.0))
    sw = ease_in_out(prog(t, 1.9, 0.5))
    txt(c, LM + 112, y0 + 82, "Salary credited", "pjsb", 40, C["ink900"], a * (1 - sw))
    txt(c, LM + 112, y0 + 82, "Left on the 20th", "pjsb", 40, C["ink900"], a * sw)
    val = int(round(SALARY * (1 - drain), -2))
    txt(c, W - LM - 40, y0 + 84, fmt_inr(val), "pjxb", 46, mix(C["ink900"], C["coral400"], drain), a, "r")


def grid_visible(t):
    return env(t, 3.7, 26.5, 0.6, 0.5)


def draw_grid(c, t, vis):
    for i in range(COLS * ROWS):
        r, k = divmod(i, COLS); x, y = GX + k * (TILE + GAP), GY + r * (TILE + GAP)
        a = ease_out(prog(t, 4.0 + i * 0.02, 0.5)) * vis
        if a <= 0.01:
            continue
        sz = TILE * (0.9 + 0.1 * a); ox, oy = x + (TILE - sz) / 2, y + (TILE - sz) / 2
        g = _tg[i]; col = C["tile"]
        if g:
            col = mix(C["tile"], C[g[3]], ease_in_out(prog(t, g[4] + (i - _first[g[0]]) * 0.05, 0.45)))
            c.rrect(ox, oy, sz, sz, 16, fill=rgb(col, a))
        else:
            m = ease_in_out(prog(t, 17.6, 0.9)); pulse = 0.5 + 0.5 * math.sin((t - 17.6) * 2.6)
            col = mix(C["tile"], C["coral200"], m * (0.8 + 0.2 * pulse))
            c.rrect(ox, oy, sz, sz, 16, fill=rgb(col, a))
            if m > 0.5:
                txt(c, ox + sz / 2, oy + sz / 2 + 17, "?", "pjxb", 52, C["coral400"], a * (m - .5) * 2, "m")


def scene_story(c, t):
    vis = grid_visible(t)
    if vis <= 0.01:
        return
    # header
    a, dy = rise(t, 3.8); a *= vis
    txt(c, LM, 305 + dy, "One ₹50,000 salary", "pjxb", 58, C["ink900"], a)
    sub = "How much can you name?" if t < 17.4 else "Who took the rest?"
    swap = ease_in_out(prog(t, 17.0, 0.5))
    txt(c, LM, 360 + dy, "How much can you name?", "pjsb", 30, C["coral400"], a * (1 - swap))
    txt(c, LM, 360 + dy, "Who took the rest?", "pjsb", 30, C["coral400"], a * swap)
    draw_grid(c, t, vis)

    # group card (swaps per group)
    for g in GROUPS:
        a0, a1 = g[4] + 0.15, g[4] + (1.1 if g is not GROUPS[-1] else 2.4)
        e = env(t, a0, a1, 0.45, 0.4) * vis
        if e <= 0.01:
            continue
        dy = (1 - ease_out(prog(t, a0, 0.5))) * 24
        y0 = 935 + dy
        c.card(LM, y0, W - 2 * LM, 104, 28, alpha=e)
        c.check(LM + 58, y0 + 52, 22, C[g[3]], e)
        txt(c, LM + 104, y0 + 66, g[1], "pjxb", 38, C["ink900"], e)
        txt(c, W - LM - 40, y0 + 68, fmt_inr(g[2]), "pjxb", 42, C["coral400"], e, "r")

    # counter
    if t < 22.0:
        ca = ease_out(prog(t, 5.2, 0.6)) * vis * (1 - ease_in_out(prog(t, 21.5, 0.5)))
        rem = SALARY - spent(t)
        col = mix(C["ink900"], C["coral400"], ease_in_out(prog(t, 17.4, 0.8)))
        sc = 1 + 0.025 * math.sin((t - 17.6) * 2.6) if t > 17.6 else 1
        txt(c, W / 2, 1210, fmt_inr(int(round(rem, -1))), "pjxb", int(132 * sc), col, ca, "m")
        lab = "left to name" if t < 17.4 else "no name · no category"
        swap = ease_in_out(prog(t, 17.2, 0.5))
        txt(c, W / 2, 1264, "left to name", "pjsb", 32, C["grey"], ca * (1 - swap), "m")
        txt(c, W / 2, 1264, "no name · no category", "pjsb", 32, C["grey"], ca * swap, "m")
        if t >= 19.2:
            txt(c, W / 2, 1316, "16% of your salary", "pjxb", 34, C["coral400"], ease_out(prog(t, 19.2, 0.6)) * ca, "m")

    # leaks: stack of cards (like paid-UPI list)
    if 22.0 <= t < 26.6:
        items = [("Chai", "₹180"), ("Late-night order", "₹349"), ("Forgotten renewal", "₹99"), ("UPI to a friend", "₹250")]
        for j, (lab, amt) in enumerate(items):
            a0 = 22.2 + j * 0.45; e = env(t, a0, 26.5, 0.55, 0.5) * vis
            if e <= 0.01:
                continue
            y0 = 935 + j * 88 + (1 - ease_out(prog(t, a0, 0.6))) * 30
            c.card(LM, y0, W - 2 * LM, 74, 22, alpha=e)
            c.check(LM + 44, y0 + 37, 16, C["coral200"], e)
            txt(c, LM + 80, y0 + 48, lab, "pjsb", 32, C["ink900"], e)
            txt(c, W - LM - 36, y0 + 49, amt, "pjxb", 36, C["coral400"], e, "r")
        txt(c, W / 2, 1318, "…and many more like these.", "pj", 30, C["grey"], env(t, 24.2, 26.5, 0.6, 0.5) * vis, "m")
    # label
    if t >= 4.6:
        pill(c, W / 2, 1590, "ILLUSTRATIVE EXAMPLE", C["teal800"], C["teal50"], 24, ease_out(prog(t, 4.6, 0.6)) * vis)


def scene_solution(c, t):
    e = env(t, 26.9, 31.5, 0.6, 0.5)
    if e <= 0.01:
        return
    old = 1 - ease_in_out(prog(t, 29.6, 0.45)); new = ease_out(prog(t, 30.15, 0.6))
    a, dy = rise(t, 27.0)
    txt(c, LM, 330 + dy, "Type it like", "pjxb", 94, C["ink900"], a * e * old); txt(c, LM, 430 + dy, "a message.", "pjxb", 94, C["ink900"], a * e * old)
    txt(c, LM, 500 + dy, "One line.", "pjsb", 34, C["coral400"], a * e * old)
    txt(c, LM, 330 + (1 - new) * 24, "Every rupee", "pjxb", 94, C["ink900"], e * new); txt(c, LM, 430 + (1 - new) * 24, "gets a name.", "pjxb", 94, C["ink900"], e * new)
    ia = ease_out(prog(t, 27.5, 0.6)) * e * old; y0 = 660 + (1 - ease_out(prog(t, 27.5, 0.6))) * 30
    if ia > 0.01:
        c.card(LM, y0, W - 2 * LM, 104, 36, fill=C["input"], alpha=ia)
        s = "swiggy 320"; n = int(len(s) * ease_in_out(prog(t, 27.9, 1.0)))
        if n == 0:
            txt(c, LM + 36, y0 + 66, "kya kharcha hua?", "mono", 34, C["grey"], ia)
        else:
            txt(c, LM + 36, y0 + 66, s[:n] + ("|" if int(t * 2.4) % 2 == 0 and n < len(s) else ""), "mono", 36, C["ink900"], ia)
        ca = ease_out(prog(t, 29.0, 0.5)) * e * old
        if ca > 0.01:
            txt(c, LM, y0 + 170, "It picks out:", "pjsb", 28, C["grey"], ca)
            x = LM
            for lab in ("₹320", "Swiggy", "category suggested"):
                w = c.text_width(lab, "pjsb", 30) + 44
                c.rrect(x, y0 + 190, w, 58, 29, fill=rgb(C["amber50"], ca)); txt(c, x + 22, y0 + 228, lab, "pjsb", 30, C["amber400"], ca); x += w + 14
            txt(c, LM, y0 + 310, "You check it, then add it.", "pj", 30, C["grey"], ca)
    ua = new * e
    if ua > 0.01:
        im, w, h = ui_crop(); y0 = 600 + (1 - new) * 40
        c.card(LM + 40, y0, w, h, 28, alpha=ua)
        im2 = im.copy(); im2.putalpha(im2.getchannel("A").point(lambda v: int(v * ua)))
        c.img.paste(im2, (c._s(LM + 40), c._s(y0)), im2)
        txt(c, W / 2, y0 + h + 52, "DEMO DATA · FROM THE REAL APP", "pjsb", 22, C["grey"], ua, "m", 3)


def scene_end(c, t):
    if t < 31.4:
        return
    a = ease_out(prog(t, 31.7, 0.7)); b = ease_out(prog(t, 32.2, 0.6)); d = ease_out(prog(t, 32.8, 0.6)); f = ease_out(prog(t, 33.4, 0.6))
    c.logo(W / 2 - 40, 790 - (1 - a) * 20, 80, False)
    if a > 0.01:
        c.img.paste(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), (0, 0))
    txt(c, W / 2, 960, "TrackMyRupee", "pjxb", 92, C["ink900"], a, "m")
    txt(c, W / 2, 1030, "Know where every rupee went.", "pjsb", 36, C["coral400"], b, "m")
    c.card(W / 2 - 320, 1110, 640, 118, 59, fill=C["teal400"], alpha=d)
    txt(c, W / 2, 1186, "Try the free demo", "pjxb", 48, "#FFFFFF", d, "m")
    txt(c, W / 2, 1290, "trackmyrupee.com/demo", "pjsb", 38, C["ink900"], f, "m")
    txt(c, W / 2, 1356, "No bank login  ·  No SMS access", "pj", 30, C["grey"], f, "m")


def frame(n: int) -> bytes:
    t = n / FPS
    c = Canvas(W, H, bg=C["paper"])
    if t < 31.0:
        c.logo(LM, 110, 46, True, word_color=C["ink900"], word_size=26)
    scene_hook(c, t); scene_story(c, t); scene_solution(c, t); scene_end(c, t)
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
