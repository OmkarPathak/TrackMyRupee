"""Reel 003 cover (calm paper): 1080x1920 + 1080x1350 crop."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render import mascot
from render.brand import C, Canvas, rgb


def build():
    W, H = 1080, 1920
    c = Canvas(W, H, bg=C["paper"])
    c.logo(90, 330, 56, True, word_color=C["ink900"], word_size=30)
    c.text(90, 640, "₹99", "pjxb", 270, rgb(C["ink900"])); c.text(90 + c.text_width("₹99", "pjxb", 270) + 36, 640, "× 5", "pjxb", 270, rgb(C["coral400"]))
    c.text(90, 920, "= ?", "pjxb", 270, rgb(C["ink900"]))
    c.text(90, 1040, "The subscription trap.", "pjxb", 76, rgb(C["coral400"]))
    c.rrect(90, 1130, 372, 58, 29, fill=rgb(C["teal50"])); c.text(90 + 186, 1167, "ILLUSTRATIVE EXAMPLE", "pjsb", 22, rgb(C["teal800"]), "m", 2)
    mascot.draw(c, 880, 1560, 250, t=0.8, mood="wide", pal=mascot.LIGHT, mouth=("C", 0.5), mstyle=mascot.NARRATOR_MSTYLE)
    return c.done()


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    im = build(); im.save(out / "cover_1080x1920.png"); im.crop((0, 285, 1080, 1635)).save(out / "cover_grid_crop_1080x1350.png")
