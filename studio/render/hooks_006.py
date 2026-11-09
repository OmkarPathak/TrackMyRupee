"""Hook concept previews for Reel 006 (net worth). Three ~6–9s hooks, narrated by the mascot.
    python studio/render/hooks_006.py <out_dir>"""
import json, math, subprocess, sys
from multiprocessing import Pool
from pathlib import Path
import numpy as np, soundfile as sf
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render import captions, lipsync, mascot
from render.brand import C, Canvas, clamp, fmt_inr, prog, rgb
from render.reel_001 import env, io, lerp, mix, pill, q, rise, txt

ROOT = Path(__file__).resolve().parents[1]
VO = ROOT / "assets" / "vo" / "hooks_006"
W, H, FPS, LM = 1080, 1920, 60, 72
META = json.loads((VO / "meta.json").read_text())
CUR = {"v": "A", "cues": None, "dur": 0}


def beat(v, phrase, lead=0.0):
    m = META[v]; t = m["text"]; k = t.find(phrase)
    if v == "B":                                   # two segments with a silent countdown between
        if phrase in m["seg2"]:
            return m["seg2_start"] + m["seg2"].find(phrase) / len(m["seg2"]) * (m["dur"] - m["seg2_start"]) - lead
        return 0.1 + m["seg1"].find(phrase) / len(m["seg1"]) * m["seg1_end"] - lead
    return 0.1 + (k / len(t)) * m["dur"] - lead


def narrator(c, t, n, v, cx=925, yb=345, sz=190, mood="talk", rot=0.0, coin_y=None, front=True):
    cu = CUR["cues"][min(n, len(CUR["cues"]) - 1)]; en = cu["energy"]
    if t < 0.45:
        sz *= max(0.01, min(1.06, q(prog(t, 0.02, 0.4)) * 1.03))
    mascot.draw(c, cx, yb, sz, t=t, mood=mood, rot=rot + math.sin(t * 2.1) * 1.4 + en * 2.0 * math.sin(t * 9), squash=0.05 * en, pal=mascot.LIGHT, wiggle=0.6,
                mouth=(cu["shape"], cu["open"]), mstyle=mascot.NARRATOR_MSTYLE, blink=1.0 if (t % 3.3) < 0.12 else 0.0, coin_y=coin_y, coin_front=front)


def frame_A(n):
    t = n / FPS; c = Canvas(W, H, bg=C["paper"]); c.logo(LM, 110, 46, True, word_color=C["ink900"], word_size=26)
    ts, tf, tw, tb = beat("A", "Same salary") - 0.05, beat("A", "Five years", 0.1), beat("A", "Why?", 0.1), beat("A", "Because", 0.1)
    old = 1 - io(prog(t, tb - 0.1, 0.25)); new = q(prog(t, tb + 0.1, 0.35))
    a, dy = rise(t, ts); txt(c, LM, 330 + dy, "Same salary.", "pjxb", 84, C["ink900"], a * old)
    a, dy = rise(t, tf); txt(c, LM, 430 + dy, "5 years later.", "pjxb", 84, C["coral400"], a * old)
    a, dy = rise(t, tw, 0.3); txt(c, LM, 540 + dy, "Why?", "pjxb", int(110 * (1 + .2 * (1 - q(prog(t, tw + .1, .3))))), C["ink900"], a * old)
    a, dy = rise(t, tb + 0.1); txt(c, LM, 330 + dy, "Salary isn't", "pjxb", 84, C["ink900"], new); txt(c, LM, 430 + dy, "the score.", "pjxb", 84, C["ink900"], new)
    a2, dy2 = rise(t, tb + 0.7); txt(c, LM, 540 + dy2, "Net worth is.", "pjxb", 100, C["coral400"], a2)
    dim = 1 - 0.55 * new
    for k, (nm, x0) in enumerate((("Riya", LM), ("Aman", W / 2 + 14))):
        ca = q(prog(t, ts + 0.25 + k * 0.15, 0.4)) * dim
        if ca > 0.01:
            y0 = 640 + (1 - q(prog(t, ts + 0.25 + k * 0.15, 0.4))) * 24
            c.card(x0, y0, W / 2 - LM - 14, 132, 28, alpha=ca)
            c.ellipse(x0 + 56, y0 + 66, 34, fill=rgb(C["teal200"] if k == 0 else C["blue200"], ca)); txt(c, x0 + 56, y0 + 80, nm[0], "pjxb", 38, "#FFFFFF", ca, "m")
            txt(c, x0 + 108, y0 + 62, nm, "pjxb", 34, C["ink900"], ca); txt(c, x0 + 108, y0 + 104, "₹80,000/month", "pjsb", 26, C["grey"], ca)
    base = 1390; sc = 400 / 1400000
    for k, (val, x0, col, lab) in enumerate(((1400000, 200, C["teal400"], "Riya"), (400000, 600, C["coral200"], "Aman"))):
        g = q(prog(t, tf + 0.3 + k * 0.15, 1.1)) * dim
        if g > 0.01:
            h = val * sc * q(prog(t, tf + 0.3 + k * 0.15, 1.1))
            c.rrect(x0, base - h, 280, h, 28, fill=rgb(col, dim)); txt(c, x0 + 140, base + 50, lab, "pjxb", 32, C["ink900"], g, "m")
            if prog(t, tf + 1.2, 0.3) > 0:
                txt(c, x0 + 140, base - h - 18, fmt_inr(val), "pjxb", 44, C["ink900"], q(prog(t, tf + 1.2, 0.3)) * dim, "m")
    gp = q(prog(t, beat("A", "ten lakh") - 0.1, 0.4)) * dim
    if gp > 0.01:
        pill(c, W / 2, 880, "₹10,00,000 gap", "#FFFFFF", C["coral400"], 30, gp)
    pill(c, W / 2, 1600, "ILLUSTRATIVE EXAMPLE", C["teal800"], C["teal50"], 24, q(prog(t, ts + 0.3, 0.4)))
    narrator(c, t, n, "A", mood="wide" if tw - 0.1 < t < tb else "talk")
    return c


