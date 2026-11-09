"""Reel 002 cover (calm paper): 1080x1920 + 1080x1350 grid crop."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render import mascot
from render.brand import C, Canvas, rgb


def build():
    W, H = 1080, 1920
    c = Canvas(W, H, bg=C["paper"])
    c.logo(90, 330, 56, True, word_color=C["ink900"], word_size=30)
    c.text(90, 540, "Same salary.", "pjxb", 92, C["ink900"]); c.text(90, 650, "5 years later.", "pjxb", 92, C["coral400"]); c.text(90, 775, "Why?", "pjxb", 118, C["ink900"])
    base, sc = 1480, 420 / 1400000
    for val, x0, col, lab in ((1400000, 150, C["teal400"], "Riya"), (400000, 560, C["coral200"], "Aman")):
        h = val * sc; c.rrect(x0, base - h, 330, h, 30, fill=rgb(col)); c.text(x0 + 165, base + 52, lab, "pjxb", 34, rgb(C["ink900"]), "m")
    c.rrect(90, 1560, 372, 58, 29, fill=rgb(C["teal50"])); c.text(90 + 186, 1597, "ILLUSTRATIVE EXAMPLE", "pjsb", 22, rgb(C["teal800"]), "m", 2)
    mascot.draw(c, 900, 700, 190, t=0.8, mood="wide", pal=mascot.LIGHT, mouth=("C", 0.5), mstyle=mascot.NARRATOR_MSTYLE)
    return c.done()


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    im = build(); im.save(out / "cover_1080x1920.png"); im.crop((0, 285, 1080, 1635)).save(out / "cover_grid_crop_1080x1350.png")
