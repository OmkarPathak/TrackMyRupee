"""Reel 002 — why seeing your net worth matters. Calm-paper style; Rupee Minis narrates
(Line mouth, audio-driven). Reuses helpers from reel_001. Illustrative example labelled; app UI
only from real captures (assets/app_captures). Captions are added separately (captions.py)."""
from __future__ import annotations

import json
import math
import subprocess
import sys
from multiprocessing import Pool
from pathlib import Path

from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render import mascot, timeline_002 as T  # noqa: E402
from render.brand import C, Canvas, clamp, fmt_inr, prog, rgb  # noqa: E402
from render.reel_001 import env, io, lerp, mix, pill, put_app, q, rise, txt  # noqa: E402

W, H, FPS, DUR = 1080, 1920, 60, T.DUR
LM = 72
S = T.SCENES
CUES_PATH = Path(__file__).resolve().parents[1] / "assets" / "vo" / "reel_002" / "cues.json"
_cues = None


def cue(n):
    global _cues
    if _cues is None:
        _cues = json.loads(CUES_PATH.read_text()) if CUES_PATH.exists() else []
    return _cues[n] if n < len(_cues) else {"shape": "X", "open": 0.0, "energy": 0.0}


NW = {"assets": [("Savings account", 120000), ("Fixed deposit", 200000), ("Mutual funds", 350000), ("EPF", 280000), ("Gold", 100000)],
      "liabs": [("Car loan", 300000), ("Credit card due", 40000)]}
OWN, OWE = sum(a for _, a in NW["assets"]), sum(a for _, a in NW["liabs"])
assert OWN - OWE == 710000


# ---- narrator ---------------------------------------------------------------------
def draw_mascot(c, t, n):
    corner, end = (925, 345, 190), (540, 700, 300)
    if t < S[7][0]:
        cx, yb, sz = corner
    else:
        k = io(prog(t, S[7][0], 0.6)); cx, yb, sz = (lerp(a, b, k) for a, b in zip(corner, end))
    cu = cue(n); en = cu["energy"]
    if t < 0.5:
        sz *= max(0.01, min(1.06, q(prog(t, 0.05, 0.4)) * 1.03))
    mood = "talk"
    if T.beat(0, "Why?") - 0.1 <= t < S[0][1] or S[4][0] <= t < S[5][0]:
        mood = "wide"
    if t >= S[7][0] + 0.3:
        mood = "happy" if cu["shape"] == "X" else "talk"
    rot = math.sin(t * 2.1) * 1.6 + en * 2.2 * math.sin(t * 9)
    if T.beat(0, "Why?") <= t < S[0][1]:
        rot = math.sin((t - T.beat(0, "Why?")) * 7) * 5                # shrug
    blink = 1.0 if (t % 3.3) < 0.12 else 0.0
    squash = 0.05 * en
    mascot.draw(c, cx, yb, sz, t=t, mood=mood, rot=rot, squash=squash, pal=mascot.LIGHT, wiggle=0.6,
                mouth=(cu["shape"], cu["open"]), mstyle=mascot.NARRATOR_MSTYLE, blink=blink)


# ---- scenes ----------------------------------------------------------------------------
def hdr(c, t, a0, line1, line2=None, size=80, col2="coral400", e=1.0, y1=330, y2=430):
    a, dy = rise(t, a0)
    txt(c, LM, y1 + dy, line1, "pjxb", size, C["ink900"], a * e)
    if line2:
        a, dy = rise(t, a0 + 0.35); txt(c, LM, y2 + dy, line2, "pjxb", size, C[col2], a * e)


