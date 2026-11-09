"""Offline lip-sync timing (M2).

Amplitude-envelope visemes derived from take audio in-process: no paid
services, no network. When the audio cannot be decoded (or no audio is
available yet), a deterministic procedural fallback distributes
mouth shapes from the cue text so the renderer and frontend still get
a consumable track.

Frame shape (JSON-serializable dict):
    {"start_sec": float, "end_sec": float,
     "viseme": "closed|narrow|open|wide", "openness": 0.0..1.0}

Visemes are coarse mouth shapes for a 2D mouth overlay — not phonemes.
"""

from __future__ import annotations

import hashlib
import io
import math
import struct
import wave

FRAME_SEC = 0.1  # 10 timing frames per second
VISEMES = ("closed", "narrow", "open", "wide")


def openness_to_viseme(openness: float) -> str:
    """Bucket a 0..1 mouth openness into a coarse viseme name."""
    if openness < 0.15:
        return "closed"
    if openness < 0.4:
        return "narrow"
    if openness < 0.7:
        return "open"
    return "wide"


def decode_wav_mono(wav_bytes: bytes) -> tuple[list[float], int]:
    """Decode 16-bit PCM WAV bytes to mono float samples + sample rate.

    Multi-channel input is averaged to mono. Raises ValueError when the
    bytes are not decodable PCM WAV (callers fall back to procedural).
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            n_channels = w.getnchannels()
            sampwidth = w.getsampwidth()
            rate = w.getframerate()
            n_frames = w.getnframes()
            if n_frames <= 0 or rate <= 0:
                raise ValueError("empty wav")
            raw = w.readframes(n_frames)
    except (wave.Error, EOFError, struct.error) as exc:
        raise ValueError(f"not decodable wav: {exc}") from exc
    if sampwidth == 1:
        # 8-bit WAV is unsigned.
        vals = [(b - 128) / 128.0 for b in raw]
    elif sampwidth == 2:
        count = len(raw) // 2
        vals = [
            v / 32768.0
            for v in struct.unpack(f"<{count}h", raw[: count * 2])
        ]
    else:
        raise ValueError(f"unsupported wav width: {sampwidth}")
    if n_channels > 1:
        vals = [
            sum(vals[i * n_channels:(i + 1) * n_channels]) / n_channels
            for i in range(len(vals) // n_channels)
        ]
    return vals, rate


def envelope_visemes(
    samples: list[float],
    sample_rate: int,
    frame_sec: float = FRAME_SEC,
) -> list[dict]:
    """Bucket RMS energy per window into viseme frames.

    Normalizes by the take's own peak window so quiet recordings still
    animate; pure silence yields all-closed frames.
    """
    if sample_rate <= 0 or not samples:
        return []
    per = max(int(sample_rate * frame_sec), 1)
    n_frames = math.ceil(len(samples) / per)
    rms: list[float] = []
    for i in range(n_frames):
        window = samples[i * per:(i + 1) * per]
        if not window:
            rms.append(0.0)
            continue
        rms.append(math.sqrt(sum(v * v for v in window) / len(window)))
    peak = max(rms) if rms else 0.0
    frames: list[dict] = []
    for i, energy in enumerate(rms):
        openness = min(max(energy / peak, 0.0), 1.0) if peak > 0 else 0.0
        frames.append(
            {
                "start_sec": round(i * frame_sec, 3),
                "end_sec": round((i + 1) * frame_sec, 3),
                "viseme": openness_to_viseme(openness),
                "openness": round(openness, 3),
            }
        )
    return frames


def analyze_wav_bytes(
    wav_bytes: bytes, frame_sec: float = FRAME_SEC
) -> list[dict]:
    """Full offline path: decode 16 kHz mono WAV → viseme frames."""
    samples, rate = decode_wav_mono(wav_bytes)
    return envelope_visemes(samples, rate, frame_sec)


def procedural_visemes(
    duration_sec: float,
    text: str = "",
    frame_sec: float = FRAME_SEC,
) -> list[dict]:
    """Deterministic text-seeded fallback when audio is undecodable.

    Syllable-ish energy: each word opens the mouth for ~0.25s with a
    closed beat between words; silence (no text) idles mostly closed.
    Seeded per (text, frame) so repeated exports agree exactly.
    """
    n = max(int(math.ceil(max(duration_sec, 0.0) / frame_sec)), 0)
    words = [w for w in (text or "").split() if w]
    frames: list[dict] = []
    for i in range(n):
        t = i * frame_sec
        if words:
            # ~2.5 words/sec spread across the take duration.
            pos = (t / max(duration_sec, frame_sec)) * len(words) * 1.0
            wi = int(pos) % len(words)
            frac = pos - math.floor(pos)
            seed = hashlib.sha256(
                f"{text}\x00{wi}\x00{i}".encode()
            ).digest()
            jitter = (seed[0] / 255.0 - 0.5) * 0.2
            openness = max(0.0, min(1.0, (0.85 - frac * 0.7) + jitter))
        else:
            seed = hashlib.sha256(f"silence\x00{i}".encode()).digest()
            openness = 0.12 if seed[0] % 4 == 0 else 0.0
        frames.append(
            {
                "start_sec": round(t, 3),
                "end_sec": round(t + frame_sec, 3),
                "viseme": openness_to_viseme(openness),
                "openness": round(openness, 3),
            }
        )
    return frames


def map_frames_to_cue(
    frames: list[dict],
    cue_start_sec: float,
    offset_sec: float = 0.0,
    rate: float = 1.0,
    end_sec: float | None = None,
) -> list[dict]:
    """Map take-local viseme frames onto the scene timeline.

    Take time t plays at cue time cue_start + (t - offset) / rate;
    frames fully before the offset are dropped, frames past the trim
    end are dropped, survivors are re-based to cue time.
    """
    rate = min(max(rate or 1.0, 0.25), 4.0)
    offset = max(offset_sec or 0.0, 0.0)
    mapped: list[dict] = []
    for f in frames:
        try:
            start = float(f["start_sec"])
            end = float(f["end_sec"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= offset:
            continue
        cue_start = cue_start_sec + (max(start, offset) - offset) / rate
        cue_end = cue_start_sec + (end - offset) / rate
        if end_sec is not None and cue_start >= end_sec:
            continue
        if end_sec is not None:
            cue_end = min(cue_end, end_sec)
        if cue_end - cue_start < 0.001:
            continue
        mapped.append(
            {
                "start_sec": round(cue_start, 3),
                "end_sec": round(cue_end, 3),
                "viseme": f.get("viseme", "closed"),
                "openness": f.get("openness", 0.0),
            }
        )
    return mapped
