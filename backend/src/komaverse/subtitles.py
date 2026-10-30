"""SRT subtitle tracks: strict-ish parse, WebVTT sidecar for the player.

One track per scene. The MP4 render burns subtitles with ffmpeg's libass
filter when it works; the VTT sidecar always ships so the in-app export
player shows captions regardless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_SEGMENTS = 500
MAX_TEXT_LEN = 500

_TS = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


@dataclass
class SubSegment:
    start: float
    end: float
    text: str


def _parse_ts(raw: str) -> float:
    m = _TS.fullmatch(raw.strip())
    if not m:
        raise ValueError(f"bad timestamp: {raw!r}")
    h, mi, s, frac = m.groups()
    frac = (frac + "000")[:3]
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(frac) / 1000


def parse_srt(text: str) -> list[SubSegment]:
    """Parse SRT content into segments. Raises ValueError with a human
    reason when the file is not usable subtitles."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\ufeff \n")
    if not text:
        raise ValueError("subtitle file is empty")
    blocks = re.split(r"\n\s*\n", text)
    segments: list[SubSegment] = []
    for block in blocks:
        lines = [ln for ln in block.strip().split("\n")]
        if not lines:
            continue
        # Optional numeric index first line.
        if re.fullmatch(r"\d+", lines[0].strip()):
            lines = lines[1:]
        if not lines:
            continue
        timing = lines[0].strip()
        if "-->" not in timing:
            raise ValueError(f"expected a timing line, got: {timing!r}")
        left, _, right = timing.partition("-->")
        try:
            start, end = _parse_ts(left), _parse_ts(right)
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        if end - start < 0.1:
            raise ValueError(f"segment shorter than 0.1s at {timing!r}")
        body = "\n".join(l.strip() for l in lines[1:] if l.strip())
        if not body:
            raise ValueError(f"empty caption at {timing!r}")
        if len(body) > MAX_TEXT_LEN:
            raise ValueError(f"caption too long at {timing!r}")
        segments.append(SubSegment(start=start, end=end, text=body))
        if len(segments) > MAX_SEGMENTS:
            raise ValueError(f"more than {MAX_SEGMENTS} captions")
    if not segments:
        raise ValueError("no captions found")
    segments.sort(key=lambda s: s.start)
    return segments


def _fmt_vtt_ts(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3600000)
    mi, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{mi:02d}:{s:02d}.{ms:03d}"


def _fmt_srt_ts(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3600000)
    mi, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{mi:02d}:{s:02d},{ms:03d}"


def segments_to_srt(segments: list[SubSegment]) -> str:
    out = []
    for i, seg in enumerate(segments, 1):
        out.append(f"{i}\n{_fmt_srt_ts(seg.start)} --> {_fmt_srt_ts(seg.end)}\n{seg.text}")
    return "\n\n".join(out)


def segments_to_vtt(segments: list[SubSegment]) -> str:
    out = ["WEBVTT", ""]
    for seg in segments:
        out.append(f"{_fmt_vtt_ts(seg.start)} --> {_fmt_vtt_ts(seg.end)}")
        out.append(seg.text)
        out.append("")
    return "\n".join(out)
