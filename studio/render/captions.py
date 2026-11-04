"""Burned-in subtitle pills (dark rounded pill, white text — as in the reference
style). Rendered as transparent PNGs and overlaid with ffmpeg so the base video
stays caption-free and any language can be applied.

    from render.captions import compose
    compose(video, audio, scenes, 'en'|'hinglish'|'hi', out_mp4)"""
import subprocess
from pathlib import Path
from render.brand import C, Canvas, rgb

W, H = 1080, 260
Y = 1370
from render.timeline import STARTS, ENDS, DUR
KEY = {"en": ("pjsb", "vo_en"), "hinglish": ("pjsb", "vo_hinglish"), "hi": ("deva", "vo_hi")}


def _wrap(c, text, key, size, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if c.text_width(t, key, size) <= maxw: cur = t
        else: lines.append(cur); cur = w
    return lines + ([cur] if cur else [])


def pill_png(text, key, path, size=40):
    c = Canvas(W, H, scale=2); c.img = c.img.convert("RGBA"); c.img.paste((0, 0, 0, 0), (0, 0, c.img.width, c.img.height))
    from PIL import ImageDraw; c.d = ImageDraw.Draw(c.img, "RGBA")
    lines = _wrap(c, text, key, size, 780)[:2]
    tw = max(c.text_width(l, key, size) for l in lines); lh = size * 1.35
    w, h = tw + 80, lh * len(lines) + 44
    x, y = (W - w) / 2, 20
    c.rrect(x, y, w, h, 34, fill=rgb(C["pill"], 0.96))
    for i, l in enumerate(lines):
        c.text(W / 2, y + 22 + size + i * lh - 6, l, key, size, (255, 255, 255, 255), "m")
    c.img.resize((W, H), __import__("PIL").Image.LANCZOS).save(path)


def compose(video, audio, scenes, lang, out, workdir):
    key, field = KEY[lang]; workdir = Path(workdir); workdir.mkdir(parents=True, exist_ok=True)
    pngs = []
    for i, s in enumerate(scenes):
        p = workdir / f"cap_{lang}_{i+1:02d}.png"; pill_png(s[field], key, p); pngs.append(p)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video)]
    for p in pngs:
        cmd += ["-loop", "1", "-framerate", "60", "-t", str(DUR), "-i", str(p)]
    cmd += ["-i", str(audio)]
    fc, last = [], "0:v"
    for i in range(len(pngs)):
        a, b = STARTS[i], ENDS[i]
        fc.append(f"[{i+1}:v]format=rgba,fade=t=in:st={a}:d=0.3:alpha=1,fade=t=out:st={b-0.3}:d=0.3:alpha=1[c{i}]")
        fc.append(f"[{last}][c{i}]overlay=0:{Y}:enable='between(t,{a},{b})'[v{i}]"); last = f"v{i}"
    cmd += ["-filter_complex", ";".join(fc), "-map", f"[{last}]", "-map", f"{len(pngs)+1}:a", "-c:v", "libx264", "-preset", "medium", "-crf", "17",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-shortest", "-movflags", "+faststart", str(out)]
    subprocess.check_call(cmd)
