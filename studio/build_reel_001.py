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
    base = out / "trackmyrupee_reel_001_silent.mp4"          # 1080x1920, 60fps, NO audio, NO captions
    subprocess.check_call([sys.executable, str(ROOT / "render/reel_001.py"), str(base)])
    from render import mix, captions
    scenes = store.package("reel-001")["scenes"]
    for f in out.glob("trackmyrupee_reel_001_draft*"):
        f.unlink()
    def remux(audio, name):
        subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-i", str(base), "-i", str(audio), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                               "-ar", "44100", "-shortest", "-movflags", "+faststart", str(out / name)])
    remux(out / "sfx_bed.wav", "trackmyrupee_reel_001_draft_sfx_only.mp4")
    langs = __import__("os").environ.get("VO_DIRS", "en").split(",")   # English only for now; e.g. VO_DIRS=en,hi for more
    for vo_dir in [ROOT / "assets" / "vo" / n for n in langs]:
        if vo_dir.is_dir() and any(vo_dir.iterdir()):
            used, warns = mix.mix(out / "sfx_bed.wav", vo_dir, out / f"audio_mix_{vo_dir.name}.wav")
            [print("WARN", vo_dir.name, w) for w in warns]
            lang = "hi" if vo_dir.name.startswith("hi") else "en"
            captions.compose(base, out / f"audio_mix_{vo_dir.name}.wav", scenes, lang, out / f"trackmyrupee_reel_001_draft_vo_{vo_dir.name}.mp4", out / "_caption_pngs")
            remux(out / f"audio_mix_{vo_dir.name}.wav", f"trackmyrupee_reel_001_clean_nocaptions_vo_{vo_dir.name}.mp4")
    exporter.write_folder(store.package("reel-001"))
    print("built", out)

if __name__ == "__main__":
    main()
