"""Rupee Minis mascot (calm piggy bank) — vector re-draw from the supplied SVG
(assets/mascot/*.svg, 200-unit viewBox, 'on-teal' colourway) so its parts can be
animated: bob, squash, head-shake, ear/tail wiggle, coin drop, eye mood.
Geometry and colours are taken 1:1 from the SVG; only motion is added."""
import math
from PIL import Image, ImageDraw, ImageFilter

BODY, LEG, EAR1, EAR2, SNOUT = "#E1F5EE", "#9FE1CB", "#9FE1CB", "#B5EAD6", "#9FE1CB"
DARK, AMBER, AMBER_RING, AMBER_INK = "#085041", "#F4B44E", "#FAD9A0", "#8F4E00"


def _h(c, a=255):
    c = c.lstrip("#"); return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


def _bez(p0, p1, p2, p3, n=24):
    return [tuple((1 - t) ** 3 * p0[i] + 3 * (1 - t) ** 2 * t * p1[i] + 3 * (1 - t) * t * t * p2[i] + t ** 3 * p3[i] for i in (0, 1)) for t in [k / n for k in range(n + 1)]]


def _qbez(p0, p1, p2, n=16):
    return [tuple((1 - t) ** 2 * p0[i] + 2 * (1 - t) * t * p1[i] + t * t * p2[i] for i in (0, 1)) for t in [k / n for k in range(n + 1)]]


def _rot(pts, ang, pivot):
    a = math.radians(ang); ca, sa = math.cos(a), math.sin(a)
    return [(pivot[0] + (x - pivot[0]) * ca - (y - pivot[1]) * sa, pivot[1] + (x - pivot[0]) * sa + (y - pivot[1]) * ca) for x, y in pts]


TAIL = _bez((33, 102), (17, 104), (9, 90), (17, 80)) + _bez((17, 80), (23, 73), (32, 78), (29, 85))
EAR1_P = _bez((106, 62), (102, 46), (108, 36), (118, 38)) + _bez((118, 38), (128, 40), (132, 50), (134, 62))
EAR2_P = _bez((137, 76), (137, 57), (143, 48), (150, 50)) + _bez((150, 50), (157, 52), (159, 68), (155, 86))
SLOT = _qbez((62, 67), (78, 59), (94, 60))
EYE = _qbez((126, 95), (134, 86), (142, 95))
RUPEE = [[(72.5, 24.5), (83.5, 24.5)], [(72.5, 29), (83.5, 29)],
         [(72.5, 24.5), (76, 24.5)] + _qbez((76, 24.5), (81.5, 24.5), (81.5, 28.8)) + _qbez((81.5, 28.8), (81.5, 33), (76, 33)) + [(72.5, 33), (80.5, 39.5)]]


def _stroke(d, pts, w, fill, u):
    P = [(x * u, y * u) for x, y in pts]
    d.line(P, fill=fill, width=max(1, int(w * u)), joint="curve")
    r = w * u / 2
    for x, y in (P[0], P[-1]):
        d.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def _ell(d, cx, cy, rx, ry, fill, u):
    d.ellipse([(cx - rx) * u, (cy - ry) * u, (cx + rx) * u, (cy + ry) * u], fill=fill)