def scene1(c, t):
    """HOOK A: same salary, very different net worth (illustrative)."""
    a0, b0 = S[0]; e = env(t, a0, b0, 0.15, 0.25)
    ts, tf, tw = T.beat(0, "Same salary") - 0.05, T.beat(0, "Five years", 0.1), T.beat(0, "Why?", 0.1)
    a, dy = rise(t, ts); txt(c, LM, 330 + dy, "Same salary.", "pjxb", 84, C["ink900"], a * e)
    a, dy = rise(t, tf); txt(c, LM, 430 + dy, "5 years later.", "pjxb", 84, C["coral400"], a * e)
    a, dy = rise(t, tw, 0.3); txt(c, LM, 540 + dy, "Why?", "pjxb", int(110 * (1 + .2 * (1 - q(prog(t, tw + .1, .3))))), C["ink900"], a * e)
    for k, nm in enumerate(("Riya", "Aman")):
        x0 = LM if k == 0 else W / 2 + 14
        ca = q(prog(t, ts + 0.25 + k * 0.15, 0.4)) * e
        if ca > 0.01:
            y0 = 640 + (1 - q(prog(t, ts + 0.25 + k * 0.15, 0.4))) * 24
            c.card(x0, y0, W / 2 - LM - 14, 132, 28, alpha=ca)
            c.ellipse(x0 + 56, y0 + 66, 34, fill=rgb(C["teal200"] if k == 0 else C["blue200"], ca)); txt(c, x0 + 56, y0 + 80, nm[0], "pjxb", 38, "#FFFFFF", ca, "m")
            txt(c, x0 + 108, y0 + 62, nm, "pjxb", 34, C["ink900"], ca); txt(c, x0 + 108, y0 + 104, "₹80,000/month", "pjsb", 26, C["grey"], ca)
    base, sc = 1290, 380 / 1400000
    for k, (val, x0, col, lab) in enumerate(((1400000, 200, C["teal400"], "Riya"), (400000, 600, C["coral200"], "Aman"))):
        g = q(prog(t, tf + 0.3 + k * 0.15, 1.0)) * e
        if g > 0.01:
            h = val * sc * q(prog(t, tf + 0.3 + k * 0.15, 1.0))
            c.rrect(x0, base - h, 280, h, 28, fill=rgb(col, e)); txt(c, x0 + 140, base + 50, lab, "pjxb", 32, C["ink900"], g, "m")
            if prog(t, tf + 1.1, 0.3) > 0:
                txt(c, x0 + 140, base - h - 18, fmt_inr(val), "pjxb", 44, C["ink900"], q(prog(t, tf + 1.1, 0.3)) * e, "m")
    gp = q(prog(t, T.beat(0, "ten lakh") - 0.1, 0.4)) * e
    if gp > 0.01:
        pill(c, W / 2, 814, "₹10,00,000 gap", "#FFFFFF", C["coral400"], 30, gp)
    pill(c, W / 2, 1600, "ILLUSTRATIVE EXAMPLE", C["teal800"], C["teal50"], 24, q(prog(t, ts + 0.3, 0.4)) * e)


def scene2(c, t):
    a0, b0 = S[1]; e = env(t, a0, b0, 0.25, 0.25)
    a, dy = rise(t, T.beat(1, "Because") - 0.05); txt(c, LM, 330 + dy, "Salary isn't the score.", "pjxb", 62, C["ink900"], a * e)
    a, dy = rise(t, T.beat(1, "Net worth is") - 0.05); txt(c, LM, 430 + dy, "Net worth is.", "pjxb", 90, C["coral400"], a * e)
    base, bw = 1190, 250
    spec = [(90, OWN, "What you own", C["teal200"], T.beat(1, "everything you own") - 0.15),
            (415, OWE, "What you owe", C["coral200"], T.beat(1, "minus everything") - 0.1),
            (740, OWN - OWE, "Net worth", C["teal400"], T.beat(1, "One number") - 0.15)]
    scale = 430 / OWN
    for i, (x, val, lab, col, tb) in enumerate(spec):
        g = q(prog(t, tb, 0.55)) * e
        if g <= 0.01:
            continue
        h = val * scale * q(prog(t, tb, 0.55))
        c.rrect(x, base - h, bw, h, 26, fill=rgb(col, e))
        txt(c, x + bw / 2, base + 52, lab, "pjxb", 32, C["ink900"], g, "m")
        if g > 0.6:
            txt(c, x + bw / 2, base - h / 2 + 14, "?" if False else "", "pjxb", 40, "#FFFFFF", g, "m")
    txt(c, 372, base - 100, "−", "pjxb", 130, C["ink900"], q(prog(t, spec[1][4], 0.3)) * e, "m")
    txt(c, 697, base - 100, "=", "pjxb", 130, C["ink900"], q(prog(t, spec[2][4] - 0.05, 0.3)) * e, "m")


