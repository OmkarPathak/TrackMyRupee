"""Reel 001 cover (calm paper style): 1080x1920 + 1080x1350 profile-grid crop."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render import mascot
from render.brand import C, Canvas, rgb

W, H, TILE, GAP, COLS = 1080, 1920, 80, 12, 10


def build():
    c = Canvas(W, H, bg=C["paper"])
    c.logo(90, 330, 56, True, word_color=C["ink900"], word_size=30)
    c.text(90, 540, "₹50,000 in.", "pjxb", 104, C["ink900"])
    c.text(90, 660, "Gone by the 20th.", "pjxb", 104, C["coral400"])
    c.text(90, 780, "Where?", "pjxb", 104, C["ink900"])
    gx, gy = (W - (COLS * TILE + (COLS - 1) * GAP)) // 2, 880
    for i in range(50):
        r, k = divmod(i, COLS); x, y = gx + k * (TILE + GAP), gy + r * (TILE + GAP)
        if i >= 42:
            c.rrect(x, y, TILE, TILE, 16, fill=rgb(C["coral200"])); c.text(x + TILE / 2, y + TILE / 2 + 17, "?", "pjxb", 52, rgb(C["coral400"]), "m")
        else:
            c.rrect(x, y, TILE, TILE, 16, fill=rgb(C["blue200"] if i < 23 else C["amber200"] if i < 37 else C["teal200"]))
    c.rrect(90, 1470, 372, 58, 29, fill=rgb(C["teal50"])); c.text(90 + 186, 1507, "ILLUSTRATIVE EXAMPLE", "pjsb", 22, rgb(C["teal800"]), "m", 2)
    mascot.draw(c, 880, 1590, 230, t=0.8, mood="wide", pal=mascot.LIGHT)
    return c.done()


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    im = build(); im.save(out / "cover_1080x1920.png"); im.crop((0, 285, W, 1635)).save(out / "cover_grid_crop_1080x1350.png")
