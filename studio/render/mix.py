"""Mix a voiceover onto the SFX bed.

Drop one clip per scene into studio/assets/vo/<lang>/scene_01.(wav|mp3|m4a) …
scene_09, or a single full-length 'vo.wav' (already timed to the 35s timeline).
Per-scene clips are placed at the scene VO start times below; SFX is ducked
under speech. Prints any clip that overruns its scene window."""
import subprocess, sys, wave
from pathlib import Path
import numpy as np

SR = 44100
STARTS = [0.1, 3.5, 7.0, 10.4, 14.4, 17.9, 22.3, 26.9, 31.6]
ENDS = [3.4, 7.0, 10.2, 14.2, 17.4, 22.0, 26.4, 31.0, 35.0]


def load(path):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4").astype(np.float64)


def read_wav(path):
    with wave.open(str(path)) as w:
        d = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
    return d.reshape(-1, 2).mean(1) if d.size % 2 == 0 else d


def mix(sfx_wav, vo_dir, out_wav):
    sfx = read_wav(sfx_wav); n = len(sfx)
    vo = np.zeros(n); used = False; warns = []
    vo_dir = Path(vo_dir)
    full = next((p for p in vo_dir.glob("vo.*")), None)
    if full:
        x = load(full); vo[:min(n, len(x))] += x[:n]; used = True
    else:
        for i in range(9):
            f = next(iter(sorted(vo_dir.glob(f"scene_{i+1:02d}.*"))), None)
            if not f:
                continue
            x = load(f); used = True
            at = int(STARTS[i] * SR); vo[at:at + len(x)] += x[:n - at]
            dur = len(x) / SR
            if STARTS[i] + dur > ENDS[i] + 0.15:
                warns.append(f"scene {i+1}: clip {dur:.2f}s overruns window by {STARTS[i] + dur - ENDS[i]:.2f}s")
    peak = np.abs(vo).max() or 1
    vo = vo / peak * 0.9 if used else vo
    env = np.convolve(np.abs(vo), np.ones(int(0.12 * SR)) / int(0.12 * SR), "same")
    duck = 1 - 0.55 * np.clip(env / 0.08, 0, 1)
    out = np.tanh(sfx * (duck if used else 1) + vo)
    pcm = (np.stack([out, out], 1) * 32767 * .92).astype("<i2")
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    return used, warns


if __name__ == "__main__":
    u, w = mix(*sys.argv[1:4]); print("vo used:", u); [print("WARN", x) for x in w]
