"""Build carousel 003: python studio/build_carousel_003.py"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from PIL import Image
from core import exporter, store
from packages.carousel_003 import PACKAGE, SLIDES, SLUG
from render import carousel_rupee as R


def main():
    store.init()
    out = exporter.OUT / SLUG; out.mkdir(parents=True, exist_ok=True)
    ims = R.render_all(SLIDES, out)
    if R.WARN:
        print("WARNINGS:", *R.WARN, sep="\n  ")
    sheet = Image.new("RGB", (4 * 540, 2 * 675), "white")
    for i, im in enumerate(ims):
        sheet.paste(im.convert("RGB").resize((540, 675)), ((i % 4) * 540, (i // 4) * 675))
    sheet.save(out / "contact_sheet.png")
    exporter.write_carousel_folder(store.package("carousel-003"))
    print("built", out)


if __name__ == "__main__":
    main()
