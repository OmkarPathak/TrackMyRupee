"""Reel 001 — "Where does your ₹50,000 salary disappear?"

Motion-graphics renderer. Every number is illustrative and labelled as such on
screen. The only product UI shown is the real app screenshot from
static/img/mobile-2.png (never a generated or faked interface).

Usage:  python studio/render/reel_001.py [out.mp4] [--preview N]
"""
from __future__ import annotations

import subprocess
import sys
from multiprocessing import Pool
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render.brand import (C, REPO, Canvas, clamp, ease_back, ease_in_out, ease_out,  # noqa: E402
                          fmt_inr, prog, rgb)

W, H, FPS = 1080, 1920, 30
DUR = 35.0

# --- the illustrative salary (sums to exactly 50,000; asserted below) -------
GROUPS = [  # key, label, rupees, colour, start-time
    ("rent", "Rent", 15000, "blue200", 7.0),
    ("emi", "EMI", 5000, "blue200", 8.1),
    ("bills", "Bills", 3000, "blue200", 9.0),
    ("food", "Food", 9000, "amber200", 10.6),
    ("travel", "Travel", 4000, "amber200", 11.7),
    ("subs", "Subscriptions", 1000, "amber200", 12.6),
    ("sip", "SIP", 5000, "teal200", 14.4),
]
MYSTERY = 8000
SALARY = 50000
assert sum(g[2] for g in GROUPS) + MYSTERY == SALARY

# tile grid: 10 x 5, one tile = ₹1,000
COLS, ROWS, TILE, GAP = 10, 5, 80, 12
GX = (W - (COLS * TILE + (COLS - 1) * GAP)) // 2
GY = 700