def row(c, y, label, amt, a, col, small=False, w=None):
    h = 58 if small else 76
    c.card(LM, y, w or (W - 2 * LM), h, 20 if small else 26, alpha=a)
    c.check(LM + 38, y + h / 2, 14 if small else 20, col, a)
    txt(c, LM + 72, y + h / 2 + (11 if small else 14), label, "pjsb", 30 if small else 36, C["ink900"], a)
    txt(c, W - LM - 34, y + h / 2 + (12 if small else 15), fmt_inr(amt), "pjxb", 32 if small else 40, C["ink900"] if small else C["coral400"], a, "r")


def scene3(c, t):
    a0, b0 = S[2]; e = env(t, a0, b0, 0.25, 0.25)
    hdr(c, t, a0 + 0.05, "An example.", None, 76, e=e)
    pill(c, W / 2, 1600, "ILLUSTRATIVE EXAMPLE", C["teal800"], C["teal50"], 24, q(prog(t, a0 + 0.3, 0.4)) * e)
    own0 = T.beat(2, "You own") + 0.0
    tt = T.beat(2, "ten lakh") - 0.1; ow = T.beat(2, "You owe") - 0.2; tt2 = T.beat(2, "three lakh") - 0.1; nw = T.beat(2, "Net worth") - 0.1
    txt(c, LM, 448, "You own", "pjsb", 28, C["grey"], q(prog(t, own0, 0.3)) * e)
    for i, (lab, amt) in enumerate(NW["assets"]):
        ta = own0 + 0.15 + i * 0.28
        y = 466 + i * 66 + (1 - q(prog(t, ta, 0.35))) * 20
        row(c, y, lab, amt, env(t, ta, b0, 0.3, 0.25) * e, C["teal200"], small=True)
    ya = 806
    ta = tt; ea = env(t, ta, b0, 0.3, 0.25) * e
    if ea > 0.01:
        yy = ya + (1 - q(prog(t, ta, 0.35))) * 18
        c.card(LM, yy, W - 2 * LM, 82, 26, fill=C["teal50"], alpha=ea)
        txt(c, LM + 36, yy + 56, "You own", "pjxb", 36, C["teal800"], ea); txt(c, W - LM - 34, yy + 58, fmt_inr(OWN), "pjxb", 44, C["teal800"], ea, "r")
    txt(c, LM, 944, "You owe", "pjsb", 28, C["grey"], q(prog(t, ow, 0.3)) * e)
    for i, (lab, amt) in enumerate(NW["liabs"]):
        ta = ow + 0.12 + i * 0.3
        y = 962 + i * 66 + (1 - q(prog(t, ta, 0.35))) * 20
        row(c, y, lab, amt, env(t, ta, b0, 0.3, 0.25) * e, C["coral200"], small=True)
    ea = env(t, tt2, b0, 0.3, 0.25) * e
    if ea > 0.01:
        yy = 1100 + (1 - q(prog(t, tt2, 0.35))) * 18
        c.card(LM, yy, W - 2 * LM, 82, 26, fill=C["coral50"], alpha=ea)
        txt(c, LM + 36, yy + 56, "You owe", "pjxb", 36, C["coral400"], ea); txt(c, W - LM - 34, yy + 58, "− " + fmt_inr(OWE), "pjxb", 44, C["coral400"], ea, "r")
    ea = env(t, nw, b0, 0.3, 0.25) * e
    if ea > 0.01:
        k = q(prog(t, nw, 0.45)); yy = 1214 + (1 - k) * 24
        c.card(LM, yy, W - 2 * LM, 130, 34, fill=C["teal400"], alpha=ea)
        txt(c, LM + 40, yy + 82, "Net worth", "pjxb", 44, "#FFFFFF", ea); txt(c, W - LM - 40, yy + 86, fmt_inr(OWN - OWE), "pjxb", 62, "#FFFFFF", ea, "r")


