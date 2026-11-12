"""Carousel renderer in the "Rupee Minis" style of the posted carousel (measured from the
director's screenshots): flat #FBF9F5, Plus Jakarta Sans ExtraBold headlines with tight
tracking, coral/green italic accent lines, white bordered cards, "@trackmyrupee" bottom-left.
1080x1350 PNG per slide + PDF.

Slide types: cover · list · statement · intro · checks · steps · example · cta
Each slide is a dict; see packages/carousel_003.py."""
from __future__ import annotations
import sys
from pathlib import Path
from PIL import Image, ImageDraw
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render.brand import C, Canvas, rgb

W, H, LM = 1080, 1350, 120
BG, INK, CORAL, GREEN = "#FBF9F5", "#1A1A1A", "#C23E26", "#0F7658"
GREY, GREY2, PILL_BG, PILL_TX, ICON = "#4B4B46", "#5A5A56", "#E2F5EF", "#085041", "#1D9F75"
BORDER = "#E8E5DF"
H1_TR = -0.067
MAXW = W - 2 * LM
WARN = []


def canvas():
    return Canvas(W, H, bg=BG)


def words_line(c, x, y, text, key, size, col, tr):
    """Letters tightened, word spaces left natural (as in the posted carousel)."""
    from render.brand import font as _font
    sp = _font(key, int(size * c.s)).getlength(" ") / c.s
    x0 = x
    for i, w in enumerate(text.split(" ")):
        adv = c.text(x, y, w, key, size, rgb(col), "l", size * tr)
        x += adv + sp
    return x - sp - x0


def head(c, lines, y, size=76, pitch=88, tr=-0.06, key="pjxb", col=INK):
    for i, ln in enumerate(lines):
        w = words_line(c, LM, y + i * pitch, ln, key, size, col, tr)
        if w > MAXW + 2:
            WARN.append(f"line too wide ({w:.0f}px): {ln!r}")
    return y + (len(lines) - 1) * pitch


def accent(c, lines, y, col=CORAL, size=76, pitch=87, tr=-0.047):
    return head(c, lines, y, size, pitch, tr, "pji", col)


def body(c, lines, y, size=40, pitch=59, col=GREY):
    for i, ln in enumerate(lines):
        w = c.text(LM, y + i * pitch, ln, "pj", size, rgb(col))
        if w > MAXW + 2:
            WARN.append(f"body too wide ({w:.0f}px): {ln!r}")


def handle(c, y=1223):
    c.text(LM, y, "@trackmyrupee", "pj", 32, rgb(GREY2), "l", -1.0)


def card(c, y, h, fill="#FFFFFF"):
    c.rrect(LM, y, MAXW, h, 36, fill=rgb(fill), outline=rgb(BORDER), width=2)


def pill_out(c, right, cy, label, col=CORAL, fill=None):
    w = c.text_width(label, "pjsb", 30) + 52
    c.rrect(right - w, cy - 31, w, 62, 31, fill=rgb(fill) if fill else None, outline=rgb(col), width=2.5)
    c.text(right - w / 2, cy + 11, label, "pjsb", 30, rgb(col), "m")


def check(c, cx, cy, r=28, col=GREEN):
    c.ellipse(cx, cy, r, fill=rgb(col))
    k = r / 28
    c.line([(cx - 11 * k, cy + 1 * k), (cx - 3 * k, cy + 9 * k), (cx + 12 * k, cy - 9 * k)], (255, 255, 255), 4.6 * k)


def slide_cover(s):
    c = canvas()
    w = c.text_width(s["pill"], "pjsb", 33, -1) + 60
    c.rrect(LM, 318, w, 68, 34, fill=rgb(PILL_BG)); c.text(LM + 30, 364, s["pill"], "pjsb", 33, rgb(PILL_TX), "l", -1.0)
    y = head(c, s["h1"], 533, 107, 113, H1_TR)
    accent(c, s["accent"], 780)
    body(c, s["body"], 876)
    c.text(LM, 1113, "Swipe", "pj", 34, rgb(GREY)); c.line([(240, 1100), (296, 1100)], rgb(GREY), 2.6); c.line([(282, 1088), (296, 1100), (282, 1112)], rgb(GREY), 2.6)
    handle(c, 1175)
    return c.done()


def slide_list(s):
    c = canvas()
    head(c, s["h2"], 293)
    y0 = s.get("y0", 442); ch = s.get("card_h", 118); gap = s.get("gap", 27)
    for i, (label, tag) in enumerate(s["cards"]):
        y = y0 + i * (ch + gap); card(c, y, ch)
        c.text(LM + 36, y + ch / 2 + 13, label, "pjsb", 37, rgb(INK))
        if tag:
            pill_out(c, LM + MAXW - 28, y + ch / 2, tag, s.get("tag_col", CORAL))
    if s.get("accent"):
        accent(c, s["accent"], s.get("accent_y", y0 + len(s["cards"]) * ch + (len(s["cards"]) - 1) * gap + 137), s.get("accent_col", CORAL))
    handle(c)
    return c.done()


