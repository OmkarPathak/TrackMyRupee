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


def chunks(text, key, size=40):
    """Split a caption into pill-sized chunks (<=2 lines), breaking only at sentence ends."""
    import re
    c = Canvas(W, H, scale=1)
    sents = [x.strip() for x in re.split(r"(?<=[.!?…])\s+", text) if x.strip()]
    out, cur = [], ""
    for sn in sents:
        trial = (cur + " " + sn).strip()
        if cur and len(_wrap(c, trial, key, size, 780)) > 2:
            out.append(cur); cur = sn
        else:
            cur = trial
    if cur:
        out.append(cur)
    return out


def compose(video, audio, scenes, lang, out, workdir, starts=None, ends=None, dur=None, prefix="cap"):
    STARTS_, ENDS_, DUR_ = (starts or STARTS), (ends or ENDS), (dur or DUR)
    key, field = KEY[lang]; workdir = Path(workdir); workdir.mkdir(parents=True, exist_ok=True)
    items = []   # (png, start, end)
    for i, s in enumerate(scenes):
        parts = chunks(s[field], key)
        a, b = STARTS_[i], ENDS_[i]
        tot = sum(len(p) for p in parts) or 1
        t = a
        for j, part in enumerate(parts):
            d = (b - a) * len(part) / tot
            p = workdir / f"{prefix}_{lang}_{i+1:02d}_{j+1}.png"; pill_png(part, key, p)
            items.append((p, t, t + d)); t += d
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video)]
    for p, _, _ in items:
        cmd += ["-loop", "1", "-framerate", "60", "-t", str(DUR_), "-i", str(p)]
    cmd += ["-i", str(audio)]
    fc, last = [], "0:v"
    for i, (_, a, b) in enumerate(items):
        fc.append(f"[{i+1}:v]format=rgba,fade=t=in:st={a:.2f}:d=0.15:alpha=1,fade=t=out:st={b-0.15:.2f}:d=0.15:alpha=1[c{i}]")
        fc.append(f"[{last}][c{i}]overlay=0:{Y}:enable='between(t,{a:.2f},{b:.2f})'[v{i}]"); last = f"v{i}"
    cmd += ["-filter_complex", ";".join(fc), "-map", f"[{last}]", "-map", f"{len(items)+1}:a", "-c:v", "libx264", "-preset", "medium", "-crf", "17",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-shortest", "-movflags", "+faststart", str(out)]
    subprocess.check_call(cmd)