def sprite(unit=4.0, mood="happy", ear=0.0, tail=0.0, coin_y=32.0, coin_front=True, coin_rot=0.0, blink=0.0):
    """Render the mascot to an RGBA sprite of 200*unit square.
    mood: 'happy' (closed smiling eye) | 'wide' (open, worried eye)."""
    S = int(200 * unit)
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im, "RGBA")
    u = unit
    # shadow
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0)); _ell(ImageDraw.Draw(sh), 96, 177, 64, 7, (0, 0, 0, 90), u)
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(3 * u / 4)))
    d = ImageDraw.Draw(im, "RGBA")

    def coin():
        cy = coin_y
        _ell(d, 78, cy, 14, 14, _h(AMBER), u)
        d.ellipse([(78 - 10) * u, (cy - 10) * u, (78 + 10) * u, (cy + 10) * u], outline=_h(AMBER_RING), width=int(2 * u))
        for seg in RUPEE:
            _stroke(d, [(x, y - 32 + cy) for x, y in seg], 2, _h(AMBER_INK), u)

    if not coin_front:
        coin()
    # legs
    for x in (54, 112):
        d.rounded_rectangle([x * u, 128 * u, (x + 24) * u, 172 * u], radius=12 * u, fill=_h(LEG))
    _stroke(d, _rot(TAIL, tail, (33, 102)), 7, _h(LEG), u)
    e1 = _rot(EAR1_P, ear, (120, 62)); e2 = _rot(EAR2_P, -ear * .8, (146, 84))
    for pts, col in ((e1, EAR1), (e2, EAR2)):
        d.polygon([(x * u, y * u) for x, y in pts], fill=_h(col)); _stroke(d, pts + [pts[0]], 4, _h(col), u)
    _ell(d, 96, 104, 68, 54, _h(BODY), u)
    # shine (soft white) — blur the alpha mask only, so no grey fringe
    m = Image.new("L", (int(110 * u), int(70 * u)), 0); ImageDraw.Draw(m).ellipse([int(26 * u), int(23 * u), int(84 * u), int(47 * u)], fill=150)
    m = m.filter(ImageFilter.GaussianBlur(5 * u)).rotate(16, expand=True, resample=Image.BICUBIC)
    lay = Image.new("RGBA", m.size, (255, 255, 255, 0)); lay.putalpha(m)
    im.alpha_composite(lay, (int(72 * u - lay.width / 2), int(80 * u - lay.height / 2)))
    d = ImageDraw.Draw(im, "RGBA")
    _ell(d, 158, 112, 21, 16, _h(SNOUT), u)
    _stroke(d, SLOT, 6, _h(DARK, 100), u)
    # blush
    m = Image.new("L", (int(60 * u), int(60 * u)), 0); ImageDraw.Draw(m).ellipse([int(17 * u), int(17 * u), int(43 * u), int(43 * u)], fill=190)
    m = m.filter(ImageFilter.GaussianBlur(5 * u))
    bl = Image.new("RGBA", m.size, _h("#F49C8C", 0)); bl.putalpha(m)
    im.alpha_composite(bl, (int(120 * u - 30 * u), int(111 * u - 30 * u)))
    d = ImageDraw.Draw(im, "RGBA")
    # eye
    if mood == "happy" or blink > .5:
        _stroke(d, EYE, 4.5, _h(DARK), u)
    else:
        _ell(d, 134, 96, 5.6, 7, _h(DARK), u); _ell(d, 135.8, 93.6, 1.7, 1.7, (255, 255, 255, 235), u)
        _stroke(d, [(124, 83), (134, 80), (143, 85)] if mood == "wide" else [(125, 85), (143, 82)], 3.2, _h(DARK), u)
    _ell(d, 152, 110, 2.6, 4.2, _h(DARK), u); _ell(d, 164, 110, 2.6, 4.2, _h(DARK), u)
    if coin_front:
        coin()
    return im


def draw(c, cx, ybottom, size, t=0.0, mood="happy", rot=0.0, squash=0.0, alpha=1.0, coin_y=None, coin_front=True, wiggle=1.0, flip=False):
    """Draw onto a brand Canvas. cx = horizontal centre, ybottom = feet baseline.
    size = sprite width in design px. Idle bob / ear / tail motion included."""
    s = c.s; unit = size * s / 200
    bob = math.sin(t * 3.2) * 0.012 * size * wiggle
    sp = sprite(unit, mood, ear=math.sin(t * 5.1) * 7 * wiggle, tail=math.sin(t * 4.3) * 14 * wiggle,
                coin_y=32 + math.sin(t * 3.6) * 1.6 if coin_y is None else coin_y, coin_front=coin_front)
    if flip:
        sp = sp.transpose(Image.FLIP_LEFT_RIGHT)
    sx, sy = 1 + squash * 0.5, 1 - squash
    if sx != 1 or sy != 1:
        sp = sp.resize((int(sp.width * sx), int(sp.height * sy)), Image.LANCZOS)
    if rot:
        sp = sp.rotate(rot, resample=Image.BICUBIC, expand=True, center=(sp.width / 2, sp.height * .88))
    if alpha < 1:
        sp.putalpha(sp.getchannel("A").point(lambda v: int(v * alpha)))
    # sprite feet at 178/200 of its height
    feet = 178 / 200 * (200 * unit * sy)
    c.img.paste(sp, (int(cx * s - sp.width / 2), int(ybottom * s - feet - bob * s - (sp.height - 200 * unit * sy) / 2)), sp)
