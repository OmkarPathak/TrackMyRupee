"""Generic 1080x1350 carousel slide renderer + PDF (used from Phase 5 on)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render.brand import C, Canvas, rgb

W, H = 1080, 1350
THEMES = {  # bg, headline, body, accent
    "dark": ("night", C["cream"], "#C9D6D1", C["teal200"]),
    "light": (C["cream"], C["ink900"], C["ink600"], C["teal400"]),
    "teal": (C["teal400"], C["white"], "#E1F5EE", C["amber200"]),
}


def _wrap(c, text, key, size, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if c.text_width(t, key, size) <= maxw: cur = t
        else: lines.append(cur); cur = w
    return lines + ([cur] if cur else [])


def slide(s, i, n):
    th = THEMES[s.get("theme", "dark")]
    c = Canvas(W, H) if th[0] == "night" else Canvas(W, H, bg=th[0])
    pad = 90
    c.logo(pad, 80, 52, True, word_color=th[1], word_size=28)
    c.text(W - pad, 118, f"{i}/{n}", "sansb", 28, rgb(th[2]), "r")
    size = s.get("headline_size", 96 if i > 1 else 112)
    y = 340
    for ln in _wrap(c, s["headline"], "serif", size, W - 2 * pad):
        c.text(pad, y, ln, "serif", size, th[1]); y += size * 1.08
    if s.get("stat"):
        y += 30; c.text(pad, y + 120, s["stat"], "serif", 190, th[3]); y += 200
    y += 30
    for ln in _wrap(c, s.get("body", ""), "sans", 44, W - 2 * pad):
        c.text(pad, y, ln, "sans", 44, th[2]); y += 62
    if s.get("footer"):
        c.text(pad, H - 90, s["footer"], "sansb", 26, rgb(th[2]), "l", 2)
    return c.done()


def render_all(slides, outdir):
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    ims = [slide(s, i + 1, len(slides)) for i, s in enumerate(slides)]
    for i, im in enumerate(ims, 1):
        im.save(outdir / f"slide_{i:02d}.png")
    ims[0].save(outdir / "carousel.pdf", save_all=True, append_images=ims[1:], resolution=150)
    return outdir