def slide_statement(s):
    c = canvas()
    y = head(c, s["h2"], s.get("y", 413))
    accent(c, s["accent"], s.get("accent_y", y + 154), s.get("accent_col", GREEN))
    if s.get("body"):
        body(c, s["body"], s.get("body_y", y + 154 + len(s["accent"]) * 87 + 60))
    handle(c)
    return c.done()


def slide_intro(s):
    c = canvas()
    c.text(LM, 349, s["pre"], "pj", 48, rgb(GREY), "l", -1.8)
    c.rrect(LM, 408, 220, 220, 52, fill=rgb(ICON))
    k = 220 / 32; x, y = LM, 408
    pts = [(x + 6 * k, y + 23 * k), (x + 12 * k, y + 17 * k), (x + 16 * k, y + 21 * k), (x + 26 * k, y + 9 * k)]
    c.line(pts, (255, 255, 255), 3 * k)
    for px, py in pts[:3]:
        c.ellipse(px, py, 1.5 * k, fill=(255, 255, 255))
    c.ellipse(x + 26 * k, y + 9 * k, 2.5 * k, fill=(255, 255, 255))
    words_line(c, LM, 780, "TrackMyRupee", "pjxb", 111, INK, -0.033)
    c.text(LM, 890, s["sub"], "pj", 43, rgb(GREY))
    handle(c)
    return c.done()


def slide_checks(s):
    c = canvas()
    head(c, s["h2"], 393)
    for i, it in enumerate(s["items"]):
        base = 516 + i * 109.5
        check(c, LM + 28, base - 18)
        text, bold = (it if isinstance(it, tuple) else (it, False))
        w = c.text(209, base, text, "pjxb" if bold else "pj", 46, rgb(INK), "l", -0.5 if bold else 0)
        if w > W - LM - 209 + 2:
            WARN.append(f"check item too wide ({w:.0f}px): {text!r}")
    if s.get("accent"):
        accent(c, s["accent"], s.get("accent_y", 516 + len(s["items"]) * 109.5 + 40), s.get("accent_col", CORAL))
    handle(c)
    return c.done()


def slide_steps(s):
    c = canvas()
    head(c, s["h2"], 293)
    y0 = 442
    for i, it in enumerate(s["steps"]):
        y = y0 + i * 190
        c.ellipse(LM + 28, y + 28, 28, fill=rgb(GREEN)); c.text(LM + 28, y + 43, str(i + 1), "pjxb", 36, (255, 255, 255), "m")
        for j, ln in enumerate(it):
            c.text(209, y + 44 + j * 58, ln, "pjxb" if (j == 0 and len(it) > 1) else "pj", 44 if j == 0 else 38, rgb(INK if j == 0 else GREY))
    handle(c)
    return c.done()


def slide_example(s):
    c = canvas()
    y = head(c, s["h2"], 293)
    card(c, 520, 330)  # label / maths / result
    c.text(LM + 40, 590, s["label"], "pjsb", 30, rgb(GREY))
    mw = c.text(LM + 40, 690, s["maths"], "pjxb", 58, rgb(INK), "l", -2.0)
    if mw > MAXW - 80: WARN.append(f"maths too wide ({mw:.0f}px)")
    c.line([(LM + 40, 730), (LM + MAXW - 40, 730)], rgb(BORDER), 2)
    c.text(LM + 40, 800, s["result"], "pjxb", 72, rgb(CORAL), "l", -2.5)
    pill = s.get("tag", "Example · illustrative")
    c.text(LM, 910, pill, "pjsb", 28, rgb(PILL_TX), "l", 0.5)
    accent(c, s["accent"], 1010, s.get("accent_col", CORAL))
    handle(c)
    return c.done()


def slide_cta(s):
    c = canvas()
    head(c, s["h2"], 293)
    accent(c, s["accent"], 293 + 88 * (len(s["h2"]) - 1) + 130, GREEN)
    y = 700
    c.rrect(LM, y, MAXW, 118, 59, fill=rgb(GREEN)); c.text(W / 2, y + 76, s["button"], "pjxb", 42, (255, 255, 255), "m", -0.5)
    c.text(LM, y + 200, s["url"], "pjsb", 40, rgb(INK))
    body(c, s["body"], y + 290, 36, 54)
    handle(c)
    return c.done()


KINDS = {"cover": slide_cover, "list": slide_list, "statement": slide_statement, "intro": slide_intro, "checks": slide_checks,
         "steps": slide_steps, "example": slide_example, "cta": slide_cta}


def render_all(slides, outdir, prefix="slide"):
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    ims = []
    for i, s in enumerate(slides, 1):
        im = KINDS[s["kind"]](s); ims.append(im); im.save(outdir / f"{prefix}_{i:02d}.png")
    ims[0].convert("RGB").save(outdir / "carousel.pdf", save_all=True, append_images=[x.convert("RGB") for x in ims[1:]], resolution=150)
    return ims
