"""Prepare a human voice recording for a reel: clean, level, split into scene clips.

    python studio/render/voice_prep.py <source.m4a> <out_dir> <boundaries.json>
boundaries.json = [[start, end], ...] one speech interval (seconds in the source) per scene.
Writes scene_XX.wav (mono 44.1k, trimmed with ~0.12s lead / 0.25s tail), durations.json and
segments.json (voiced intervals per clip, used to time on-screen beats to the actual speech)."""
import json, subprocess, sys
from pathlib import Path
import numpy as np, soundfile as sf

CLEAN = "highpass=f=80,afftdn=nr=12:nf=-50:tn=1,loudnorm=I=-16:TP=-1.5:LRA=7"


def voiced(y, sr, thr_db=-42, min_gap=0.16, min_len=0.08):
    win = int(0.02 * sr); n = len(y) // win
    r = np.array([20 * np.log10(np.sqrt((y[i * win:(i + 1) * win] ** 2).mean()) + 1e-9) for i in range(n)])
    on = r > thr_db; segs = []; s = None
    for i, v in enumerate(on):
        if v and s is None: s = i
        if not v and s is not None:
            segs.append([s * 0.02, i * 0.02]); s = None
    if s is not None: segs.append([s * 0.02, n * 0.02])
    merged = []
    for a, b in segs:
        if merged and a - merged[-1][1] < min_gap: merged[-1][1] = b
        else: merged.append([a, b])
    return [[round(a, 3), round(b, 3)] for a, b in merged if b - a >= min_len]


def main(src, out, bounds):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    tmp = out / "_clean.wav"
    subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "44100", "-af", CLEAN, str(tmp)])
    y, sr = sf.read(tmp, dtype="float32")
    durs, segs_all = [], []
    for i, (a, b) in enumerate(json.loads(Path(bounds).read_text()), 1):
        a0, b0 = max(0, a - 0.12), min(len(y) / sr, b + 0.25)
        clip = y[int(a0 * sr):int(b0 * sr)].copy()
        fade = int(0.02 * sr); clip[:fade] *= np.linspace(0, 1, fade); clip[-fade:] *= np.linspace(1, 0, fade)
        sf.write(out / f"scene_{i:02d}.wav", clip, sr)
        durs.append(round(len(clip) / sr, 3)); segs_all.append(voiced(clip, sr))
        print(f"scene {i}: {durs[-1]:.2f}s, voiced segments: {segs_all[-1]}")
    (out / "durations.json").write_text(json.dumps(durs)); (out / "segments.json").write_text(json.dumps(segs_all))
    tmp.unlink()


if __name__ == "__main__":
    main(*sys.argv[1:4])