def frame_B(n):
    t = n / FPS; c = Canvas(W, H, bg=C["paper"]); c.logo(LM, 110, 46, True, word_color=C["ink900"], word_size=26)
    m = META["B"]; cd0, cd1 = m["seg1_end"] + 0.1, m["seg2_start"] - 0.1
    a, dy = rise(t, 0.1); txt(c, LM, 330 + dy, "Quick test.", "pjxb", 84, C["ink900"], a)
    a, dy = rise(t, beat("B", "What's your", 0.1)); txt(c, LM, 430 + dy, "What's your", "pjxb", 72, C["coral400"], a); txt(c, LM, 515 + dy, "net worth?", "pjxb", 100, C["coral400"], a)
    cx, cy, R = W / 2, 1010, 250
    ra = q(prog(t, beat("B", "You've got", 0.1), 0.4))
    if ra > 0.01:
        c.ellipse(cx, cy, R + 14, fill=rgb(C["tile"], ra)); c.ellipse(cx, cy, R - 26, fill=rgb(C["paper"], ra))
        p = clamp((t - cd0) / (cd1 - cd0)); remain = 1 - p
        steps = 90
        pts = [(cx + (R - 6) * math.sin(2 * math.pi * remain * i / steps), cy - (R - 6) * math.cos(2 * math.pi * remain * i / steps)) for i in range(steps + 1)]
        if remain > 0.01:
            c.line(pts, rgb(C["teal400"] if remain > 0.34 else C["coral400"], ra), 26)
        num = 3 - int(min(2.999, max(0.0, (t - cd0) / (cd1 - cd0) * 3)))
        if t < cd0:
            num = 3
        pop = 1 + 0.12 * (1 - ((t - cd0) / ((cd1 - cd0) / 3)) % 1.0) if cd0 <= t < cd1 else 1
        if t < cd1:
            txt(c, cx, cy + 105, str(num), "pjxb", int(300 * pop), C["ink900"], ra, "m")
        else:
            k = q(prog(t, cd1, 0.3)); txt(c, cx, cy + 22, "Time's up.", "pjxb", int(78 * (1 + .25 * (1 - k))), C["coral400"], k, "m")
    a, dy = rise(t, beat("B", "If you hesitated", 0.05)); txt(c, W / 2, 1400 + dy, "Hesitated?", "pjxb", 60, C["ink900"], a, "m"); txt(c, W / 2, 1470 + dy, "This one's for you.", "pjsb", 40, C["coral400"], a, "m")
    narrator(c, t, n, "B", mood="wide" if cd0 <= t < cd1 + .5 else "talk")
    return c