_tile_group = []
for g in GROUPS:
    _tile_group += [g] * (g[2] // 1000)
_tile_group += [None] * (MYSTERY // 1000)

_phone = None


def phone():
    global _phone
    if _phone is None:
        im = Image.open(REPO / "static/img/mobile-2.png").convert("RGBA")
        h = 900
        im = im.resize((int(im.width * h / im.height), h), Image.LANCZOS)
        # render at canvas scale (2x) so it stays crisp
        _phone = im.resize((im.width * 2, im.height * 2), Image.LANCZOS), im.width, im.height
    return _phone


def tile_xy(i):
    r, c = divmod(i, COLS)
    return GX + c * (TILE + GAP), GY + r * (TILE + GAP)


def remaining(t):
    spent = sum(g[2] for g in GROUPS if t >= g[4] + 0.35)
    return SALARY - spent


def center(c, y, text, key, size, fill, alpha=1.0, tracking=0):
    if alpha <= 0:
        return
    col = rgb(fill) if isinstance(fill, str) else fill
    c.text(W / 2, y, text, key, size, col[:3] + (int(255 * alpha),), "m", tracking)


def pill(c, cx, cy, text, fg, bg, size=30, alpha=1.0, dot=None):
    tw = c.text_width(text, "sansb", size, 2)
    pad = 28 + (26 if dot else 0)
    w = tw + pad * 2
    c.rrect(cx - w / 2, cy - 36, w, 72, 36, fill=rgb(bg, 0.85 * alpha))
    x = cx - w / 2 + pad
    if dot:
        c.ellipse(x - 4, cy, 8, fill=rgb(dot, alpha)); x += 22
    c.text(x, cy + size * 0.36, text, "sansb", size, rgb(fg, alpha), "l", 2)


def draw_grid(c, t, scene):
    """scene controls tile appearance; returns nothing."""
    appear0 = 3.5
    for i in range(COLS * ROWS):
        x, y = tile_xy(i)
        a = ease_back(prog(t, appear0 + i * 0.022, 0.45))
        if a <= 0:
            continue
        sz = TILE * clamp(a, 0, 1.12)
        ox, oy = x + (TILE - sz) / 2, y + (TILE - sz) / 2
        g = _tile_group[i]
        col, alpha = C["cream"], 0.92
        if g:
            k = prog(t, g[4] + (i - _first_index(g)) * 0.045, 0.3)
            if k > 0:
                mix = ease_out(k)
                col = _mix(C["cream"], C[g[3]], mix)
        else:
            # mystery tiles: stay blank cream, then turn coral-outlined + pulse
            m = prog(t, 17.6, 0.6)
            if m > 0:
                pulse = 0.5 + 0.5 * __import__("math").sin((t - 17.6) * 5.2)
                col = _mix(C["cream"], C["coral200"], ease_out(m) * (0.55 + 0.45 * pulse))
                c.glow(ox, oy, sz, sz, 14, C["coral200"], blur=18, alpha=0.5 * pulse * m)
        c.rrect(ox, oy, sz, sz, 14, fill=rgb(col, alpha))
        if g is None and t >= 17.8 and a >= 1:
            c.text(ox + sz / 2, oy + sz / 2 + 16, "?", "serif", 52, rgb(C["coral400"]), "m")


_first = {}


def _first_index(g):
    if not _first:
        for i, gg in enumerate(_tile_group):
            if gg and gg[0] not in _first:
                _first[gg[0]] = i
    return _first[g[0]]


def _mix(a, b, k):
    ra, rb_ = rgb(a), rgb(b)
    return "#%02x%02x%02x" % tuple(int(ra[i] + (rb_[i] - ra[i]) * k) for i in range(3))


def frame(n: int) -> bytes:
    t = n / FPS
    c = Canvas(W, H)
    # slow background drift so the frame never feels static
    drift = int(40 * (t / DUR))
    c.glow(-200, 300 + drift, 700, 700, 350, C["teal800"], blur=120, alpha=0.35)

    # persistent brand bug (top-left, inside safe area)
    if t < 31.0:
        c.logo(64, 150, 54, True, word_size=30)

    # ---------------- S1 hook 0 – 3.4 ----------------------------------
    if t < 3.9:
        fade = 1 - prog(t, 3.3, 0.5)
        ys = 760 - 40 * ease_in_out(prog(t, 3.0, 0.8))
        pa = ease_out(prog(t, 0.2, 0.4)) * fade
        pill(c, W / 2, 560 + 30 * (1 - ease_out(prog(t, 0.2, 0.4))), "SALARY CREDITED", C["teal900"], C["teal200"], 30, pa, dot=C["teal900"])
        val = int(50000 * ease_out(prog(t, 0.55, 0.9)))
        center(c, ys + 90, fmt_inr(val), "serif", 250, C["cream"], fade)
        center(c, 1010, "By the 20th…", "sansm", 62, C["cream"], ease_out(prog(t, 1.9, 0.5)) * fade)
        center(c, 1110, "where did it go?", "serif", 118, C["teal200"], ease_out(prog(t, 2.6, 0.5)) * fade)

    # ---------------- S2..S7 tile story 3.5 – 26.2 ----------------------
    if 3.4 <= t < 26.6:
        out = 1 - prog(t, 25.9, 0.7)
        zoom_in = ease_in_out(prog(t, 17.4, 1.2)) * (1 - ease_in_out(prog(t, 22.0, 1.0)))
        # header: salary → label of the active group
        ha = ease_out(prog(t, 3.9, 0.5)) * out
        center(c, 470, "₹50,000 salary", "sansb", 40, C["teal200"], ha, 1)
        active = None
        for g in GROUPS:
            if g[4] <= t < g[4] + 1.05 or (g is GROUPS[-1] and g[4] <= t < g[4] + 2.2):
                active = g
        if t < 7.0:
            center(c, 590, "Let's follow one salary.", "serif", 92, C["cream"], ease_out(prog(t, 4.2, 0.5)) * (1 - prog(t, 6.7, 0.3)) * out)
        elif active:
            k = ease_out(prog(t, active[4], 0.35))
            center(c, 590, f"{active[1]}  {fmt_inr(active[2])}", "serif", 96, C["cream"], k * out)
        elif 17.4 <= t < 22.1:
            center(c, 590, "…and then, this.", "serif", 96, C["cream"], ease_out(prog(t, 17.4, 0.5)) * out)
        elif 22.1 <= t < 26.6:
            center(c, 590, "It's never one big thing.", "serif", 92, C["cream"], ease_out(prog(t, 22.2, 0.5)) * out)

        # group captions under the active tiles
        sub = {"rent": "Fixed. You saw this coming.", "emi": "", "bills": "",
               "food": "Life happens.", "travel": "", "subs": "", "sip": "Future you says thanks."}
        if active and sub.get(active[0]):
            center(c, 660, sub[active[0]], "sansm", 36, C["cream"], 0.75 * ease_out(prog(t, active[4] + 0.1, 0.4)) * out)

        # grid (with a gentle push-in during the reveal)
        if zoom_in > 0:
            c2 = c  # draw grid then scale region? keep simple: shift grid slightly
        draw_grid(c, t, None)

        # remaining counter
        rem = remaining(t)
        cy = GY + ROWS * (TILE + GAP) + 110
        ra = ease_out(prog(t, 5.2, 0.5)) * out
        if t < 17.4:
            center(c, cy, "NOT YET NAMED", "sansb", 30, C["cream"], 0.6 * ra, 4)
            center(c, cy + 120, fmt_inr(rem), "serif", 130, C["cream"], ra)
        elif t < 22.0:
            center(c, cy, "NO NAME · NO CATEGORY", "sansb", 30, C["coral200"], 0.95 * ra, 4)
            sc = 1 + 0.04 * (0.5 + 0.5 * __import__("math").sin((t - 17.6) * 5.2))
            center(c, cy + 120, fmt_inr(MYSTERY), "serif", int(130 * sc), C["coral200"], ra)
            if t >= 19.4:
                center(c, cy + 200, "16% of your salary", "sansb", 42, C["cream"], ease_out(prog(t, 19.4, 0.5)) * out)

        # illustrative tag
        if t >= 4.6:
            pill(c, W / 2, 1650, "ILLUSTRATIVE EXAMPLE", C["cream"], C["teal800"], 26, ease_out(prog(t, 4.6, 0.5)) * out * 0.9)

        # S7 leak chips
        if 22.2 <= t < 26.6:
            chips = [("₹180 chai", 0.0), ("₹349 late-night order", 0.45),
                     ("₹99 forgotten renewal", 0.9), ("₹250 UPI to a friend", 1.35)]
            pos = [(300, 1310), (770, 1310), (300, 1425), (770, 1425)]
            for j, (txt, dl) in enumerate(chips):
                k = ease_out(prog(t, 22.3 + dl, 0.55))
                if k <= 0:
                    continue
                cxx, cyy = pos[j][0], pos[j][1] + (1 - k) * 40
                tw = c.text_width(txt, "sansm", 34)
                c.rrect(cxx - tw / 2 - 28, cyy - 38, tw + 56, 76, 38, fill=rgb(C["coral50"], 0.95 * k * out))
                c.text(cxx, cyy + 12, txt, "sansm", 34, rgb(C["coral400"], k * out), "m")
            center(c, 1540, "…and many more like these.", "sansm", 34, C["cream"], 0.7 * ease_out(prog(t, 24.2, 0.5)) * out)

    # ---------------- S8 solution 26 – 31.2 ----------------------------
    if 26.6 <= t < 31.6:
        out = 1 - prog(t, 30.9, 0.6)
        k = ease_out(prog(t, 26.8, 0.9))
        center(c, 330, "Type it like a message.", "serif", 104, C["cream"], ease_out(prog(t, 27.0, 0.5)) * out)
        # typed chip (typographic quote of a real, documented input example)
        txt = "swiggy 320"
        n_ch = int(len(txt) * prog(t, 27.8, 0.8))
        shown = txt[:n_ch] + ("▍" if int(t * 3) % 2 == 0 and n_ch < len(txt) else "")
        shown = txt[:n_ch]
        c.rrect(W / 2 - 250, 390, 500, 96, 48, fill=rgb(C["cream"], 0.1 * out), outline=rgb(C["teal200"], 0.8 * out), width=2)
        c.text(W / 2 - 200, 455, "“" + shown + ("”" if n_ch == len(txt) else ""), "sansm", 50, rgb(C["teal200"], out))
        center(c, 570, "Every rupee gets a name.", "sansm", 44, C["cream"], 0.85 * ease_out(prog(t, 28.5, 0.5)) * out)
        im, w1, h1 = phone()
        y = 620 + (1 - k) * 900
        c.paste(im, W / 2 - w1 / 2, y, alpha=out)
        if k > 0.9:
            center(c, 1575, "DEMO DATA · FROM THE REAL APP", "sansb", 24, C["cream"], 0.55 * out, 3)

    # ---------------- S9 end card 31 – 35 ------------------------------
    if t >= 31.0:
        a = ease_out(prog(t, 31.2, 0.6))
        c.logo(W / 2 - 88, 560 - 20 * (1 - a), 176)
        center(c, 880, "TrackMyRupee", "serif", 130, C["cream"], a)
        center(c, 980, "Know where every rupee went.", "sansm", 44, C["teal200"], ease_out(prog(t, 31.7, 0.5)))
        k = ease_out(prog(t, 32.3, 0.6))
        c.rrect(W / 2 - 330, 1100, 660, 120, 60, fill=rgb(C["teal400"], k))
        center(c, 1178, "Try the free demo", "sansb", 52, C["white"], k)
        center(c, 1290, "trackmyrupee.com/demo", "sansm", 40, C["cream"], 0.9 * ease_out(prog(t, 32.8, 0.5)))
        center(c, 1380, "No bank login  ·  No SMS access", "sansm", 32, C["cream"], 0.6 * ease_out(prog(t, 33.3, 0.5)), 1)

    return c.done().tobytes()


def render(out: Path, preview=None):
    out.parent.mkdir(parents=True, exist_ok=True)
    total = int(DUR * FPS)
    frames = preview if preview else range(total)
    if preview:
        for n in preview:
            Image.frombytes("RGB", (W, H), frame(n)).save(out.parent / f"preview_{n:04d}.png")
        return
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with Pool(4) as pool:
        for i, buf in enumerate(pool.imap(frame, frames, chunksize=6)):
            p.stdin.write(buf)
            if i % 150 == 0:
                print(f"  frame {i}/{total}", flush=True)
    p.stdin.close(); p.wait()


if __name__ == "__main__":
    args = sys.argv[1:]
    prev = None
    if "--preview" in args:
        i = args.index("--preview"); prev = [int(x) for x in args[i + 1].split(",")]; args = args[:i]
    render(Path(args[0] if args else "video_silent.mp4"), prev)
