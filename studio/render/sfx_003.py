"""SFX bed for Reel 003 (synthesised)."""
import sys, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render.sfx import SR, chime, ding, pop, thud, tick, type_click, whoosh
from render import timeline_003 as T


def build():
    S, B = T.SCENES, T.beat
    out = np.zeros(int(SR * T.DUR) + SR)
    def put(x, at, g=1.0):
        i = max(0, int(at * SR)); out[i:i + len(x)] += x[:len(out) - i] * g
    put(ding(), 0.03, .7); put(pop(520), 0.05, .4); put(pop(440), B(0, "Five plans") + 0.25, .45); put(pop(620), B(0, "So what's") - 0.05, .5); put(thud(), B(0, "a year?") - 0.1, .55)
    tb = B(1, "₹5,940") - 0.1
    put(pop(500), B(1, "₹495") - 0.05, .4)
    for i in range(12): put(tick(700 + 30 * i), tb + 0.1 + i * 0.12, .35)
    put(thud(), tb + 1.55, .6); put(chime(), tb + 1.6, .35)
    for i in range(5): put(pop(430 + i * 40), S[2][0] + 0.35 + i * 0.12, .3)
    put(thud(), B(2, "forgotten") - 0.1, .5)
    for ph in ("Scan last", "List every", "Then note"): put(pop(560), B(3, ph) - 0.1, .45)
    put(whoosh(.5), S[4][0] - 0.2, .35)
    put(whoosh(.5), B(4, "You see") - 0.2, .35); put(pop(500), B(4, "You see") + 0.2, .35)
    put(whoosh(.5), B(4, "get a reminder") - 0.25, .35); put(ding(1320), B(4, "get a reminder") + 0.5, .5)
    put(whoosh(.5), S[5][0] - 0.2, .4); put(chime(), S[5][0] + 0.5, .8)
    return np.tanh(out * 1.1)[:int(SR * T.DUR)]


if __name__ == "__main__":
    y = build(); pcm = (np.stack([y, y], 1) * 32767 * .9).astype("<i2")
    with wave.open(sys.argv[1], "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
