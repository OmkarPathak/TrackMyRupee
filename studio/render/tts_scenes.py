"""Generic per-scene Kokoro TTS for any reel package.
    python studio/render/tts_scenes.py <package_module> <out_dir> [voice] [lang]
Writes scene_XX.wav and durations.json (seconds per clip). No speed-up: windows adapt to the clip."""
import importlib, json, sys
from pathlib import Path
import soundfile as sf
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kokoro_onnx import Kokoro
from render.tts_kokoro import spoken

def main(pkg_mod, out, voice="af_heart", lang="en-us"):
    P = importlib.import_module(pkg_mod).PACKAGE
    k = Kokoro(str(ROOT / "assets/models/kokoro-v1.0.onnx"), str(ROOT / "assets/models/voices-v1.0.bin"))
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    durs = []
    for i, s in enumerate(P["scenes"]):
        y, sr = k.create(spoken(s["vo_en"]), voice=voice, speed=1.0, lang=lang)
        sf.write(out / f"scene_{i+1:02d}.wav", y, sr); durs.append(round(len(y) / sr, 3))
        print(f"scene {i+1}: {durs[-1]:.2f}s  | {s['vo_en'][:70]}")
    (out / "durations.json").write_text(json.dumps(durs))

if __name__ == "__main__":
    main(*sys.argv[1:])
