"""Brand tokens + a small supersampled drawing canvas shared by the Reel,
cover and carousel renderers.

Colours are taken from the production site (templates/landing.html, the
--tmr-* tokens). Fonts are the site's own pair: Instrument Serif (display) and
DM Sans (UI). Instrument Serif has no rupee glyph, so rupee signs inside serif
text are drawn with DM Sans at the same size.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "assets" / "fonts"
REPO = ROOT.parent

C = {
    "teal50": "#E1F5EE", "teal100": "#9FE1CB", "teal200": "#5DCAA5",
    "teal400": "#0F7657", "teal600": "#0F6E56", "teal800": "#085041", "teal900": "#04342C",
    "coral50": "#FEF0EE", "coral200": "#F49C8C", "coral400": "#C23E27",
    "amber50": "#FDF3E3", "amber200": "#F4B44E", "amber400": "#8F4E00",
    "blue50": "#E6F1FB", "blue200": "#85B7EB", "blue400": "#185FA5",
    "ink900": "#1A1A19", "ink600": "#4B4B48", "ink400": "#595956", "ink100": "#EBEBEA",
    "cream": "#F2F0EB", "white": "#FFFFFF",
    "night_top": "#06302A", "night_bot": "#0B1311",
}

FONTS = {
    "serif": "InstrumentSerif-Regular.ttf",
    "sans": "DMSans-Regular.ttf",
    "sansm": "DMSans-Medium.ttf",
    "sansb": "DMSans-Bold.ttf",
    "deva": "NotoSansDevanagari-SemiBold.ttf",
    "pj": "PlusJakartaSans-Medium.ttf", "pjsb": "PlusJakartaSans-SemiBold.ttf", "pjxb": "PlusJakartaSans-ExtraBold.ttf",
    "mono": "DMMono-Medium.ttf",
    "pji": "PlusJakartaSans-Italic.ttf", "pjmi": "PlusJakartaSans-MediumItalic.ttf",
}
C.update({"paper": "#FAF8F6", "tile": "#E9E6E1", "grey": "#8A8A86", "pill": "#2B2B2B", "input": "#ECEAE6"})


def rgb(h: str, a: float | None = None):
    h = h.lstrip("#")
    t = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return t if a is None else t + (int(round(255 * a)),)


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def prog(t, start, dur):
    return clamp((t - start) / dur) if dur > 0 else float(t >= start)


def ease_out(p):
    return 1 - (1 - p) ** 3


def ease_in_out(p):
    return 3 * p * p - 2 * p ** 3


def ease_back(p, s=1.45):
    p -= 1
    return p * p * ((s + 1) * p + s) + 1


@lru_cache(maxsize=256)
def font(key: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / FONTS[key]), px)


@lru_cache(maxsize=8)
def gradient(w, h, top, bot):
    ys = [i / (h - 1) for i in range(h)]
    col = Image.new("RGB", (1, h))
    a, b = rgb(top), rgb(bot)
    col.putdata([tuple(int(a[k] + (b[k] - a[k]) * y) for k in range(3)) for y in ys])
    return col.resize((w, h))


class Canvas:
    """Draw in design pixels (e.g. 1080x1920); renders at `scale`x and
    downsamples for anti-aliasing."""

    def __init__(self, w=1080, h=1920, scale=2, bg=None, grad=("night_top", "night_bot")):
        self.w, self.h, self.s = w, h, scale
        if bg:
            self.img = Image.new("RGB", (w * scale, h * scale), rgb(bg))
        else:
            self.img = gradient(w * scale, h * scale, C[grad[0]], C[grad[1]]).copy()
        self.d = ImageDraw.Draw(self.img, "RGBA")

    # -- primitives -------------------------------------------------------
    def _s(self, v):
        return int(round(v * self.s))

    def rrect(self, x, y, w, h, r, fill=None, outline=None, width=0):
        s = self._s
        self.d.rounded_rectangle([s(x), s(y), s(x + w) - 1, s(y + h) - 1], radius=s(r),
                                 fill=fill, outline=outline, width=s(width) if width else 0)

    def ellipse(self, cx, cy, r, fill=None, outline=None, width=0):
        s = self._s
        self.d.ellipse([s(cx - r), s(cy - r), s(cx + r), s(cy + r)], fill=fill,
                       outline=outline, width=s(width) if width else 0)

    def line(self, pts, fill, width):
        s = self._s
        self.d.line([(s(x), s(y)) for x, y in pts], fill=fill, width=s(width), joint="curve")

    def _runs(self, text, key):
        """Split text so ₹ always uses DM Sans (serif lacks the glyph)."""
        if key != "serif" or "₹" not in text:
            return [(text, key)]
        out, buf = [], ""
        for ch in text:
            if ch == "₹":
                if buf:
                    out.append((buf, key)); buf = ""
                out.append((ch, "sansm"))
            else:
                buf += ch
        if buf:
            out.append((buf, key))
        return out

    def text_width(self, text, key, size, tracking=0):
        f = lambda k: font(k, self._s(size * (0.78 if (k == "sansm" and key == "serif") else 1)))
        w = sum(f(k).getlength(t) for t, k in self._runs(text, key))
        return w / self.s + tracking * max(0, len(text) - 1)

    def text(self, x, y, text, key="sans", size=48, fill="#FFFFFF", anchor="l", tracking=0):
        """anchor: l / m / r horizontally; y is the baseline. Alpha in `fill` is honoured
        (drawn through an L mask — PIL ignores RGBA ink alpha for text on RGB images)."""
        s = self._s
        fill = rgb(fill) if isinstance(fill, str) else fill
        alpha = fill[3] if len(fill) > 3 else 255
        if alpha <= 0:
            return 0
        total = self.text_width(text, key, size, tracking)
        x0 = x - (total / 2 if anchor == "m" else total if anchor == "r" else 0)
        pad = int(size * 0.6 * self.s)
        Wm, Hm = int(total * self.s) + 2 * pad, int(size * 2.2 * self.s)
        mask = Image.new("L", (Wm, Hm), 0); md = ImageDraw.Draw(mask)
        base = int(size * 1.4 * self.s); cx = pad
        for run, k in self._runs(text, key):
            f = font(k, s(size * (0.78 if (k == "sansm" and key == "serif") else 1)))
            if tracking:
                for ch in run:
                    md.text((cx, base), ch, font=f, fill=255, anchor="ls"); cx += f.getlength(ch) + tracking * self.s
            else:
                md.text((cx, base), run, font=f, fill=255, anchor="ls"); cx += f.getlength(run)
        if alpha < 255:
            mask = mask.point(lambda v: v * alpha // 255)
        self.img.paste(Image.new("RGB", mask.size, tuple(fill[:3])), (s(x0) - pad, s(y) - base), mask)
        return total

    def shadow(self, x, y, w, h, r, blur=20, dy=10, alpha=0.10):
        s = self._s; pad = blur * 3
        layer = Image.new("RGBA", (s(w + 2 * pad), s(h + 2 * pad)), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle([s(pad), s(pad), s(pad + w), s(pad + h)], radius=s(r), fill=(26, 26, 25, int(255 * alpha)))
        layer = layer.filter(ImageFilter.GaussianBlur(s(blur)))
        self.img.paste(layer, (s(x - pad), s(y - pad + dy)), layer)

    def card(self, x, y, w, h, r=28, fill="#FFFFFF", alpha=1.0, shadow=True):
        if alpha <= 0:
            return
        if shadow:
            self.shadow(x, y, w, h, r, alpha=0.10 * alpha)
        self.rrect(x, y, w, h, r, fill=rgb(fill, alpha))

    def check(self, cx, cy, r, color, alpha=1.0):
        self.ellipse(cx, cy, r, fill=rgb(color, alpha))
        k = r / 15
        self.line([(cx - 7 * k, cy + 0.5 * k), (cx - 2 * k, cy + 6 * k), (cx + 8 * k, cy - 6 * k)], (255, 255, 255, int(255 * alpha)), 3.4 * k)

    def paste(self, im: Image.Image, x, y, alpha=1.0):
        s = self._s
        if alpha < 1:
            im = im.copy()
            a = im.getchannel("A").point(lambda v: int(v * alpha))
            im.putalpha(a)
        self.img.paste(im, (s(x), s(y)), im)

    def glow(self, x, y, w, h, r, color, blur=26, alpha=0.55):
        s = self._s
        pad = blur * 3
        layer = Image.new("RGBA", (s(w + 2 * pad), s(h + 2 * pad)), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(
            [s(pad), s(pad), s(pad + w), s(pad + h)], radius=s(r), fill=rgb(color, alpha))
        layer = layer.filter(ImageFilter.GaussianBlur(s(blur)))
        self.img.paste(layer, (s(x - pad), s(y - pad)), layer)

    def logo(self, x, y, size, with_word=False, word_color="#F2F0EB", word_size=None):
        """The real TrackMyRupee mark (static/img/logo.svg geometry, 32-unit grid)."""
        k = size / 32
        self.rrect(x, y, size, size, 6 * k, fill=rgb(C["teal400"]))
        pts = [(x + 6 * k, y + 23 * k), (x + 12 * k, y + 17 * k), (x + 16 * k, y + 21 * k), (x + 26 * k, y + 9 * k)]
        self.line(pts, (255, 255, 255), 3 * k)
        for px, py in pts:
            self.ellipse(px, py, 1.5 * k, fill=(255, 255, 255))
        self.ellipse(x + 26 * k, y + 9 * k, 2.5 * k, fill=(255, 255, 255))
        if with_word:
            ws = word_size or size * 0.62
            self.text(x + size + size * 0.28, y + size * 0.72, "TrackMyRupee", "sansb", ws, word_color)

    def done(self) -> Image.Image:
        return self.img.resize((self.w, self.h), Image.LANCZOS) if self.s != 1 else self.img


def fmt_inr(n: int) -> str:
    """Indian digit grouping: 1,50,000."""
    s = str(int(n))
    if len(s) <= 3:
        return "₹" + s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:]); head = head[:-2]
    if head:
        parts.insert(0, head)
    return "₹" + ",".join(parts + [tail])
