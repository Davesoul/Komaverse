"""Project renderer: scenes/clips/cues → H.264 + AAC MP4.

Clip sources: panel crops (page image + box), frame composites (layer PNGs),
page stills (kind=media, source_id=page id), video files (kind=video,
source_id=finished generation job id; looped to the clip length, Ken Burns
moves apply over the moving picture). Text title cards (kind=text) are
rastered in-process with entrance anims (none/fade/rise). Motion: none,
zoom-in, zoom-out, pan-left, pan-right, pan-up, pan-down, drift.
Per-clip fade in/out, a color grade (none/grayscale/sepia/vintage/vivid),
plus transitions into a clip (crossfade, fade-black, fade-white,
wipe-left, wipe-right, push-left, impact) overlap the boundary frames. Timeline audio cues are mixed in a second ffmpeg pass
(adelay/volume/afade per cue, amix, AAC). Encoding via the imageio-ffmpeg
bundled binary (libx264/yuv420p/faststart) so output plays everywhere.
"""

from __future__ import annotations

import base64
import io
import logging
import subprocess
import tempfile
import textwrap
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from komaverse import storage
from komaverse.config import settings
from komaverse.db_models import ClipRow, FrameRow, JobRow, PageRow, PanelRow, ProjectRow
from komaverse.models import uuid_short

logger = logging.getLogger("komaverse.renderer")

MAX_TOTAL_SEC = 120.0
MAX_FPS = 30
# Canvas paper token (#f6f1e8): exports match what the artist sees.
PAPER_RGB = (246, 241, 232)


def data_url_to_bgr(data_url: str) -> np.ndarray | None:
    try:
        payload = data_url.split(",", 1)[1] if "," in data_url else data_url
        img = Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGBA")
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGR)
    except Exception:
        return None


_CHOPS = {
    "multiply": ImageChops.multiply,
    "screen": ImageChops.screen,
    "overlay": ImageChops.overlay,
    "soft-light": ImageChops.soft_light,
    "hard-light": ImageChops.hard_light,
    "difference": ImageChops.difference,
    "darken": ImageChops.darker,
    "lighten": ImageChops.lighter,
}


def _apply_blend(base: Image.Image, over: Image.Image, mode: str) -> Image.Image:
    """Blend `over` onto opaque `base` (both RGBA).

    Honors every mode Pillow implements; color-dodge/burn, exclusion and the
    HSL modes still fall back to normal (documented in BLEND_MODES).
    """
    chop = _CHOPS.get(mode)
    if chop is None:
        return Image.alpha_composite(base, over)
    mixed = chop(base.convert("RGB"), over.convert("RGB"))
    blended = mixed.convert("RGBA")
    blended.putalpha(over.getchannel("A"))
    return Image.alpha_composite(base, blended)


def composite_frame_image(frame: FrameRow) -> Image.Image:
    """Alpha-composite visible layers onto paper at frame size (RGBA)."""
    canvas = Image.new("RGBA", (frame.width, frame.height), PAPER_RGB + (255,))
    for layer in sorted(frame.layers, key=lambda layer: layer.layer_order):
        if not layer.visible or not layer.data_url:
            continue
        try:
            payload = layer.data_url.split(",", 1)[1]
            img = Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGBA")
        except Exception:
            continue
        if img.size != canvas.size:
            img = img.resize(canvas.size)
        if layer.opacity < 100:
            alpha = img.getchannel("A").point(lambda v: v * layer.opacity // 100)
            img.putalpha(alpha)
        canvas = _apply_blend(canvas, img, layer.blend_mode or "normal")
    return canvas


def composite_frame(frame: FrameRow) -> np.ndarray:
    """Alpha-composite visible layers onto paper at frame size (BGR)."""
    return cv2.cvtColor(np.array(composite_frame_image(frame)), cv2.COLOR_RGBA2BGR)


def contain_resize(src: np.ndarray, w: int, h: int) -> np.ndarray:
    """Scale src to fit inside (w, h), then center on a black canvas.

    This is what the preview player shows (object-fit contain on a
    black stage): the whole picture stays visible, aspect intact.
    """
    sh, sw = src.shape[:2]
    scale = min(w / sw, h / sh)
    nw, nh = max(int(sw * scale), 1), max(int(sh * scale), 1)
    resized = cv2.resize(src, (nw, nh))
    canvas = np.zeros((h, w, 3), dtype=np.uint8)
    x, y = (w - nw) // 2, (h - nh) // 2
    canvas[y : y + nh, x : x + nw] = resized
    return canvas


def motion_canvas(base: np.ndarray, w: int, h: int, t: float, motion: str) -> np.ndarray:
    """Canvas-space Ken Burns on a WxH frame, mirroring the preview CSS.

    Preview pans/zooms the contained picture (scale <= 1.18, +/-4% pans);
    the warp below uses the same factors so the MP4 matches the stage.
    Out-of-canvas samples stay black (the preview stage is #000).
    """
    if motion == "zoom-in":
        s, tx, ty = 1.0 + 0.18 * t, 0.0, 0.0
    elif motion == "zoom-out":
        s, tx, ty = 1.18 - 0.18 * t, 0.0, 0.0
    elif motion == "pan-left":
        s, tx, ty = 1.15, 1.15 * (4.0 - 8.0 * t) / 100.0 * w, 0.0
    elif motion == "pan-right":
        s, tx, ty = 1.15, 1.15 * (-4.0 + 8.0 * t) / 100.0 * w, 0.0
    elif motion == "pan-up":
        s, tx, ty = 1.15, 0.0, 1.15 * (4.0 - 8.0 * t) / 100.0 * h
    elif motion == "pan-down":
        s, tx, ty = 1.15, 0.0, 1.15 * (-4.0 + 8.0 * t) / 100.0 * h
    elif motion == "drift":
        s, tx, ty = (
            1.15,
            1.15 * (-4.0 + 8.0 * t) / 100.0 * w,
            1.15 * (-4.0 + 8.0 * t) / 100.0 * h,
        )
    else:  # none and unknown motions hold the frame still
        return base
    cx, cy = w / 2.0, h / 2.0
    mat = np.float32([[s, 0, cx - s * cx + tx], [0, s, cy - s * cy + ty]])
    return cv2.warpAffine(
        base, mat, (w, h),
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0),
    )


