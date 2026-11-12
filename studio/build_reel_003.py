"""Build Reel 003 (library idea reel-003: the ₹99 subscription trap).
Prereq: python studio/render/tts_scenes.py packages.reel_003 studio/assets/vo/reel_003 af_heart en-us
    python studio/build_reel_003.py"""
import json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from core import exporter, store
from packages.reel_003 import PACKAGE, SLUG
from render import captions, lipsync, mix, timeline_003 as T


def main():
    store.init()
    out = exporter.OUT / SLUG; out.mkdir(parents=True, exist_ok=True)
    vo = T.VO_DIR
    sfx_ = "user" if T.VOICE == "user" else "en"
    subprocess.check_call([sys.executable, str(ROOT / "render/cover_003.py"), str(out)])
    subprocess.check_call([sys.executable, str(ROOT / "render/sfx_003.py"), str(out / "sfx_bed.wav")])
    used, warns = mix.mix(out / "sfx_bed.wav", vo, out / f"audio_mix_{sfx_}.wav", T.STARTS, T.ENDS, vo_only_wav=vo / "narration.wav")
    [print("WARN", w) for w in warns]
    (vo / "cues.json").write_text(json.dumps(lipsync.analyze(vo / "narration.wav", 60, 0.0, T.DUR)))
    base = out / f"trackmyrupee_reel_003_silent_{sfx_}.mp4"
    subprocess.check_call([sys.executable, str(ROOT / "render/reel_003.py"), str(base)])
    for f in out.glob(f"trackmyrupee_reel_003_draft_vo_{sfx_}*"):
        f.unlink()
    captions.compose(base, out / f"audio_mix_{sfx_}.wav", [{**s} for s in PACKAGE["scenes"]], "en", out / f"trackmyrupee_reel_003_draft_vo_{sfx_}.mp4", out / "_caption_pngs", T.STARTS, T.ENDS, T.DUR, prefix="r3")
    subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-i", str(base), "-i", str(out / f"audio_mix_{sfx_}.wav"), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                           "-shortest", "-movflags", "+faststart", str(out / f"trackmyrupee_reel_003_clean_nocaptions_vo_{sfx_}.mp4")])
    exporter.write_folder(store.package("reel-003"))
    print("built", out)


if __name__ == "__main__":
    main()
