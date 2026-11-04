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
    def remux(audio, name):
        subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out / "trackmyrupee_reel_001_silent.mp4"), "-i", str(audio),
                               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-shortest", "-movflags", "+faststart", str(out / name)])
    remux(out / "sfx_bed.wav", "trackmyrupee_reel_001_draft_sfx_only.mp4")
    from render import mix
    for vo_dir in sorted((ROOT / "assets" / "vo").glob("*")):
        if vo_dir.is_dir() and any(vo_dir.iterdir()):
            used, warns = mix.mix(out / "sfx_bed.wav", vo_dir, out / f"audio_mix_{vo_dir.name}.wav")
            [print("WARN", vo_dir.name, w) for w in warns]
            remux(out / f"audio_mix_{vo_dir.name}.wav", f"trackmyrupee_reel_001_draft_vo_{vo_dir.name}.mp4")
    exporter.write_folder(store.package("reel-001"))
    print("built", out)

if __name__ == "__main__":
    main()
