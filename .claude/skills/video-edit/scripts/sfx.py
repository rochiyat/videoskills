"""Efek suara sintetis (tanpa file sampel) dan mixing relatif terhadap level suara.

Setiap efek dinormalkan ke level suara pembicara lalu diturunkan sebesar `volume_db`,
jadi efek tidak pernah lebih keras dari suara, apa pun level rekamannya.
"""
import wave

import numpy as np

RATE = 48000
RNG = np.random.default_rng(7)


def _env(n, attack, release):
    t = np.arange(n) / RATE
    env = np.minimum(1.0, t / max(attack, 1e-4))
    return env * np.exp(-np.maximum(0, t - attack) / release)


def whoosh(dur=0.42):
    n = int(dur * RATE)
    noise = RNG.standard_normal(n)
    # lowpass satu kutub yang cutoff-nya naik lalu turun: terdengar seperti udara lewat
    t = np.linspace(0, 1, n)
    cutoff = 300 + 5200 * np.sin(np.pi * t) ** 2
    alpha = 1 - np.exp(-2 * np.pi * cutoff / RATE)
    out = np.empty(n)
    acc = 0.0
    for i in range(n):
        acc += alpha[i] * (noise[i] - acc)
        out[i] = acc
    out *= np.sin(np.pi * t) ** 2
    pan = np.clip(t, 0.15, 0.85)
    return np.stack([out * np.sqrt(1 - pan), out * np.sqrt(pan)], axis=1)


def pop(dur=0.12):
    n = int(dur * RATE)
    t = np.arange(n) / RATE
    freq = 320 + 700 * np.exp(-t / 0.018)
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    out = np.sin(phase) * _env(n, 0.002, 0.035)
    return np.stack([out, out], axis=1)


def tick(dur=0.05):
    n = int(dur * RATE)
    t = np.arange(n) / RATE
    out = (np.sin(2 * np.pi * 2300 * t) + 0.3 * RNG.standard_normal(n)) * _env(n, 0.0005, 0.008)
    return np.stack([out, out], axis=1)


def ding(dur=0.9):
    n = int(dur * RATE)
    t = np.arange(n) / RATE
    out = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t / d)
              for f, a, d in [(1318.5, 1.0, 0.35), (2637, 0.35, 0.18), (3955, 0.12, 0.1)])
    out = out * _env(n, 0.003, 10)
    return np.stack([out, out], axis=1)


SOUNDS = {"whoosh": whoosh, "pop": pop, "tick": tick, "ding": ding}
# Titik "hit" tiap efek (detik dari awal sampel), diselaraskan dengan momen visualnya.
HIT = {"whoosh": 0.21, "pop": 0.0, "tick": 0.0, "ding": 0.0}


def _peak_rms_db(x):
    hop = int(RATE * 0.01)
    mono = x.mean(axis=1)
    n = len(mono) // hop
    if n == 0:
        return -100.0
    rms = np.sqrt(np.mean(mono[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    return float(20 * np.log10(rms.max()))


def voice_level_db(segments, envelopes, fps):
    """Level suara pembicara (persentil 95 dari RMS 10 ms) pada bagian yang dipakai."""
    values = []
    for seg in segments:
        db = envelopes.get(seg["clip"])
        if db is None:
            continue
        a = int(seg["start"] / 0.01)
        values.append(db[a : a + int(seg["frames"] / fps / 0.01)])
    values = np.concatenate(values) if values else np.array([-30.0])
    active = values[values > np.percentile(values, 40)]
    return float(np.percentile(active if len(active) else values, 95))


def render_track(events, total, voice_db, volume_db, path):
    """Tulis WAV stereo sepanjang reel berisi semua efek. events: [(waktu_hit, nama)]."""
    volume_db = min(volume_db, -3.0)
    track = np.zeros((int(total * RATE) + RATE, 2))
    for at, name in events:
        sample = SOUNDS[name]()
        gain = 10 ** ((voice_db + volume_db - _peak_rms_db(sample)) / 20)
        start = int((at - HIT[name]) * RATE)
        if start < 0:
            sample, start = sample[-start:], 0
        end = min(len(track), start + len(sample))
        track[start:end] += sample[: end - start] * gain
    track = np.clip(track[: int(total * RATE)], -1, 1)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes((track * 32767).astype("<i2").tobytes())
