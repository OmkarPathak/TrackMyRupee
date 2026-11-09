"""Build Reel 002 (library idea reel-006: why net worth matters).
Prereq (once, or when the script changes):  python studio/render/tts_scenes.py packages.reel_002 studio/assets/vo/reel_002 af_heart en-us
    python studio/build_reel_002.py"""
import json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from core import exporter, store
from packages.reel_002 import PACKAGE, SLUG
from render import captions, lipsync, mix, timeline_002 as T


def main():
    store.init()
    ideas = store.read("ideas")
    for i in ideas:
        if i["id"] == "reel-006":
            i["title"] = PACKAGE["title"]; i["duration"] = f"{round(T.DUR)}s"
            i["concept"] = "Why seeing your net worth matters (own − owe), a worked illustrative example, why it matters, one-place tracking with a tiny daily habit. Mascot narrates."
    store.write("ideas", ideas)
    out = exporter.OUT / SLUG; out.mkdir(parents=True, exist_ok=True)
    vo = ROOT / "assets" / "vo" / "reel_002"
    subprocess.check_call([sys.executable, str(ROOT / "render/cover_002.py"), str(out)])
    subprocess.check_call([sys.executable, str(ROOT / "render/sfx_002.py"), str(out / "sfx_bed.wav")])
    used, warns = mix.mix(out / "sfx_bed.wav", vo, out / "audio_mix_en.wav", T.STARTS, T.ENDS, vo_only_wav=vo / "narration.wav")
    [print("WARN", w) for w in warns]
    (vo / "cues.json").write_text(json.dumps(lipsync.analyze(vo / "narration.wav", 60, 0.0, T.DUR)))   # mouth cues from the narration
    base = out / "trackmyrupee_reel_006_silent.mp4"
    subprocess.check_call([sys.executable, str(ROOT / "render/reel_002.py"), str(base)])
    for f in out.glob("trackmyrupee_reel_006_draft*"):
        f.unlink()
    scenes = [{**s} for s in PACKAGE["scenes"]]
    captions.compose(base, out / "audio_mix_en.wav", scenes, "en", out / "trackmyrupee_reel_006_draft_vo_en.mp4", out / "_caption_pngs", T.STARTS, T.ENDS, T.DUR, prefix="r6")
    subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-i", str(base), "-i", str(out / "audio_mix_en.wav"), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                           "-ar", "44100", "-shortest", "-movflags", "+faststart", str(out / "trackmyrupee_reel_006_clean_nocaptions_vo_en.mp4")])
    exporter.write_folder(store.package("reel-006"))
    print("built", out)


if __name__ == "__main__":
    main()