def scene4(c, t):
    a0, b0 = S[3]; e = env(t, a0, b0, 0.25, 0.25)
    hdr(c, t, a0 + 0.05, "Why it matters.", None, 80, e=e)
    cards = [(T.beat(3, "It shows") - 0.1, "Shows direction", "Salary can rise while net worth stalls.", C["teal200"]),
             (T.beat(3, "It exposes") - 0.1, "Exposes quiet debt", "A loan can quietly cancel your savings.", C["coral200"]),
             (T.beat(3, "And it gives") - 0.1, "A scoreboard", "Watch the trend, not just the number.", C["amber200"])]
    for j, (ta, title, body, col) in enumerate(cards):
        ce = env(t, ta, b0, 0.3, 0.25) * e
        if ce <= 0.01:
            continue
        y0 = 470 + j * 160 + (1 - q(prog(t, ta, 0.4))) * 24
        c.card(LM, y0, W - 2 * LM, 136, 30, alpha=ce); c.check(LM + 58, y0 + 68, 22, col, ce)
        txt(c, LM + 104, y0 + 62, title, "pjxb", 38, C["ink900"], ce); txt(c, LM + 104, y0 + 106, body, "pj", 27, C["grey"], ce)
    # mini chart: salary keeps rising, net worth stalls
    ca = env(t, cards[0][0] + 0.3, b0, 0.4, 0.25) * e
    if ca > 0.01:
        x0, y0, wch, hch = LM + 20, 1000, W - 2 * LM - 40, 250
        c.rrect(LM, y0 - 30, W - 2 * LM, hch + 100, 30, fill=rgb("#FFFFFF", ca * 0.0))
        txt(c, LM, y0 - 10, "Over time", "pjsb", 26, C["grey"], ca)
        g = q(prog(t, cards[0][0] + 0.4, 1.6))
        n = 40
        sal = [(x0 + wch * i / n, y0 + hch - 190 * (i / n)) for i in range(n + 1)]
        nwp = [(x0 + wch * i / n, y0 + hch - 40 - (20 * (i / n) if i < 22 else 20 * 22 / n - 70 * ((i - 22) / (n - 22)) ** 1.2 * 0.5)) for i in range(n + 1)]
        k = int(g * n)
        if k > 1:
            c.line(sal[:k + 1], rgb(C["coral400"], ca), 7); c.line(nwp[:k + 1], rgb(C["teal400"], ca), 7)
        txt(c, x0 + wch, y0 + hch + 56, "Salary", "pjsb", 26, C["coral400"], ca, "r"); txt(c, x0, y0 + hch + 56, "Net worth", "pjsb", 26, C["teal400"], ca)


def tilted(c, cx, cy, w, h, label, ang, a):
    if a <= 0.01:
        return
    s = c.s; pad = 40
    lay = Image.new("RGBA", (int((w + 2 * pad) * s), int((h + 2 * pad) * s)), (0, 0, 0, 0))
    sub = Canvas(int(w + 2 * pad), int(h + 2 * pad), scale=c.s, bg="#FFFFFF"); sub.img = lay; 
    from PIL import ImageDraw
    sub.d = ImageDraw.Draw(lay, "RGBA")
    sub.shadow(pad, pad, w, h, 24, alpha=0.12)
    sub.rrect(pad, pad, w, h, 24, fill=(255, 255, 255, 255))
    f = mascot  # noqa
    lay_text = Canvas(int(w + 2 * pad), int(h + 2 * pad), scale=c.s, bg="#FFFFFF")
    sub.img = lay
    # text on its own mask via Canvas.text (paste solid colour through mask) needs an RGB base: draw onto a temp RGB then composite via alpha of card
    tmp = Image.new("RGB", lay.size, (255, 255, 255)); tcv = Canvas(int(w + 2 * pad), int(h + 2 * pad), scale=c.s, bg="#FFFFFF"); tcv.img = tmp
    tcv.d = ImageDraw.Draw(tmp, "RGBA"); tcv.text(pad + w / 2, pad + h / 2 + 12, label, "pjxb", 32, rgb(C["ink900"]), "m")
    mask = lay.getchannel("A").point(lambda v: 255 if v > 250 else 0)
    lay.paste(tmp, (0, 0), ImageChops.multiply(mask, Image.new("L", lay.size, 255)))
    lay = lay.rotate(ang, resample=Image.BICUBIC, expand=True)
    if a < 1:
        lay.putalpha(lay.getchannel("A").point(lambda v: int(v * a)))
    c.img.paste(lay, (int(cx * s - lay.width / 2), int(cy * s - lay.height / 2)), lay)