def frame_C(n):
    t = n / FPS; c = Canvas(W, H, bg=C["paper"]); c.logo(LM, 110, 46, True, word_color=C["ink900"], word_size=26)
    t5, tq = beat("C", "five thousand", 0.1), beat("C", "But do you", 0.1)
    b1 = env(t, 0.5, tq - 0.05, 0.3, 0.22); b2 = env(t, tq + 0.05, 99, 0.3, 0.2)
    for e_, y in ((b1, 330), (b2, 330)):
        if e_ > 0.01:
            c.card(LM, y + (1 - e_) * 20, W - 2 * LM, 400, 40, alpha=e_)
    if b1 > 0.01:
        txt(c, W / 2, 440, "I'm a piggy bank.", "pjxb", 62, C["ink900"], b1, "m")
        txt(c, W / 2, 510, "I know exactly what's inside me:", "pjsb", 38, C["grey"], b1, "m")
        k = q(prog(t, t5 - 0.4, 0.9)); txt(c, W / 2, 680, fmt_inr(int(5000 * k)), "pjxb", 150, C["coral400"], b1 * q(prog(t, t5 - 0.6, 0.3)), "m")
    if b2 > 0.01:
        txt(c, W / 2, 455, "But do you know", "pjxb", 58, C["ink900"], b2, "m"); txt(c, W / 2, 535, "what YOU'RE", "pjxb", 58, C["ink900"], b2, "m")
        k = 1 + 0.2 * (1 - q(prog(t, tq + 0.3, 0.3))); txt(c, W / 2, 690, "worth?", "pjxb", int(118 * k), C["coral400"], b2, "m")
    coin_y, front = None, True
    if t5 - 0.3 <= t < t5 + 0.6:
        coin_y = 32 + 30 * io(prog(t, t5 - 0.3, 0.45)); front = coin_y < 40
    mood = "wide" if t >= tq else "talk"
    narrator(c, t, n, "C", cx=540, yb=1400, sz=470, mood=mood, coin_y=coin_y, front=front)
    return c


FRAMES = {"A": frame_A, "B": frame_B, "C": frame_C}


def frame_bytes(n):
    c = FRAMES[CUR["v"]](n); t = n / FPS
    pill(c, W / 2, 56, f"HOOK {CUR['v']} · preview", C["cream"], C["pill"], 22, 1.0)
    return c.done().tobytes()


def sfx_for(v, dur):
    from render.sfx import SR, ding, pop, thud, tick, whoosh
    out = np.zeros(int(SR * dur) + SR)
    def put(x, at, g=1.0):
        i = max(0, int(at * SR)); out[i:i + len(x)] += x[:len(out) - i] * g
    put(ding(), 0.05, .7)
    if v == "A":
        put(pop(520), beat("A", "Same salary"), .4); put(whoosh(.8), beat("A", "Five years", .1), .35); put(thud(), beat("A", "ten lakh", .1), .6); put(thud(), beat("A", "Because"), .5)
    if v == "B":
        m = META["B"]
        for i in range(3): put(tick(900 - 80 * i), m["seg1_end"] + 0.1 + i * 3.0 / 3.0, .7)
        put(thud(), m["seg2_start"] - 0.1, .8)
    if v == "C":
        put(pop(360), 0.05, .5); put(pop(480), beat("C", "five thousand", .4), .5); put(thud(), beat("C", "But do you", .1), .5)
    return out[:int(SR * dur)], SR


def build(v, outdir):
    CUR["v"] = v; dur = META[v]["dur"] + 0.7
    audio = VO / f"{v}.wav"
    y, sr = sf.read(audio, dtype="float32"); sfx, ssr = sfx_for(v, dur)
    mix_ = np.zeros(int(dur * sr)); mix_[:len(y)] += y * 0.9
    sfx_r = sfx[:int(dur * ssr)]
    if ssr != sr:
        idx = (np.arange(int(dur * sr)) * ssr / sr).astype(int).clip(0, len(sfx_r) - 1); sfx_r = sfx_r[idx]
    mix_ = np.tanh(mix_ + 0.5 * sfx_r[:len(mix_)])
    wav = outdir / f"hook_{v}_audio.wav"; sf.write(wav, mix_.astype("float32"), sr)
    CUR["cues"] = lipsync.analyze(audio, FPS, 0.0, dur); CUR["dur"] = dur
    silent = outdir / f"hook_{v}_silent.mp4"; total = int(dur * FPS)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an",
           "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", str(silent)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with Pool(4) as pool:
        for buf in pool.imap(frame_bytes, range(total), chunksize=6):
            p.stdin.write(buf)
    p.stdin.close(); p.wait()
    m = META[v]
    if v == "B":
        scenes = [{"vo_en": m["seg1"]}, {"vo_en": m["seg2"]}]; st = [0.1, m["seg2_start"]]; en = [m["seg1_end"] + 0.1, dur]
    else:
        scenes = [{"vo_en": m["text"]}]; st = [0.1]; en = [dur]
    final = outdir / f"hook_{v}.mp4"
    captions.compose(silent, wav, scenes, "en", final, outdir / "_caps", st, en, dur, prefix=f"h{v}")
    silent.unlink(); return final


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    vs = sys.argv[2].split(",") if len(sys.argv) > 2 else ["A", "B", "C"]
    files = [build(v, out) for v in vs]
    print("built", [f.name for f in files])
