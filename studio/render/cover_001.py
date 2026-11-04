"""Reel 001 cover: 1080x1920 (+ 1080x1350 profile-grid crop preview)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render.brand import C, Canvas, rgb

W, H = 1080, 1920
TILE, GAP, COLS = 80, 12, 10


def build():
    c = Canvas(W, H)
    c.glow(-200, 300, 800, 800, 400, C["teal800"], blur=130, alpha=0.4)
    c.logo(90, 330, 64, True, word_size=34)
    # headline, centred in the 1080x1350 safe crop (y 285-1635)
    c.text(90, 560, "Where does your", "serif", 96, C["cream"])
    c.text(90, 690, "₹50,000 salary", "serif", 150, C["cream"])
    c.text(90, 830, "disappear?", "serif", 150, C["coral200"])
    gx, gy = (W - (COLS * TILE + (COLS - 1) * GAP)) // 2, 960
    for i in range(50):
        r, k = divmod(i, COLS)
        x, y = gx + k * (TILE + GAP), gy + r * (TILE + GAP)
        if i >= 42:
            c.glow(x, y, TILE, TILE, 14, C["coral200"], blur=16, alpha=0.5)
            c.rrect(x, y, TILE, TILE, 14, fill=rgb(C["coral200"]))
            c.text(x + TILE / 2, y + TILE / 2 + 16, "?", "serif", 52, rgb(C["coral400"]), "m")
        else:
            col = C["blue200"] if i < 23 else C["amber200"] if i < 37 else C["teal200"]
            c.rrect(x, y, TILE, TILE, 14, fill=rgb(col, 0.9))
    c.text(90, 1520, "ILLUSTRATIVE EXAMPLE  ·  ₹8,000 UNACCOUNTED", "sansb", 26, rgb(C["cream"], 0.65), "l", 3)
    return c.done()


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    im = build(); im.save(out / "cover_1080x1920.png")
    im.crop((0, 285, W, 1635)).save(out / "cover_grid_crop_1080x1350.png")
