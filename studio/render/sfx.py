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
def chime():
    t = t_(1.4); return sum(np.sin(2*np.pi*f*t)*a for f, a in ((880,.4),(1318,.3),(1760,.2))) * np.exp(-t*3) * .5

def build():
    out = np.zeros(int(SR * DUR) + SR)
    def put(x, at, g=1.0):
        i = int(at * SR); out[i:i+len(x)] += x[:len(out)-i] * g
    put(ding(), 0.25, .9)
    put(whoosh(.6), 3.0, .6)
    for i in range(0, 50, 2): put(pop(480 + (i % 7) * 20), 3.55 + i*0.022, .18)
    for g, at in ((15,7.0),(5,8.1),(3,9.0),(9,10.6),(4,11.7),(1,12.6),(5,14.4)):
        for j in range(g): put(tick(700 + 40*(j % 5)), at + j*0.045, .4)
    put(riser(2.4), 15.2, .8); put(thud(), 17.6, .9)
    for i, at in enumerate((22.3, 22.75, 23.2, 23.65)): put(pop(420 + i*60), at, .5)
    put(whoosh(.7), 25.9, .5)
    put(whoosh(.7, False), 26.7, .5)
    for i in range(10): put(type_click(), 27.8 + i*0.08, 1)
    put(ding(1760), 28.8, .5)
    put(whoosh(.5), 30.9, .5); put(chime(), 31.4, .8)
    out = np.tanh(out * 1.1)
    return out[:int(SR * DUR)]

if __name__ == "__main__":
    y = build(); pcm = (np.stack([y, y], 1) * 32767 * .9).astype("<i2")
    with wave.open(sys.argv[1], "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
