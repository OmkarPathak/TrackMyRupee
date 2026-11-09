"""SFX bed for Reel 002 (synthesised; same sound language as Reel 001)."""
import sys, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render.sfx import SR, chime, clink, ding, pop, thud, tick, type_click, whoosh
from render import timeline_002 as T


def build():
    S, B = T.SCENES, T.beat
    out = np.zeros(int(SR * T.DUR) + SR)
    def put(x, at, g=1.0):
        i = max(0, int(at * SR)); out[i:i + len(x)] += x[:len(out) - i] * g
    put(ding(), 0.05, .8); put(pop(360), 0.1, .4)
    put(pop(520), B(0, "Same salary") + 0.2, .4); put(pop(430), B(0, "Same salary") + 0.4, .4)
    put(whoosh(.9), B(0, "Five years", .1), .35); put(thud(), B(0, "ten lakh", .1), .6); put(pop(640), B(0, "Why?", .1), .5)
    for i, ph in enumerate(("everything you own", "minus everything", "One number")):
        put(tick(700 + 120 * i), B(1, ph) - 0.15, .5)
    put(thud(), B(1, "One number") - 0.1, .5)
    own0 = B(2, "You own")
    for i in range(5): put(tick(760 + 40 * i), own0 + 0.15 + i * 0.28, .4)
    put(chime(), B(2, "ten lakh") - 0.1, .35)
    for i in range(2): put(tick(520 + 40 * i), B(2, "You owe") - 0.08 + i * 0.3, .4)
    put(chime(), B(2, "three lakh") - 0.1, .3); put(thud(), B(2, "Net worth") - 0.1, .8); put(chime(), B(2, "Net worth") - 0.05, .5)
    for ph in ("It shows", "It exposes", "And it gives"): put(pop(560), B(3, ph) - 0.1, .45)
    put(whoosh(.8), B(3, "It shows") + 0.4, .3)
    for i in range(5): put(pop(430 + i * 50), S[4][0] + 0.5 + i * 0.22, .35)
    put(thud(), S[4][0] + 1.7, .5)
    for i in range(10): put(type_click(), B(5, "log your") - 0.2 + 0.3 + i * 0.11, 1)
    put(pop(520), B(5, "update your") - 0.1, .4)
    put(whoosh(.5), B(6, "puts it") - 0.2, .4)
    put(whoosh(.5), B(6, "savings") - 0.2, .4)
    for i in range(18): put(pop(480 + (i % 6) * 30), B(6, "savings") + 0.15 + i * 0.07, .15)
    put(whoosh(.5), B(6, "One number") - 0.25, .4); put(chime(), B(6, "One number") + 0.6, .45)
    put(whoosh(.5), S[7][0] - 0.2, .4); put(chime(), S[7][0] + 0.5, .8)
    return np.tanh(out * 1.1)[:int(SR * T.DUR)]


if __name__ == "__main__":
    y = build(); pcm = (np.stack([y, y], 1) * 32767 * .9).astype("<i2")
    with wave.open(sys.argv[1], "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
