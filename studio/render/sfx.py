"""Synthesised sound-effect bed for Reel 001 (no external assets, no licences).
Writes a 44.1kHz stereo WAV. Music is intentionally NOT included: add a licensed
track in CapCut / Instagram (see editing-instructions.md)."""
import sys, wave
import numpy as np

SR = 44100
DUR = 35.0

def t_(d): return np.arange(int(SR * d)) / SR

def env(n, a=0.005, r=0.2):
    e = np.ones(n); na = max(1, int(a * SR)); e[:na] = np.linspace(0, 1, na)
    nr = max(1, int(r * SR)); e[-nr:] *= np.linspace(1, 0, nr) if nr < n else 1
    return e

def ding(f=1320):
    t = t_(0.7); return (np.sin(2*np.pi*f*t) + .4*np.sin(2*np.pi*f*2.01*t)) * np.exp(-t*6) * .5
def tick(f=900):
    t = t_(0.09); return np.sin(2*np.pi*f*t) * np.exp(-t*55) * .45
def pop(f=520):
    t = t_(0.16); fr = f * (1 + 1.5*np.exp(-t*30)); return np.sin(2*np.pi*np.cumsum(fr)/SR) * np.exp(-t*22) * .5
def whoosh(d=0.6, up=True):
    n = int(SR*d); x = np.random.RandomState(1).randn(n)
    k = np.convolve(x, np.ones(40)/40, "same"); sw = np.linspace(0, 1, n) if up else np.linspace(1, 0, n)
    return k * np.sin(np.pi*np.linspace(0, 1, n))**2 * (0.3 + sw*0.5) * 2.2
def riser(d=1.6):
    t = t_(d); f = 200 + 900*(t/d)**2; return np.sin(2*np.pi*np.cumsum(f)/SR) * (t/d)**1.5 * .35
def thud():
    t = t_(0.8); f = 90*np.exp(-t*3)+45; return np.sin(2*np.pi*np.cumsum(f)/SR) * np.exp(-t*5) * .9
def type_click():
    t = t_(0.03); return np.random.RandomState(2).randn(len(t)) * np.exp(-t*140) * .25
def fall(d=0.9):
    t = t_(d); f = 1100 * np.exp(-t * 3.2) + 90; return np.sin(2*np.pi*np.cumsum(f)/SR) * np.minimum(1, t*30) * (1 - t/d) * .45
def slam():
    t = t_(0.5); n = np.random.RandomState(3).randn(len(t)) * np.exp(-t*30) * .5
    return n + np.sin(2*np.pi*np.cumsum(120*np.exp(-t*6)+40)/SR) * np.exp(-t*7) * .9
def clink():
    t = t_(0.5); return (np.sin(2*np.pi*2400*t) + .5*np.sin(2*np.pi*3600*t)) * np.exp(-t*14) * .35
def chime():
    t = t_(1.4); return sum(np.sin(2*np.pi*f*t)*a for f, a in ((880,.4),(1318,.3),(1760,.2))) * np.exp(-t*3) * .5

def build():
    from render import timeline as T
    out = np.zeros(int(SR * T.DUR) + SR)
    def put(x, at, g=1.0):
        i = int(at * SR); out[i:i+len(x)] += x[:len(out)-i] * g
    put(ding(), 0.0, .9); put(pop(300), 0.05, .5); put(clink(), 0.62, .9)
    put(fall(.9), 0.9, .8); put(slam(), 1.2, .55); put(slam(), 2.2, .6)
    put(whoosh(.5), 3.1, .5); put(pop(700), 3.8, .3)
    for i in range(0, 50, 2): put(pop(480 + (i % 7) * 20), T.STORY_IN + 0.15 + i * 0.012, .12)
    for k, n in (("rent", 15), ("emi", 5), ("bills", 3), ("food", 9), ("travel", 4), ("subs", 1), ("sip", 5)):
        for j in range(n): put(tick(700 + 40 * (j % 5)), T.GROUP_START[k] + j * 0.028, .35)
    put(riser(1.6), T.REVEAL - 1.5, .7); put(thud(), T.REVEAL, .9)
    for i in range(4): put(pop(420 + i * 60), T.LEAKS + i * 0.4, .45)
    put(whoosh(.5), T.STORY_OUT - 0.3, .45)
    for i, d in enumerate((2.6, 4.4, 5.7)): put(pop(560 + i * 80), T.HABIT + d, .5)
    for i in range(14): put(tick(900 + 30 * i), T.HABIT + 5.2 + i * 0.07, .3)
    put(whoosh(.4), T.SCENES[8][0], .4)
    for i in range(10): put(type_click(), T.TYPE0 + i * T.TYPE_STEP, 1)
    put(ding(1760), T.PARSED, .4); put(clink(), T.ADD_TAP, .8)
    put(whoosh(.4), T.SCENES[9][0], .4); put(pop(520), T.LIST_IN, .4)
    put(ding(1320), T.REMINDER_IN + 0.15, .6); put(ding(1760), T.REMINDER_IN + 0.5, .4)
    put(whoosh(.5), T.END_IN - 0.4, .45); put(chime(), T.END_IN + 0.5, .8)
    out = np.tanh(out * 1.1)
    return out[:int(SR * T.DUR)]

if __name__ == "__main__":
    import sys, wave
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    y = build(); pcm = (np.stack([y, y], 1) * 32767 * .9).astype("<i2")
    with wave.open(sys.argv[1], "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