def motion_frame(base: np.ndarray, w: int, h: int, t: float, motion: str) -> np.ndarray:
    """Crop a WxH window from a 2x base image following the motion path."""
    bh, bw = base.shape[:2]
    if motion == "zoom-in":
        s = 2.0 - t  # window scale 2x -> 1x
        ww, hh = int(w * s), int(h * s)
        x, y = (bw - ww) // 2, (bh - hh) // 2
    elif motion == "zoom-out":
        s = 1.0 + t
        ww, hh = int(w * s), int(h * s)
        x, y = (bw - ww) // 2, (bh - hh) // 2
    elif motion == "pan-left":
        ww, hh = w, h
        x, y = int((bw - ww) * (1 - t)), (bh - hh) // 2
    elif motion == "pan-right":
        ww, hh = w, h
        x, y = int((bw - ww) * t), (bh - hh) // 2
    elif motion == "pan-up":
        ww, hh = w, h
        x, y = (bw - ww) // 2, int((bh - hh) * (1 - t))
    elif motion == "pan-down":
        ww, hh = w, h
        x, y = (bw - ww) // 2, int((bh - hh) * t)
    elif motion == "drift":
        # Slow diagonal drift: top-left settles to bottom-right.
        ww, hh = w, h
        x, y = int((bw - ww) * t), int((bh - hh) * t)
    else:  # none
        ww, hh = w, h
        x, y = (bw - ww) // 2, (bh - hh) // 2
    x = min(max(x, 0), bw - ww)
    y = min(max(y, 0), bh - hh)
    return cv2.resize(base[y : y + hh, x : x + ww], (w, h))


def fade_frame(frame: np.ndarray, alpha: float) -> np.ndarray:
    """Scale toward black by alpha (1 = untouched)."""
    if alpha >= 1.0:
        return frame
    if alpha <= 0.0:
        return np.zeros_like(frame)
    return (frame.astype(np.float32) * alpha).astype(np.uint8)


TEXT_CARD_BG = (13, 13, 18)  # near-black title-card ground (RGB)


