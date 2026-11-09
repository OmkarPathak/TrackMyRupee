"""Offline neural TTS with Kokoro (kokoro-onnx). Generates one clip per scene so
timing can be checked against the scene windows.

    python studio/render/tts_kokoro.py en  af_heart  en-us
    python studio/render/tts_kokoro.py hi  hf_alpha  hi
Model files (not committed): studio/assets/models/kokoro-v1.0.onnx + voices-v1.0.bin
(download: github.com/thewh1teagle/kokoro-onnx releases, tag model-files-v1.0)."""
import sys
from pathlib import Path
import numpy as np, soundfile as sf
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kokoro_onnx import Kokoro
from packages.reel_001 import PACKAGE
from render.mix import ENDS, STARTS
import re

def spoken(t):
    """Spell out initialisms the TTS would otherwise read as words (EMI -> 'Emmy')."""
    for a, b in ((r'\bEMIs?\b', 'E M I'), (r'\bFDs\b', 'F Ds'), (r'\bFD\b', 'F D'), (r'\bEPF\b', 'E P F'), (r'\bPPF\b', 'P P F'), (r'\bNPS\b', 'N P S'), (r'\bSMS\b', 'S M S'), (r'\bETFs?\b', 'E T F'), (r'\bSIPs?\b', 'S I P'), (r'\bOTT\b', 'O T T'), (r'\bUPI\b', 'U P I'), (r'\b(\d+|nine) ?pm\b', 'nine P M'), (r'\bPM\b', 'P M')):
        t = re.sub(a, b, t)
    return t

def main(tag, voice, lang, key):
    k = Kokoro(str(ROOT / "assets/models/kokoro-v1.0.onnx"), str(ROOT / "assets/models/voices-v1.0.bin"))
    out = ROOT / "assets/vo" / tag; out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*"): f.unlink()
    lines = [x.strip() for x in PACKAGE["voiceover"][key].split("\n\n") if x.strip()]
    assert len(lines) == len(STARTS), (len(lines), len(STARTS))
    for i, text in enumerate(lines):
        window = ENDS[i] - STARTS[i]
        speed = 1.0
        for _ in range(3):
            y, sr = k.create(spoken(text) if lang != 'hi' else text, voice=voice, speed=speed, lang=lang)
            dur = len(y) / sr
            if dur <= window or speed >= 1.3: break
            speed = min(1.3, speed * dur / window * 1.03)
        sf.write(out / f"scene_{i+1:02d}.wav", y, sr)
        print(f"scene {i+1}: {dur:.2f}s / window {window:.1f}s (speed {speed:.2f}){'  OVERRUN' if dur > window + .15 else ''}")

if __name__ == "__main__":
    tag, voice, lang = sys.argv[1:4]
    main(tag, voice, lang, "hindi" if lang == "hi" else "en")
