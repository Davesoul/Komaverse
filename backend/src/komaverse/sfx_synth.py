"""Procedural anime SFX: tiny offline synth (no assets, no licenses).

Mixing order follows the anime convention: dialogue first, impact hits
second, music bed last, silence used on purpose. Every kind renders
48 kHz mono 16-bit WAV bytes plus its duration in seconds.
"""

import io
import wave

import numpy as np

SR = 48000
SFX_KINDS = ("impact", "whoosh", "pop", "sparkle", "roomtone")


def _to_wav(samples: np.ndarray) -> bytes:
    peak = float(np.max(np.abs(samples))) or 1.0
    pcm = (samples / peak * 0.89 * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(pcm.tobytes())
    return buf.getvalue()


def _noise(n: int) -> np.ndarray:
    return np.random.default_rng(7).standard_normal(n)


def _impact() -> np.ndarray:
    # Layered hit: sine drop plus noise burst, loudest at the connect.
    n = int(SR * 0.6)
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * (150 * np.exp(-t * 6) + 40) * t) * np.exp(-t * 7)
    burst = _noise(n) * np.exp(-t * 25) * 0.7
    return tone + burst


def _whoosh() -> np.ndarray:
    # Riser sweep for cuts, pans, and entrances.
    n = int(SR * 0.9)
    t = np.arange(n) / SR
    env = np.sin(np.pi * t / (n / SR)) ** 2
    return _noise(n) * env * 0.8


def _pop() -> np.ndarray:
    # Short blip for UI taps, blinks, and reaction beats.
    n = int(SR * 0.18)
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * 600 * t) * np.exp(-t * 30)


def _sparkle() -> np.ndarray:
    # High arpeggio for eye-catches, magic, and title cards.
    n = int(SR * 0.7)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for i, freq in enumerate((2093.0, 2637.0, 3136.0, 4186.0)):
        start = int(i * SR * 0.1)
        seg = np.sin(2 * np.pi * freq * t) * np.exp(-np.maximum(t - i * 0.1, 0) * 8)
        seg[:start] = 0
        out += seg
    return out * 0.5


def _roomtone() -> np.ndarray:
    # Soft bed for ma (deliberate quiet beats): lowpassed noise.
    n = int(SR * 2.0)
    x = _noise(n)
    y = np.zeros(n)
    alpha = 0.08
    for i in range(1, n):
        y[i] = y[i - 1] + alpha * (x[i] - y[i - 1])
    return y * 3.0


_RENDER = {
    "impact": _impact,
    "whoosh": _whoosh,
    "pop": _pop,
    "sparkle": _sparkle,
    "roomtone": _roomtone,
}


def render_sfx(kind: str) -> tuple[bytes, float]:
    """Render one SFX kind to (wav bytes, seconds). Raises KeyError."""
    samples = _RENDER[kind]()
    return _to_wav(samples), len(samples) / SR