def scene5(c, t):
    a0, b0 = S[4]; e = env(t, a0, b0, 0.25, 0.25)
    hdr(c, t, a0 + 0.05, "But your money lives", "in five places.", 72, e=e)
    items = [("Bank app", 300, 640, -4), ("Demat app", 770, 700, 3), ("Mutual fund app", 280, 860, 3), ("EPF passbook", 780, 930, -3), ("Loan portal", 520, 1090, 2)]
    for i, (lab, x, y, ang) in enumerate(items):
        ta = a0 + 0.5 + i * 0.22
        tilted(c, x, y + (1 - q(prog(t, ta, 0.4))) * 30 + math.sin(t * 1.4 + i) * 4, 380, 86, lab, ang, env(t, ta, b0, 0.3, 0.25) * e)
    ba = q(prog(t, a0 + 1.7, 0.4)) * e
    txt(c, W / 2, 1290, "5 places. 0 totals.", "pjxb", 70, C["coral400"], ba, "m")


def scene6(c, t):
    a0, b0 = S[5]; e = env(t, a0, b0, 0.25, 0.25)
    hdr(c, t, a0 + 0.05, "Net worth needs", "a habit.", 76, e=e)
    ta = T.beat(5, "log your") - 0.2
    ce = env(t, ta, b0, 0.3, 0.25) * e
    if ce > 0.01:
        y0 = 520 + (1 - q(prog(t, ta, 0.4))) * 24
        c.card(LM, y0, W - 2 * LM, 130, 30, alpha=ce); c.check(LM + 58, y0 + 65, 22, C["teal200"], ce)
        txt(c, LM + 104, y0 + 58, "Daily", "pjxb", 38, C["ink900"], ce); txt(c, LM + 104, y0 + 102, "Log what you spend, in seconds.", "pj", 27, C["grey"], ce)
        n = min(10, int(max(0, t - ta - 0.3) / 0.11) + 1)
        put_app(c, f"add_t{n:02d}", 120, y0 + 160, 840, 112, 222, ce)
    tb = T.beat(5, "update your") - 0.1
    ce = env(t, tb, b0, 0.3, 0.25) * e
    if ce > 0.01:
        y0 = 1010 + (1 - q(prog(t, tb, 0.4))) * 24
        c.card(LM, y0, W - 2 * LM, 130, 30, alpha=ce); c.check(LM + 58, y0 + 65, 22, C["amber200"], ce)
        txt(c, LM + 104, y0 + 58, "Monthly", "pjxb", 38, C["ink900"], ce); txt(c, LM + 104, y0 + 102, "Update your balances.", "pj", 27, C["grey"], ce)
        txt(c, W / 2, 1200, "DEMO ACCOUNT · REAL APP", "pjsb", 22, C["grey"], ce * .9, "m", 3)


CHIPS = [("Savings", 0), ("FD", 0), ("RD", 0), ("PPF", 0), ("EPF", 0), ("VPF", 0), ("NPS", 0), ("SSY", 0), ("Demat", 0), ("Mutual funds", 0), ("ETF", 0), ("SGB", 0),
         ("Gold", 0), ("Real estate", 0), ("Insurance", 0), ("Credit card", 1), ("Home loan", 1), ("Vehicle loan", 1)]


