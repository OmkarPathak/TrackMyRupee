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
    c.text(90, 540, "Your salary went up.", "pjxb", 80, C["ink900"])
    c.text(90, 650, "Are you actually", "pjxb", 100, C["coral400"]); c.text(90, 760, "richer?", "pjxb", 100, C["coral400"])
    for k, (y, lab, val, col) in enumerate(((880, "Salary", "+₹10,000", C["teal400"]), (1030, "Net worth", "?", C["coral400"]))):
        c.card(90, y, 900, 124, 30); c.check(150, y + 62, 24, C["teal200"] if k == 0 else C["coral200"])
        c.text(200, y + 78, lab, "pjxb", 42, rgb(C["ink900"])); c.text(950, y + (84 if k == 0 else 90), val, "pjxb", 46 if k == 0 else 72, rgb(col), "r")
    mascot.draw(c, 840, 1520, 250, t=0.8, mood="wide", pal=mascot.LIGHT, mouth=("C", 0.5), mstyle=mascot.NARRATOR_MSTYLE)
    return c.done()


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    im = build(); im.save(out / "cover_1080x1920.png"); im.crop((0, 285, 1080, 1635)).save(out / "cover_grid_crop_1080x1350.png")
