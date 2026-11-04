"""Proof: the Rupee Minis mascot narrating the Reel 1 hook, 3 mouth designs side by side,
English then Hindi. Mouth/body motion is driven only by the audio (lipsync.py).
    python studio/render/proof_avatar.py out.mp4"""
import math, subprocess, sys
from multiprocessing import Pool
from pathlib import Path
import numpy as np, soundfile as sf
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render import mascot, lipsync
from render.brand import C, Canvas, clamp

ROOT = Path(__file__).resolve().parents[1]
W, H, FPS = 1080, 1920, 60
EN_AT, HI_AT = 0.6, 5.0
LINES = [("English", "Fifty thousand, credited. By the twentieth… gone. Where?", "pjsb", EN_AT, EN_AT + 3.25),
         ("हिन्दी", "पचास हज़ार आए। बीस तारीख़ तक… ग़ायब। कहाँ?", "deva", HI_AT, HI_AT + 3.4)]
DUR = HI_AT + 3.4 + 0.9
STYLES = [(1, "1 · Line"), (2, "2 · Jaw"), (3, "3 · Snout")]
_cues = None


def cues():
    global _cues
    if _cues is None:
        sr = 24000; mix = np.zeros(int(DUR * sr), dtype="float32")
        for f, at in (("hook_en.wav", EN_AT), ("hook_hi.wav", HI_AT)):
            y, s = sf.read(ROOT / "proof" / f, dtype="float32"); assert s == sr
            mix[int(at * sr):int(at * sr) + len(y)] += y
        sf.write(ROOT / "proof" / "narration.wav", mix, sr)
        _cues = lipsync.analyze(ROOT / "proof" / "narration.wav", FPS, 0.0, DUR)
    return _cues


def frame(n):
    t = n / FPS; cu = cues(); cu = cu[min(n, len(cu) - 1)]
    c = Canvas(W, H, bg=C["paper"])
    c.text(72, 250, "Narrating mascot", "pjxb", 72, C["ink900"]); c.text(72, 320, "Mouth design test · pick 1, 2 or 3", "pjsb", 34, C["coral400"])
    speaking = cu["shape"] != "X" or cu["energy"] > 0.05
    for i, (st, label) in enumerate(STYLES):
        cx = 190 + i * 350
        bl = 1.0 if (t % 3.3) < 0.12 else 0.0
        squash = 0.05 * cu["energy"]; rot = math.sin(t * 2.3 + i) * 1.5 + cu["energy"] * 2.0 * math.sin(t * 9)
        mood = "talk" if (EN_AT - 0.2 < t < HI_AT + 3.6) else "happy"
        mascot.draw(c, cx, 880, 340, t=t + i * .3, mood=mood, rot=rot, squash=squash, pal=mascot.LIGHT, wiggle=0.6,
                    mouth=(cu["shape"], cu["open"]), mstyle=st, blink=bl)
        c.text(cx, 960, label, "pjxb", 34, C["ink900"], "m")
    for name, text, key, a, b in LINES:
        if a - 0.2 <= t < b + 0.5:
            al = clamp((t - (a - 0.2)) / 0.25) * (1 - clamp((t - b) / 0.4))
            tw = c.text_width(text, key, 44)
            col = (43, 43, 43, int(245 * al))
            c.rrect(W / 2 - tw / 2 - 44, 1240, tw + 88, 100, 34, fill=col)
            c.text(W / 2, 1305, text, key, 44, (255, 255, 255, int(255 * al)), "m")
            c.text(W / 2, 1190, name.upper() if name == "English" else name, "pjsb", 26, (138, 138, 134, int(255 * al)), "m", 3)
    c.text(W / 2, 1600, "Audio-driven: no external lip-sync tool · same pipeline as the Reel", "pj", 26, C["grey"], "m")
    return c.done().tobytes()


def render(out):
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True); cues()
    tmp = out.with_suffix(".silent.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an",
           "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", str(tmp)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE); total = int(DUR * FPS)
    with Pool(4) as pool:
        for i, buf in enumerate(pool.imap(frame, range(total), chunksize=6)):
            p.stdin.write(buf)
            if i % 120 == 0: print(f"  frame {i}/{total}", flush=True)
    p.stdin.close(); p.wait()
    subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-i", str(ROOT / "proof" / "narration.wav"), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                           "-shortest", "-movflags", "+faststart", str(out)]); tmp.unlink()


if __name__ == "__main__":
    render(sys.argv[1])