def scene7(c, t):
    a0, b0 = S[6]; e = env(t, a0, b0, 0.25, 0.25)
    t1, t2, t3 = T.beat(6, "puts it") - 0.1, T.beat(6, "savings") - 0.1, T.beat(6, "One number") - 0.15
    p1 = env(t, t1, t2 - 0.05, 0.3, 0.22) * e; p2 = env(t, t2 + 0.1, t3 - 0.05, 0.3, 0.22) * e; p3 = env(t, t3 + 0.05, b0, 0.3, 0.25) * e
    if p1 > 0.01:
        a, dy = rise(t, t1); txt(c, LM, 330 + dy, "One place.", "pjxb", 94, C["ink900"], a * p1)
        put_app(c, "accounts_top", (W - 780) // 2, 520 + (1 - q(prog(t, t1 + 0.1, 0.4))) * 28, 780, 215, 430, p1)
        txt(c, W / 2, 1110, "DEMO ACCOUNT · REAL APP", "pjsb", 22, C["grey"], p1 * .9, "m", 3)
    if p2 > 0.01:
        a, dy = rise(t, t2); txt(c, LM, 330 + dy, "30+ account types.", "pjxb", 80, C["ink900"], a * p2)
        txt(c, LM, 395 + dy, "Straight from the app.", "pjsb", 32, C["coral400"], a * p2)
        x, y = LM, 520
        for i, (lab, kind) in enumerate(CHIPS):
            wch = c.text_width(lab, "pjsb", 32) + 70
            if x + wch > W - LM:
                x, y = LM, y + 96
            ca = q(prog(t, t2 + 0.15 + i * 0.07, 0.3)) * p2
            if ca > 0.01:
                c.card(x, y, wch, 72, 36, alpha=ca)
                c.ellipse(x + 30, y + 36, 9, fill=rgb(C["coral200"] if kind else C["teal200"], ca))
                txt(c, x + 50, y + 48, lab, "pjsb", 32, C["ink900"], ca)
            x += wch + 16
        txt(c, LM, y + 190, "…and more. Assets and liabilities, side by side.", "pj", 30, C["grey"], q(prog(t, t2 + 1.6, 0.4)) * p2)
    if p3 > 0.01:
        a, dy = rise(t, t3); txt(c, LM, 330 + dy, "One number,", "pjxb", 94, C["ink900"], a * p3); txt(c, LM, 430 + dy, "with history.", "pjxb", 94, C["coral400"], a * p3)
        put_app(c, "networth_top", (W - 700) // 2, 540 + (1 - q(prog(t, t3 + 0.1, 0.4))) * 28, 700, 240, 640, p3)
        txt(c, W / 2, 1300, "DEMO ACCOUNT · REAL APP", "pjsb", 22, C["grey"], p3 * .9, "m", 3)


def scene8(c, t):
    t0 = S[7][0]
    if t < t0:
        return
    a = q(prog(t, t0 + 0.35, 0.4)); b = q(prog(t, t0 + 0.7, 0.4)); d = q(prog(t, t0 + 1.0, 0.4)); f = q(prog(t, t0 + 1.35, 0.4))
    c.logo(W / 2 - 40, 790 - (1 - a) * 18, 80, False)
    txt(c, W / 2, 960, "TrackMyRupee", "pjxb", 92, C["ink900"], a, "m")
    txt(c, W / 2, 1030, "See your net worth in one place.", "pjsb", 36, C["coral400"], b, "m")
    c.card(W / 2 - 320, 1110, 640, 118, 59, fill=C["teal400"], alpha=d)
    txt(c, W / 2, 1186, "Try the free demo", "pjxb", 48, "#FFFFFF", d, "m")
    txt(c, W / 2, 1290, "trackmyrupee.com/demo", "pjsb", 38, C["ink900"], f, "m")
    txt(c, W / 2, 1356, "No bank login  ·  No SMS access", "pj", 30, C["grey"], f, "m")


def frame(n):
    t = n / FPS
    c = Canvas(W, H, bg=C["paper"])
    if t < S[7][0] - 0.2:
        c.logo(LM, 110, 46, True, word_color=C["ink900"], word_size=26)
    scene1(c, t); scene2(c, t); scene3(c, t); scene4(c, t); scene5(c, t); scene6(c, t); scene7(c, t); scene8(c, t)
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
