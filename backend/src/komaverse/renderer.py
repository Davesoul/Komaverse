"""Project renderer: scenes/clips/cues → H.264 + AAC MP4.

Clip sources: panel crops (page image + box), frame composites (layer PNGs),
page stills (kind=media, source_id=page id), video files (kind=video,
source_id=finished generation job id; looped to the clip length, Ken Burns
moves apply over the moving picture). Motion: none, zoom-in, zoom-out,
pan-left, pan-right, pan-up, pan-down. Per-clip fade in/out plus
transitions into a clip (crossfade, fade-black, wipe-left, impact)
overlap the boundary frames. Timeline audio cues are mixed in a second ffmpeg pass
(adelay/volume/afade per cue, amix, AAC). Encoding via the imageio-ffmpeg
bundled binary (libx264/yuv420p/faststart) so output plays everywhere.
"""

from __future__ import annotations

import base64
import io
import logging
import subprocess
import tempfile
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageChops
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


def cover_resize(src: np.ndarray, w: int, h: int) -> np.ndarray:
    """Scale src to cover (w, h), then center-crop."""
    sh, sw = src.shape[:2]
    scale = max(w / sw, h / sh)
    resized = cv2.resize(src, (int(sw * scale) + 1, int(sh * scale) + 1))
    rh, rw = resized.shape[:2]
    x, y = (rw - w) // 2, (rh - h) // 2
    return resized[y : y + h, x : x + w]


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
    """Cover-crop, Ken Burns motion, and fade envelope for one frame."""
    base = cover_resize(frame, w * 2, h * 2)
    out = motion_frame(base, w, h, i / max(n - 1, 1), motion or "none")
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



def mix_audio_tracks(
    silent: Path, tracks: list[dict], video_dur: float, dest: Path
) -> None:
    """Mix timeline tracks onto the silent render (AAC, video stream copied).

    Each track: path, start_sec, offset_sec, volume, fade_in, fade_out,
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
        parts = [
            "aresample=48000",
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
        ordered = sorted(scene.clips, key=lambda c: c.order)
        clips.extend(ordered)
        offset += sum(c.duration for c in ordered)
    total = offset
    if not clips or total <= 0:
        raise ValueError("nothing to render: add clips first")
    if total > MAX_TOTAL_SEC:
        raise ValueError(f"project too long ({total:.0f}s > {MAX_TOTAL_SEC:.0f}s)")

    # Pass 1: resolve everything up front so skips are warned, never silent.
    # Stills resolve to one image; videos resolve to (tag, file, duration).
    resolved: list[tuple[ClipRow, object]] = []
    for clip in clips:
        if clip.kind == "video":
            vid = resolve_clip_video(clip, session)
            if vid is None:
                msg = f"skipped clip {clip.id} (video): unresolvable source"
                logger.warning(msg)
                warnings.append(msg)
                continue
            resolved.append((clip, ("video", *vid)))
            continue
        src = resolve_clip_image(clip, session)
        if src is None or src.size == 0:
            msg = f"skipped clip {clip.id} ({clip.kind}): unresolvable source"
            logger.warning(msg)
            warnings.append(msg)
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
                        "end_sec": max(end - max(cue.fade_out, 0), 0),
                    }
                )

    # Subtitles: merge every scene track onto the export timeline. The VTT
    # sidecar always ships for the in-app player; the libass burn is
    # best-effort (missing system fonts fail the filter) with a warning.
    from komaverse.subtitles import (  # noqa: PLC0415 — heavy sibling, render only
        SubSegment,
        parse_srt,
        segments_to_srt,
        segments_to_vtt,
    )

    merged_subs: list[SubSegment] = []
    for scene in scenes:
        track = scene.subtitle_track
        if track is None:
            continue
        try:
            srt_path = storage.resolve(track.file_path)
            parsed = parse_srt(srt_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            warnings.append(f"subtitles on '{scene.name}' skipped ({exc})")
            continue
        base = scene_starts[scene.id]
        merged_subs.extend(
            SubSegment(start=s.start + base, end=s.end + base, text=s.text)
            for s in parsed
        )
    merged_subs.sort(key=lambda s: s.start)

    vtt_url: str | None = None
    if merged_subs:
        vtt_rel = Path("exports") / project_id / f"{uuid_short()}.vtt"
        vtt_dest = settings.media_dir / vtt_rel
        vtt_dest.parent.mkdir(parents=True, exist_ok=True)
        vtt_dest.write_text(segments_to_vtt(merged_subs), encoding="utf-8")
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

        def emit(frame: np.ndarray) -> None:
            nonlocal done
            writer.send(frame)
            done += 1
            if on_progress is not None:
                on_progress(done, total_frames)

        def still_frame(clip, src, i: int, n: int) -> np.ndarray:
            return finish_frame(
                src, clip.motion, i, n, w, h, fps,
                clip.fade_in or 0.0, clip.fade_out or 0.0,
            )

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
            a = finish_frame(
                a_raw, pclip.motion, pn - k + j, pn, w, h, fps,
                pclip.fade_in or 0.0, pclip.fade_out or 0.0,
            )
            if isinstance(src, tuple):
                _, vpath, vdur = src
                b_raw = read_video_frame(vpath, vdur, j / fps, w, h)
            else:
                b_raw = src
            b = finish_frame(
                b_raw, clip.motion, j, n, w, h, fps,
                clip.fade_in or 0.0, clip.fade_out or 0.0,
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
                            t_src = t_out if t_out < vdur else t_out % vdur
                            cap.set(cv2.CAP_PROP_POS_MSEC, t_src * 1000)
                            ok, frame = cap.read()
                            if ok and frame is not None:
                                last = frame
                            frame = last
                            if frame is None:
                                frame = np.full((h, w, 3), PAPER_RGB[::-1], dtype=np.uint8)
                            emit(finish_frame(
                                frame, clip.motion, i, n, w, h, fps,
                                clip.fade_in or 0.0, clip.fade_out or 0.0,
                            ))
                    finally:
                        cap.release()
                    continue
                for i in range(k_in, n - k_out):
                    emit(still_frame(clip, src, i, n))
        finally:
            writer.close()

    burned_subs = False
    if merged_subs:
        merged_srt = dest.with_name(f"{dest.stem}_subs.srt")
        merged_srt.write_text(segments_to_srt(merged_subs), encoding="utf-8")
        escaped = merged_srt.as_posix().replace("\\", "/").replace(":", "\\:")
        try:
            render_video(["-vf", f"subtitles='{escaped}'"])
            burned_subs = True
        except Exception as exc:
            warnings.append(f"subtitle burn-in failed ({exc}); VTT sidecar still ships")
        finally:
            merged_srt.unlink(missing_ok=True)
    if not burned_subs:
        render_video([])

    audio_cues = 0
    if tracks:
        try:
            mix_audio_tracks(silent, tracks, video_dur, dest)
            audio_cues = len(tracks)
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
