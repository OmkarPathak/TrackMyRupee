"""Build every renderable asset for Reel 001 into studio/output/<slug>/.
   python studio/build_reel_001.py"""
import subprocess, sys, shutil
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from core import exporter, store
from packages.reel_001 import PACKAGE, SLUG

def main():
    store.init()
    out = exporter.OUT / SLUG; out.mkdir(parents=True, exist_ok=True)
    subprocess.check_call([sys.executable, str(ROOT / "render/cover_001.py"), str(out)])
    subprocess.check_call([sys.executable, str(ROOT / "render/sfx.py"), str(out / "sfx_bed.wav")])
    subprocess.check_call([sys.executable, str(ROOT / "render/reel_001.py"), str(out / "trackmyrupee_reel_001_silent.mp4")])
    subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out / "trackmyrupee_reel_001_silent.mp4"), "-i", str(out / "sfx_bed.wav"),
                           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-shortest", "-movflags", "+faststart", str(out / "trackmyrupee_reel_001_draft.mp4")])
    exporter.write_folder(store.package("reel-001"))
    print("built", out)

if __name__ == "__main__":
    main()
