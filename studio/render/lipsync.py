"""Audio-driven mouth + body cues for the narrating mascot — no external binaries.
For each video frame: a mouth shape (Rhubarb-style letters) and openness, plus gentle
body energy (bounce/sway). Language-agnostic (works for English, Hindi, Hinglish):
it reads loudness, spectral brightness and noisiness of the voice, not words.
    X closed · B small/spread (ee, s) · C medium · D wide open · F small round (oo) · G thin (f/v/sh)
"""
import numpy as np
import soundfile as sf

SHAPES = {"X": (0.0, 1.0), "B": (0.28, 1.25), "C": (0.58, 1.0), "D": (1.0, 1.05), "E": (0.6, 0.7), "F": (0.45, 0.55), "G": (0.2, 1.1)}


def analyze(path, fps=60, offset=0.0, total=None):
    y, sr = sf.read(path, dtype="float32")
    if y.ndim > 1:
        y = y.mean(1)
    n_frames = int((total if total else len(y) / sr + offset) * fps) + 1
    win = int(0.032 * sr)
    out = []
    feats = []
    for i in range(n_frames):
        c = int((i / fps - offset) * sr)
        a, b = c - win // 2, c + win // 2
        if a < 0 or b > len(y):
            feats.append((0.0, 0.0, 0.0)); continue
        s = y[a:b] * np.hanning(b - a)
        rms = float(np.sqrt((s ** 2).mean()))
        spec = np.abs(np.fft.rfft(s)); freqs = np.fft.rfftfreq(len(s), 1 / sr)
        cen = float((spec * freqs).sum() / (spec.sum() + 1e-9))
        zcr = float(((y[a:b][:-1] * y[a:b][1:]) < 0).mean())
        feats.append((rms, cen, zcr))
    f = np.array(feats)
    rms = f[:, 0]
    ref = np.percentile(rms[rms > 0.003], 92) if (rms > 0.003).any() else 1.0
    lvl = np.clip(rms / ref, 0, 1.2)
    # smooth with fast attack / slower release
    sm = np.zeros_like(lvl); cur = 0.0
    for i, v in enumerate(lvl):
        cur = cur + (v - cur) * (0.65 if v > cur else 0.28); sm[i] = cur
    res = []
    for i in range(n_frames):
        l, (r, cen, zcr) = sm[i], feats[i]
        if l < 0.07:
            sh = "X"
        elif zcr > 0.22 and l < 0.5:
            sh = "G" if cen > 3500 else "B"           # fricatives: s / sh / f
        elif cen > 2300:
            sh = "B" if l < 0.6 else "C"             # bright vowels (ee, ay)
        elif cen < 900 and l > 0.25:
            sh = "F" if l < 0.7 else "E"             # dark/round vowels (oo, oh)
        else:
            sh = "D" if l > 0.72 else "C"
        res.append({"shape": sh, "open": float(min(1.0, l * SHAPES[sh][0] / max(SHAPES[sh][0], 1e-6))) if sh != "X" else 0.0, "energy": float(l)})
    return res