def render_text_image(
    text: str,
    *,
    size: int = 64,
    color: str = "#ffffff",
    position: str = "center",
    w: int = 1920,
    h: int = 1080,
    anim: str = "none",
    progress: float = 1.0,
) -> np.ndarray:
    """Full-frame text title card (BGR uint8): the export look for text clips.

    Entrance anims play over the first stretch of the clip: fade dissolves
    in, rise floats up while dissolving. Static callers pass the defaults.
    """
    body = (text or "").strip()
    size = min(max(int(size or 64), 16), 256)
    try:
        font = ImageFont.load_default(size=size)
    except Exception:
        font = ImageFont.load_default()
    # Rough char budget from the point size, then hard-wrap.
    per_line = max(10, int(w / max(size * 0.55, 1)))
    lines: list[str] = []
    for para in body.splitlines() or [""]:
        lines.extend(textwrap.wrap(para, width=per_line) or [""])
    lines = lines[:12]
    p = min(max(progress, 0.0), 1.0)
    if anim == "fade":
        alpha = min(1.0, p / 0.25) if p < 1.0 else 1.0
        dy = 0.0
    elif anim == "rise":
        k = min(1.0, p / 0.35)
        alpha = k
        dy = (1.0 - k) * size * 1.2
    else:
        alpha, dy = 1.0, 0.0
    try:
        rgb = tuple(int(color.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        rgb = (255, 255, 255)
    card = Image.new("RGB", (w, h), TEXT_CARD_BG)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cy = h * 0.22 if position == "top" else h * 0.78 if position == "bottom" else h / 2
    anchor = "ma" if position in ("top", "bottom") else "mm"
    draw.multiline_text(
        (w / 2, cy - dy),
        "\n".join(lines),
        font=font,
        fill=(*rgb, int(255 * alpha)),
        anchor=anchor,
        align="center",
        spacing=max(size // 3, 4),
    )
    out = Image.alpha_composite(card.convert("RGBA"), layer).convert("RGB")
    return cv2.cvtColor(np.asarray(out), cv2.COLOR_RGB2BGR)


def apply_filter(frame: np.ndarray, name: str) -> np.ndarray:
    """CapCut-style color grade on one BGR frame (export burn-in)."""
    if not name or name == "none":
        return frame
    if name == "grayscale":
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if name == "sepia":
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32)
        mat = np.array(
            [[0.393, 0.769, 0.189], [0.349, 0.686, 0.168], [0.272, 0.534, 0.131]]
        )
        graded = np.clip(rgb @ mat.T, 0, 255).astype(np.uint8)
        return cv2.cvtColor(graded, cv2.COLOR_RGB2BGR)
    if name == "vintage":
        aged = apply_filter(frame, "sepia").astype(np.float32)
        aged = np.clip(aged * 0.55 + frame.astype(np.float32) * 0.45, 0, 255)
        h, w = frame.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        dist = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / 2
        mask = np.clip(1.0 - 0.45 * dist, 0.0, 1.0)[..., None]
        return (aged * mask).astype(np.uint8)
    if name == "vivid":
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 1] = np.clip(hsv[..., 1] * 1.35, 0, 255)
        back = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
        return np.clip((back - 128) * 1.08 + 128, 0, 255).astype(np.uint8)
    return frame


def fade_alpha(i: int, n: int, fps: int, fade_in: float, fade_out: float) -> float:
    """Fade envelope for frame i of n (clip-local): 0..1..0 ramps."""
    dur = n / fps
    pos = i / fps
    a = 1.0
    if fade_in > 0:
        a = min(a, pos / fade_in)
    if fade_out > 0:
        a = min(a, max(dur - pos, 0.0) / fade_out)
    return min(max(a, 0.0), 1.0)


def blend_frames(a: np.ndarray, b: np.ndarray, j: int, k: int, kind: str) -> np.ndarray:
    """Transition frame j of k blending tail frame a into head frame b."""
    t = (j + 1) / (k + 1)  # endpoints never duplicate their neighbours
    if kind == "fade-black":
        if t < 0.5:
            return fade_frame(a, 1.0 - 2.0 * t)
        return fade_frame(b, 2.0 * t - 1.0)
    if kind == "impact":
        # Impact frame: a hard white flash right on the cut, manga-style.
        white = np.full_like(a, 255).astype(np.float32)
        u = t * 2.0
        if u < 1.0:
            return (a.astype(np.float32) * (1.0 - u) + white * u).astype(np.uint8)
        u -= 1.0
        return (white * (1.0 - u) + b.astype(np.float32) * u).astype(np.uint8)
    if kind == "wipe-left":
        cut = int(a.shape[1] * t)
        out = a.copy()
        out[:, :cut] = b[:, :cut]
        return out
    if kind == "wipe-right":
        cut = int(a.shape[1] * t)
        out = a.copy()
        out[:, a.shape[1] - cut :] = b[:, b.shape[1] - cut :]
        return out
    if kind == "push-left":
        # b slides in from the right, pushing a out to the left.
        cut = int(a.shape[1] * t)
        out = a.copy()
        out[:, : a.shape[1] - cut] = a[:, cut:]
        out[:, a.shape[1] - cut :] = b[:, :cut]
        return out
    if kind == "fade-white":
        white = np.full_like(a, 255).astype(np.float32)
        if t < 0.5:
            u = 2.0 * t
            return (a.astype(np.float32) * (1.0 - u) + white * u).astype(np.uint8)
        u = 2.0 * t - 1.0
        return (white * (1.0 - u) + b.astype(np.float32) * u).astype(np.uint8)
    # crossfade
    return (a.astype(np.float32) * (1.0 - t) + b.astype(np.float32) * t).astype(np.uint8)


def read_video_frame(vpath, vdur, t_sec: float, w: int, h: int) -> np.ndarray:
    """One video frame by timestamp (own capture: used for the handful of
    transition-boundary frames, which are not sequential)."""
    frame = None
    cap = cv2.VideoCapture(str(vpath))
    try:
        t = t_sec
        if t >= (vdur or 0) > 0:
            t %= vdur
        for attempt in (t, 0.0):
            cap.set(cv2.CAP_PROP_POS_MSEC, attempt * 1000)
            ok, got = cap.read()
            if ok and got is not None:
                frame = got
                break
    finally:
        cap.release()
    if frame is None:
        frame = np.full((h, w, 3), PAPER_RGB[::-1], dtype=np.uint8)
    return frame


def finish_frame(
    frame: np.ndarray, motion: str, i: int, n: int, w: int, h: int,
    fps: int, fade_in: float, fade_out: float,
) -> np.ndarray:
    """Contain-fit on black, canvas-space Ken Burns, fade envelope."""
    base = contain_resize(frame, w, h)
    out = motion_canvas(base, w, h, i / max(n - 1, 1), motion or "none")
    return fade_frame(out, fade_alpha(i, n, fps, fade_in or 0.0, fade_out or 0.0))


def resolve_clip_image(clip: ClipRow, session: Session) -> np.ndarray | None:
    if clip.kind == "panel":
        panel = session.get(PanelRow, clip.source_id)
        if panel is None or panel.page is None:
            return None
        img = cv2.imread(str(storage.resolve(panel.page.image_path)))
        if img is None:
            return None
        x, y = max(int(panel.x), 0), max(int(panel.y), 0)
        w, h = int(panel.w), int(panel.h)
        crop = img[y : y + h, x : x + w]
        return crop if crop.size else None
    if clip.kind == "frame":
        frame = session.get(FrameRow, clip.source_id)
        return composite_frame(frame) if frame is not None else None
    if clip.kind == "media":
        page = session.get(PageRow, clip.source_id)
        if page is None:
            return None
        return cv2.imread(str(storage.resolve(page.image_path)))
    if clip.kind == "text":
        if not (clip.text_content or "").strip():
            return None
        return render_text_image(
            clip.text_content or "",
            size=clip.text_size or 64,
            color=clip.text_color or "#ffffff",
            position=clip.text_position or "center",
        )
    return None  # ai clips land in M4


def video_duration_sec(path: Path) -> float | None:
    """Container duration via OpenCV (no ffprobe dependency)."""
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        if fps > 0 and count > 0:
            return count / fps
        return None
    finally:
        cap.release()


def resolve_clip_video(clip: ClipRow, session: Session) -> tuple[Path, float] | None:
    """(video file, duration_sec) for kind == "video" clips (finished gen jobs)."""
    if clip.kind != "video":
        return None
    job = session.get(JobRow, clip.source_id)
    if job is None or job.kind != "video" or job.status != "done" or not job.result_url:
        return None
    try:
        path = storage.resolve(job.result_url.split("/media/", 1)[-1])
    except Exception:
        return None
    if not path.is_file():
        return None
    dur = video_duration_sec(path)
    return (path, dur) if dur and dur > 0 else None


def audio_has_stream(path: Path) -> bool:
    """True when ffmpeg can open the file and finds an audio stream.

    Note: bare `ffmpeg -i` exits 1 ("no output file") while still printing
    the stream list, so the verdict keys on the `Audio:` line, not the code.
    """
    try:
        proc = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "Audio:" in (proc.stderr or "")

def ffmpeg_info() -> dict:
    """Probe the bundled ffmpeg binary: presence, libx264, libass.

    Returns {"exe", "ok", "libx264", "libass", "version"}. Render needs
    libx264 (H.264 MP4); subtitle burn-in degrades to the VTT sidecar
    without the libass `subtitles`/`ass` filter, so callers can warn
    instead of failing the export.
    """
    try:
        import imageio_ffmpeg  # noqa: PLC0415 — heavy optional at call time

        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return {"exe": "", "ok": False, "libx264": False, "libass": False,
                "version": ""}
    try:
        proc = subprocess.run(
            [exe, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=30,
        )
        out = proc.stdout or ""
        filters = subprocess.run(
            [exe, "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=30,
        ).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return {"exe": exe, "ok": False, "libx264": False, "libass": False,
                "version": ""}
    if proc.returncode != 0:
        return {"exe": exe, "ok": False, "libx264": False, "libass": False,
                "version": ""}
    version = (out.splitlines() or [""])[0][:120]
    has_x264 = "libx264" in out
    has_ass = (" subtitles " in filters) or (" ass " in filters)
    return {
        "exe": exe,
        "ok": has_x264,
        "libx264": has_x264,
        "libass": has_ass,
        "version": version,
    }


def transcode_to_wav(data: bytes, suffix: str) -> bytes:
    """Normalize arbitrary audio bytes to 16 kHz mono 16-bit WAV.

    Meta's transcription endpoint labels everything WAV and rejects
    containers it cannot parse (notably raw MediaRecorder webm/opus),
    so voice input and takes are transcoded before upload. Raises
    RuntimeError with ffmpeg's stderr tail on failure.
    """
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    in_path = None
    out_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=suffix or ".bin", delete=False
        ) as fh:
            fh.write(data)
            in_path = fh.name
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            out_path = fh.name
        proc = subprocess.run(
            [exe, "-y", "-v", "error", "-i", in_path,
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
             "-f", "wav", out_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()[-1:]
            raise RuntimeError("; ".join(tail) or "ffmpeg failed")
        wav = Path(out_path).read_bytes()
        if len(wav) < 44 or not wav.startswith(b"RIFF"):
            raise RuntimeError("ffmpeg produced no WAV output")
        return wav
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"ffmpeg unavailable: {exc}") from exc
    finally:
        for path in (in_path, out_path):
            if path:
                try:
                    Path(path).unlink()
                except OSError:
                    pass



def _atempo_chain(rate: float) -> list[str]:
    """Split a speed factor into ffmpeg atempo filters (each 0.5..2.0)."""
    parts = []
    r = min(max(rate, 0.25), 4.0)
    while r > 2.0:
        parts.append("atempo=2.000")
        r /= 2.0
    while r < 0.5:
        parts.append("atempo=0.500")
        r *= 2.0
    parts.append(f"atempo={r:.3f}")
    return parts


def mix_audio_tracks(
    silent: Path, tracks: list[dict], video_dur: float, dest: Path
) -> None:
    """Mix timeline tracks onto the silent render (AAC, video stream copied).

    Each track: path, start_sec, offset_sec, volume, fade_in, fade_out, rate,
    end_sec. Video duration rules: audio is padded/trimmed to match.
    Raises RuntimeError with ffmpeg's stderr tail on failure.
    """
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [exe, "-y", "-i", str(silent)]
    for track in tracks:
        cmd += ["-ss", f"{track['offset_sec']:.3f}", "-i", str(track["path"])]
    chains = []
    for i, track in enumerate(tracks):
        start_ms = max(int(round(track["start_sec"] * 1000)), 0)
        # Trim first (video clips butt-joint; cues already end themselves),
        # then delay into place: trimming after the delay would eat audio.
        parts = ["aresample=48000"]
        if track.get("loop_sec"):
            size = max(int(round(48000 * track["loop_sec"])), 1)
            parts.append(f"aloop=loop=-1:size={size}")
        if track.get("trim_dur"):
            parts.append(f"atrim=0:{track['trim_dur']:.3f}")
        if track.get("rate", 1.0) != 1.0:
            parts += _atempo_chain(track["rate"])
        parts += [
            f"adelay={start_ms}|{start_ms}",
            f"volume={track['volume']:.3f}",
        ]
        if track["fade_in"] > 0:
            parts.append(
                f"afade=t=in:st={track['start_sec']:.3f}:d={track['fade_in']:.3f}"
            )
        if track["fade_out"] > 0:
            parts.append(
                f"afade=t=out:st={track['end_sec']:.3f}:d={track['fade_out']:.3f}"
            )
        parts.append(f"apad=whole_dur={video_dur:.3f}")
        chains.append(f"[{i + 1}:a]{','.join(parts)}[a{i}]")
    if len(tracks) > 1:
        chains.append(
            f"{''.join(f'[a{i}]' for i in range(len(tracks)))}"
            f"amix=inputs={len(tracks)}:normalize=0[aout]"
        )
        amap = "[aout]"
    else:
        amap = "[a0]"
    cmd += [
        "-filter_complex", ";".join(chains),
        "-map", "0:v", "-map", amap,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        "-t", f"{video_dur:.3f}",
        "-movflags", "+faststart",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "ffmpeg failed")[-500:])


def render_project(
    project_id: str,
    session: Session,
    fps: int | None = None,
    width: int | None = None,
    height: int | None = None,
    include_audio: bool = True,
    on_progress=None,
    scene_id: str | None = None,
) -> dict:
    project = session.get(ProjectRow, project_id)
    if project is None:
        raise ValueError("project not found")
    warnings: list[str] = []
    if fps is not None and fps > MAX_FPS:
        warnings.append(f"fps {fps} clamped to {MAX_FPS}")
    fps = min(fps or project.fps or 24, MAX_FPS)
    w = (width or project.canvas_width or 1280) // 2 * 2
    h = (height or project.canvas_height or 720) // 2 * 2
    if width is not None and width != w:
        warnings.append(f"width {width} coerced to {w} (even)")
    if height is not None and height != h:
        warnings.append(f"height {height} coerced to {h} (even)")

    scenes = sorted(project.scenes, key=lambda s: s.order)
    if scene_id is not None:
        scenes = [s for s in scenes if s.id == scene_id]
        if not scenes:
            raise ValueError("timeline not found in this project")
    clips: list[ClipRow] = []
    scene_starts: dict[str, float] = {}
    offset = 0.0
    for scene in scenes:
        scene_starts[scene.id] = offset
        # Overlay lanes never join the sequential base: V1 alone sets the
        # timeline length; overlays place at absolute scene time.
        ordered = sorted(
            (c for c in scene.clips if (c.lane or 0) == 0),
            key=lambda c: c.order,
        )
        clips.extend(ordered)
        offset += sum(c.duration for c in ordered)
    total = offset
    if not clips or total <= 0:
        raise ValueError("nothing to render: add clips first")
    if total > MAX_TOTAL_SEC:
        raise ValueError(f"project too long ({total:.0f}s > {MAX_TOTAL_SEC:.0f}s)")

    # Pass 1: resolve everything up front. A dangling source (deleted
    # page/panel/frame/job) keeps its slot as blank paper with a warning —
    # dropping it would compress the timeline so the MP4 no longer matches
    # the preview, which holds the slot (black stage + "missing" label).
    paper = np.full((h, w, 3), PAPER_RGB[::-1], dtype=np.uint8)
    resolved: list[tuple[ClipRow, object]] = []
    for clip in clips:
        if clip.kind == "video":
            vid = resolve_clip_video(clip, session)
            if vid is None:
                msg = (
                    f"clip {clip.id} (video): unresolvable source — "
                    f"blank paper holds its {clip.duration}s slot"
                )
                logger.warning(msg)
                warnings.append(msg)
                resolved.append((clip, paper.copy()))
                continue
            resolved.append((clip, ("video", *vid)))
            continue
        src = resolve_clip_image(clip, session)
        if src is None or src.size == 0:
            msg = (
                f"clip {clip.id} ({clip.kind}): unresolvable source — "
                f"blank paper holds its {clip.duration}s slot"
            )
            logger.warning(msg)
            warnings.append(msg)
            resolved.append((clip, paper.copy()))
            continue
        resolved.append((clip, src))
    if not resolved:
        raise ValueError("no clips could be resolved")
    planned = [
        (clip, src, max(int(round(clip.duration * fps)), 1))
        for clip, src in resolved
    ]
    # Transition overlap: clip i lends its first k frames and the previous
    # clip its last k; the k blended frames replace both. k leaves at least
    # one untouched frame on each side, else the transition is skipped.
    overlap = [0] * len(planned)
    for idx in range(1, len(planned)):
        clip = planned[idx][0]
        if (clip.transition or "none") == "none":
            continue
        tdur = min(max(clip.transition_duration or 0.5, 0.1), 2.0)
        k = min(int(round(tdur * fps)), planned[idx - 1][2] - 1, planned[idx][2] - 1)
        overlap[idx] = k if k >= 1 else 0
    total_frames = sum(n for _, _, n in planned) - sum(overlap)
    video_dur = total_frames / fps

    # Overlay lanes V2+: fullscreen cutaways at absolute scene time.
    # They never stretch the base timeline; they clamp to the export end.
    overlays: list[dict] = []
    for scene in scenes:
        base = scene_starts[scene.id]
        for clip in sorted(scene.clips, key=lambda c: ((c.lane or 0), c.order)):
            lane = clip.lane or 0
            if lane == 0:
                continue
            gstart = base + max(clip.start or 0.0, 0.0)
            if gstart >= video_dur:
                warnings.append(
                    f"overlay {clip.id} starts past the export end: skipped"
                )
                continue
            gend = min(gstart + max(clip.duration, 0.1), video_dur)
            if clip.kind == "video":
                vid = resolve_clip_video(clip, session)
                if vid is None:
                    warnings.append(
                        f"overlay {clip.id} (video): unresolvable source"
                    )
                    continue
                src = ("video", *vid)
            else:
                src = resolve_clip_image(clip, session)
                if src is None or src.size == 0:
                    warnings.append(
                        f"overlay {clip.id} ({clip.kind}): unresolvable source"
                    )
                    continue
            overlays.append(
                {"clip": clip, "src": src, "start": gstart, "end": gend,
                 "lane": lane}
            )
    overlays.sort(key=lambda o: (o["lane"], o["start"]))

    # Timeline audio, verified before the mux stage runs.
    tracks: list[dict] = []
    if include_audio:
        for scene in scenes:
            for cue in sorted(scene.cues, key=lambda c: c.start_sec):
                take = cue.audio
                if take is None:
                    continue
                apath = storage.resolve(take.file_path)
                if not apath.is_file() or not audio_has_stream(apath):
                    warnings.append(
                        f"skipped audio '{take.name or take.id}': unreadable file"
                    )
                    continue
                start = scene_starts[scene.id] + max(cue.start_sec, 0)
                trim = max(take.duration_sec - max(cue.offset_sec, 0), 0.1)
                # Cue speed: rate > 1 shortens the cue on the timeline
                # (rate < 1 stretches it); the fade anchors below reuse end.
                rate = min(max(getattr(cue, "rate", 1.0) or 1.0, 0.25), 4.0)
                trim = trim / rate
                end = min(video_dur, start + trim) if take.duration_sec > 0 else video_dur
                if cue.end_sec is not None:
                    end = min(end, max(cue.end_sec, start + 0.1))
                tracks.append(
                    {
                        "path": apath,
                        "start_sec": start,
                        "offset_sec": max(cue.offset_sec, 0),
                        "volume": min(max(cue.volume, 0), 2),
                        "fade_in": max(cue.fade_in, 0),
                        "fade_out": max(cue.fade_out, 0),
                        "rate": rate,
                        "end_sec": max(end - max(cue.fade_out, 0), 0),
                    }
                )
        # Overlay video audio rides the same mix at absolute time.
        for ov in overlays:
            oclip = ov["clip"]
            if not isinstance(ov["src"], tuple):
                continue
            _, ovpath, ovdur = ov["src"]
            ocdur = ov["end"] - ov["start"]
            if ocdur <= 0 or not audio_has_stream(ovpath):
                continue
            ofade_out = max(oclip.fade_out or 0.0, 0.0)
            _ov_off = max((oclip.offset or 0.0), 0.0)
            tracks.append(
                {
                    "path": ovpath,
                    "start_sec": ov["start"],
                    "offset_sec": _ov_off,
                    "volume": 1.0,
                    "fade_in": max(oclip.fade_in or 0.0, 0.0),
                    "fade_out": ofade_out,
                    "end_sec": max(ov["start"] + ocdur - ofade_out, ov["start"] + 0.1),
                    "loop_sec": (ovdur - _ov_off) if ocdur > (ovdur - _ov_off) else None,
                    "trim_dur": ocdur,
                }
            )
        cue_count = len(tracks)
        # Native audio of video clips: frames are re-rendered from pixels,
        # so without this the export goes silent wherever a video plays.
        # AI generations have no audio stream and are skipped silently;
        # looping clips loop their audio to match the looping picture.
        pos_frames = 0
        for idx, (clip, src, n) in enumerate(planned):
            start = pos_frames / fps
            k_out = overlap[idx + 1] if idx + 1 < len(overlap) else 0
            pos_frames += n - k_out
            if not isinstance(src, tuple):
                continue
            _, vpath, vdur = src
            cdur = (n - k_out) / fps
            if cdur <= 0 or not audio_has_stream(vpath):
                continue
            fade_out = max(clip.fade_out or 0.0, 0.0)
            _clip_off = max((clip.offset or 0.0), 0.0)
            tracks.append(
                {
                    "path": vpath,
                    "start_sec": start,
                    "offset_sec": _clip_off,
                    "volume": 1.0,
                    "fade_in": max(clip.fade_in or 0.0, 0.0),
                    "fade_out": fade_out,
                    "end_sec": max(start + cdur - fade_out, start + 0.1),
                    "loop_sec": (vdur - _clip_off) if cdur > (vdur - _clip_off) else None,
                    "trim_dur": cdur,
                }
            )

    # Subtitles: merge every scene track onto the export timeline. The VTT
    # sidecar always ships for the in-app player; the libass burn is
    # best-effort (missing system fonts fail the filter) with a warning.
    from komaverse.subtitles import (  # noqa: PLC0415 — heavy sibling, render only
        SubSegment,
        parse_srt,
        segments_to_ass,
        segments_to_srt,
        segments_to_vtt,
    )

    merged_subs: list[tuple[int, SubSegment]] = []
    for scene in scenes:
        base = scene_starts[scene.id]
        for track in getattr(scene, "subtitle_tracks", None) or []:
            try:
                srt_path = storage.resolve(track.file_path)
                parsed = parse_srt(srt_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                warnings.append(f"subtitles on '{scene.name}' skipped ({exc})")
                continue
            lane = track.lane if track.lane is not None else 0
            merged_subs.extend(
                (lane, SubSegment(start=s.start + base, end=s.end + base, text=s.text))
                for s in parsed
            )
    merged_subs.sort(key=lambda it: it[1].start)

    vtt_url: str | None = None
    if merged_subs:
        vtt_rel = Path("exports") / project_id / f"{uuid_short()}.vtt"
        vtt_dest = settings.media_dir / vtt_rel
        vtt_dest.parent.mkdir(parents=True, exist_ok=True)
        vtt_dest.write_text(segments_to_vtt([s for _, s in merged_subs]), encoding="utf-8")
        vtt_url = f"/media/{vtt_rel.as_posix()}"

    export_id = uuid_short()
    rel = Path("exports") / project_id / f"{export_id}.mp4"
    dest = settings.media_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    silent = dest.with_name(f"{dest.stem}_silent.mp4") if tracks else dest

    def render_video(extra_params: list[str]) -> None:
        writer = imageio_ffmpeg.write_frames(
            str(silent),
            (w, h),
            pix_fmt_in="bgr24",
            pix_fmt_out="yuv420p",
            fps=fps,
            codec="libx264",
            quality=8,
            macro_block_size=None,
            output_params=["-movflags", "+faststart", *extra_params],
        )
        writer.send(None)
        done = 0

        overlay_caps: dict[int, object] = {}
        overlay_last: dict[int, np.ndarray] = {}
        for _oi, _ov in enumerate(overlays):
            if isinstance(_ov["src"], tuple):
                _, _vpath, _vdur = _ov["src"]
                overlay_caps[_oi] = cv2.VideoCapture(str(_vpath))
        def emit(frame: np.ndarray) -> None:
            nonlocal done
            t = done / fps
            for _oi, _ov in enumerate(overlays):
                if not (_ov["start"] <= t < _ov["end"]):
                    continue
                _clip = _ov["clip"]
                _n = max(int(round((_ov["end"] - _ov["start"]) * fps)), 1)
                _i = min(max(int((t - _ov["start"]) * fps), 0), _n - 1)
                _src = _ov["src"]
                if isinstance(_src, tuple):
                    _, _vpath, _vdur = _src
                    _got = None
                    _cap = overlay_caps.get(_oi)
                    if _cap is not None:
                        _ov_pic_off = max((_clip.offset or 0.0), 0.0)
                        _t_span = max(_vdur - _ov_pic_off, 0.1)
                        _t_src = _ov_pic_off + ((t - _ov["start"]) % _t_span) if _vdur > 0 else _ov_pic_off
                        _cap.set(cv2.CAP_PROP_POS_MSEC, _t_src * 1000)
                        _ok, _read = _cap.read()
                        if _ok and _read is not None:
                            _got = _read
                            overlay_last[_oi] = _read
                    if _got is None:
                        _got = overlay_last.get(_oi)
                    if _got is None:
                        _got = np.full((h, w, 3), PAPER_RGB[::-1], dtype=np.uint8)
                    frame = apply_filter(
                        finish_frame(
                            _got, _clip.motion, _i, _n, w, h, fps,
                            _clip.fade_in or 0.0, _clip.fade_out or 0.0,
                        ),
                        _clip.clip_filter or "none",
                    )
                else:
                    frame = still_frame(_clip, _src, _i, _n)
            writer.send(frame)
            done += 1
            if on_progress is not None:
                on_progress(done, total_frames)

        def still_frame(clip, src, i: int, n: int) -> np.ndarray:
            raw = src
            if clip.kind == "text" and (clip.text_anim or "none") != "none":
                # Entrance anims re-raster per frame; the resolved card is
                # the settled end state the thumbs show.
                raw = render_text_image(
                    clip.text_content or "",
                    size=clip.text_size or 64,
                    color=clip.text_color or "#ffffff",
                    position=clip.text_position or "center",
                    w=w,
                    h=h,
                    anim=clip.text_anim or "none",
                    progress=i / max(n - 1, 1),
                )
            out = finish_frame(
                raw, clip.motion, i, n, w, h, fps,
                clip.fade_in or 0.0, clip.fade_out or 0.0,
            )
            return apply_filter(out, clip.clip_filter or "none")

        def tail_frame(idx: int, j: int) -> np.ndarray:
            """Transition contributor: tail frame j of the overlap into
            clip idx (j < overlap[idx]), fade envelopes included."""
            clip, src, n = planned[idx]
            k = overlap[idx]
            pclip, psrc, pn = planned[idx - 1]
            if isinstance(psrc, tuple):
                _, pvpath, pvdur = psrc
                a_raw = read_video_frame(pvpath, pvdur, (pn - k + j) / fps, w, h)
            else:
                a_raw = psrc
            a = apply_filter(
                finish_frame(
                    a_raw, pclip.motion, pn - k + j, pn, w, h, fps,
                    pclip.fade_in or 0.0, pclip.fade_out or 0.0,
                ),
                pclip.clip_filter or "none",
            )
            if isinstance(src, tuple):
                _, vpath, vdur = src
                b_raw = read_video_frame(vpath, vdur, j / fps, w, h)
            else:
                b_raw = src
            b = apply_filter(
                finish_frame(
                    b_raw, clip.motion, j, n, w, h, fps,
                    clip.fade_in or 0.0, clip.fade_out or 0.0,
                ),
                clip.clip_filter or "none",
            )
            return blend_frames(a, b, j, k, clip.transition or "crossfade")

        try:
            for idx, (clip, src, n) in enumerate(planned):
                k_in = overlap[idx]
                k_out = overlap[idx + 1] if idx + 1 < len(overlap) else 0
                if k_in:
                    for j in range(k_in):
                        emit(tail_frame(idx, j))
                if isinstance(src, tuple):
                    _, vpath, vdur = src
                    cap = cv2.VideoCapture(str(vpath))
                    try:
                        last = None
                        for i in range(k_in, n - k_out):
                            t_out = i / fps
                            _pic_off = max((clip.offset or 0.0), 0.0)
                            _span = max(vdur - _pic_off, 0.1)
                            t_src = _pic_off + (t_out if t_out < _span else t_out % _span)
                            cap.set(cv2.CAP_PROP_POS_MSEC, t_src * 1000)
                            ok, frame = cap.read()
                            if ok and frame is not None:
                                last = frame
                            frame = last
                            if frame is None:
                                frame = np.full((h, w, 3), PAPER_RGB[::-1], dtype=np.uint8)
                            emit(apply_filter(
                                finish_frame(
                                    frame, clip.motion, i, n, w, h, fps,
                                    clip.fade_in or 0.0, clip.fade_out or 0.0,
                                ),
                                clip.clip_filter or "none",
                            ))
                    finally:
                        cap.release()
                    continue
                for i in range(k_in, n - k_out):
                    emit(still_frame(clip, src, i, n))
        finally:
            for _cap in overlay_caps.values():
                try:
                    _cap.release()
                except Exception:
                    pass
            writer.close()

    burned_subs = False
    if merged_subs:
        multi_lane = len({lane for lane, _ in merged_subs}) > 1
        subs_suffix = "_subs.ass" if multi_lane else "_subs.srt"
        merged_subs_file = dest.with_name(f"{dest.stem}{subs_suffix}")
        if multi_lane:
            # SRT has no per-track placement: S1 bottom, S2 top via ASS.
            merged_subs_file.write_text(segments_to_ass(merged_subs), encoding="utf-8")
        else:
            merged_subs_file.write_text(
                segments_to_srt([s for _, s in merged_subs]), encoding="utf-8"
            )
        escaped = merged_subs_file.as_posix().replace("\\", "/").replace(":", "\\:")
        try:
            render_video(["-vf", f"subtitles='{escaped}'"])
            burned_subs = True
        except Exception as exc:
            warnings.append(f"subtitle burn-in failed ({exc}); VTT sidecar still ships")
        finally:
            merged_subs_file.unlink(missing_ok=True)
    if not burned_subs:
        render_video([])

    audio_cues = 0
    if tracks:
        try:
            mix_audio_tracks(silent, tracks, video_dur, dest)
            audio_cues = cue_count if include_audio else 0
        except RuntimeError as exc:
            warnings.append(f"audio mux failed ({exc}); exported silent")
            silent.replace(dest)
        else:
            silent.unlink(missing_ok=True)
    return {
        "export_id": export_id,
        "url": f"/media/{rel.as_posix()}",
        "duration_sec": video_dur,
        "clips": len(resolved),
        "fps": fps,
        "width": w,
        "height": h,
        "warnings": warnings,
        "audio_cues": audio_cues,
        "subtitles_vtt_url": vtt_url,
    }
