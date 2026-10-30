"""Scenes / clips / frames API + synchronous MP4 export. Auth required;
all data scoped to the current user."""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from pathlib import Path

import cv2
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from komaverse import storage
from komaverse.api.usage import record_usage
from komaverse.auth import ensure_owner, get_current_user, own_project
from komaverse.config import settings
from komaverse.db import get_session, session_factory
from komaverse.db_models import (
    AudioCueRow,
    ClipRow,
    ExportReportRow,
    FrameRow,
    JobRow,
    LayerRow,
    PageRow,
    PanelRow,
    SceneRow,
    SequenceRow,
    SubtitleTrackRow,
    UserRow,
)
from komaverse.models import Clip, DrawFrame, ExportReport, Job, Scene, Sequence, uuid_short
from komaverse.observability import metrics
from komaverse.renderer import MAX_TOTAL_SEC, composite_frame_image, render_project

logger = logging.getLogger("komaverse.animate")

router = APIRouter(prefix="/api/v1")

CLIP_KINDS = ("panel", "frame", "media", "video", "ai")
MOTIONS = ("none", "zoom-in", "zoom-out", "pan-left", "pan-right")

MIN_FRAME_DIM = 8
MAX_FRAME_DIM = 4096
MAX_LAYERS = 64
MAX_BATCH_CLIPS = 200
THUMB_MAX_DIM = 320
MAX_LAYER_DATA_URL = 20 * 1024 * 1024  # per layer; canvas PNGs stay far below
# Must stay in sync with the canvas engine: every value here is a valid
# Canvas2D globalCompositeOperation. The renderer honors normal/multiply/screen
# and falls back to normal for the rest.
BLEND_MODES = (
    "normal",
    "multiply",
    "screen",
    "overlay",
    "darken",
    "lighten",
    "color-dodge",
    "color-burn",
    "hard-light",
    "soft-light",
    "difference",
    "exclusion",
    "hue",
    "saturation",
    "color",
    "luminosity",
)


def _check_dims(width: int | None, height: int | None) -> None:
    for label, value in (("width", width), ("height", height)):
        if value is None:
            continue
        if not MIN_FRAME_DIM <= value <= MAX_FRAME_DIM:
            raise HTTPException(
                400,
                f"{label} must be {MIN_FRAME_DIM}..{MAX_FRAME_DIM}px, got {value}",
            )


def _check_layers(layers: list[LayerPut]) -> None:
    if len(layers) > MAX_LAYERS:
        raise HTTPException(400, f"too many layers ({len(layers)} > {MAX_LAYERS})")
    for layer in layers:
        if not 0 <= layer.opacity <= 100:
            raise HTTPException(
                400, f"opacity must be 0..100, got {layer.opacity}"
            )
        if layer.blend_mode not in BLEND_MODES:
            raise HTTPException(400, f"unknown blend mode: {layer.blend_mode}")
        if layer.data_url and len(layer.data_url) > MAX_LAYER_DATA_URL:
            raise HTTPException(413, "layer pixels too large")


class SceneCreate(BaseModel):
    name: str = "Scene"


class ScenePatch(BaseModel):
    name: str | None = None
    order: int | None = None


class ClipCreate(BaseModel):
    kind: str = "panel"
    source_id: str = ""
    duration: float = 2.0
    motion: str = "none"


class ClipBatchCreate(BaseModel):
    clips: list[ClipCreate] = []


class ClipOrder(BaseModel):
    clip_ids: list[str] = []


class FrameOrder(BaseModel):
    frame_ids: list[str] = []


class ClipPatch(BaseModel):
    duration: float | None = None
    motion: str | None = None
    order: int | None = None
    start: float | None = None


class FrameCreate(BaseModel):
    name: str = ""
    width: int = 1920
    height: int = 1080
    sequence_id: str | None = None


class LayerPut(BaseModel):
    id: str | None = None
    name: str = "Layer"
    opacity: int = 100
    visible: bool = True
    locked: bool = False
    blend_mode: str = "normal"
    layer_order: int = 0
    data_url: str | None = None


class FramePut(BaseModel):
    name: str | None = None
    width: int | None = None
    height: int | None = None
    layers: list[LayerPut] | None = None


class ExportRequest(BaseModel):
    fps: int | None = None
    width: int | None = None
    height: int | None = None
    include_audio: bool = True
    scene_id: str | None = None


def _get_scene(session: Session, user: UserRow, scene_id: str) -> SceneRow:
    row = session.get(SceneRow, scene_id)
    if row is None:
        raise HTTPException(404, "scene not found")
    ensure_owner(session, user, row.project_id)
    return row


def _require_clip_source(session: Session, user: UserRow, kind: str, source_id: str) -> None:
    """Reject dangling or foreign clip sources at write time (never silently)."""
    if kind == "panel":
        row = session.get(PanelRow, source_id)
        project_id = row.page.project_id if row and row.page else None
    elif kind == "frame":
        row = session.get(FrameRow, source_id)
        project_id = row.project_id if row else None
    elif kind == "media":
        row = session.get(PageRow, source_id)
        project_id = row.project_id if row else None
    elif kind == "video":
        job = session.get(JobRow, source_id)
        if job is None or job.project_id is None or job.kind != "video":
            raise HTTPException(400, f"unknown video: {source_id}")
        if job.status != "done":
            raise HTTPException(400, f"video {source_id} is still {job.status}")
        row, project_id = job, job.project_id
    else:  # pragma: no cover — kind is validated before this runs
        raise HTTPException(400, f"unknown clip kind: {kind}")
    if row is None:
        raise HTTPException(400, f"unknown {kind}: {source_id}")
    if project_id is None:
        raise HTTPException(400, f"orphaned {kind}: {source_id}")
    try:
        ensure_owner(session, user, project_id)
    except HTTPException:
        raise HTTPException(400, f"unknown {kind}: {source_id}") from None


@router.post("/projects/{project_id}/scenes", response_model=Scene, status_code=201)
def create_scene(
    project_id: str,
    body: SceneCreate,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    project = own_project(session, user, project_id)
    existing = session.execute(
        select(SceneRow).where(SceneRow.project_id == project.id)
    )
    row = SceneRow(
        project_id=project.id,
        name=body.name or "Scene",
        order=len(existing.scalars().all()),
    )
    session.add(row)
    session.commit()
    return row.to_schema()


@router.get("/projects/{project_id}/scenes", response_model=list[Scene])
def list_scenes(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    project = own_project(session, user, project_id)
    res = session.execute(
        select(SceneRow)
        .where(SceneRow.project_id == project.id)
        .order_by(SceneRow.order)
    )
    return [r.to_schema() for r in res.scalars().all()]


@router.patch("/scenes/{scene_id}", response_model=Scene)
def patch_scene(
    scene_id: str,
    body: ScenePatch,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = _get_scene(session, user, scene_id)
    if body.name is not None and body.name.strip():
        row.name = body.name.strip()[:200]
    if body.order is not None:
        row.order = body.order
    session.commit()
    return row.to_schema()


@router.post("/scenes/{scene_id}/duplicate", response_model=Scene, status_code=201)
def duplicate_scene(
    scene_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Copy a timeline (clips, cues, subtitles) for an alternate cut."""
    row = _get_scene(session, user, scene_id)
    project = own_project(session, user, row.project_id)
    existing = session.execute(
        select(SceneRow).where(SceneRow.project_id == project.id)
    )
    copy = SceneRow(
        project_id=project.id,
        name=f"{row.name} copy"[:200] if row.name else "copy",
        order=len(existing.scalars().all()),
    )
    session.add(copy)
    session.flush()
    for clip in sorted(row.clips, key=lambda c: c.order):
        session.add(
            ClipRow(
                scene_id=copy.id,
                kind=clip.kind,
                source_id=clip.source_id,
                start=clip.start,
                duration=clip.duration,
                motion=clip.motion,
                order=clip.order,
            )
        )
    for cue in row.cues:
        session.add(
            AudioCueRow(
                scene_id=copy.id,
                audio_id=cue.audio_id,
                start_sec=cue.start_sec,
                offset_sec=cue.offset_sec,
                end_sec=cue.end_sec,
                lane=cue.lane,
                volume=cue.volume,
                fade_in=cue.fade_in,
                fade_out=cue.fade_out,
            )
        )
    session.flush()
    old_track = session.execute(
        select(SubtitleTrackRow).where(SubtitleTrackRow.scene_id == row.id)
    ).scalars().first()
    if old_track is not None:
        try:
            data = storage.resolve(old_track.file_path).read_bytes()
            rel, _url = storage.save_upload(project.id, "subtitles.srt", data)
            session.add(
                SubtitleTrackRow(scene_id=copy.id, lang=old_track.lang, file_path=rel)
            )
        except OSError:
            logger.warning("subtitle copy failed for scene %s", row.id)
    session.commit()
    return copy.to_schema()


@router.delete("/scenes/{scene_id}", status_code=204)
def delete_scene(
    scene_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = _get_scene(session, user, scene_id)
    session.delete(row)
    session.commit()


class SequenceCreate(BaseModel):
    name: str = "Sequence"


class SequencePatch(BaseModel):
    name: str | None = None
    order: int | None = None


def _sequence_rows(session: Session, project_id: str) -> list[SequenceRow]:
    return (
        session.execute(
            select(SequenceRow)
            .where(SequenceRow.project_id == project_id)
            .order_by(SequenceRow.index)
        )
        .scalars()
        .all()
    )


def _frame_rows(session: Session, project_id: str) -> list[FrameRow]:
    return (
        session.execute(
            select(FrameRow)
            .where(FrameRow.project_id == project_id)
            .order_by(FrameRow.index)
        )
        .scalars()
        .all()
    )


def _adopt_orphans(session: Session, project, first: SequenceRow) -> None:
    """Attach sequence-less frames to the given sequence, keeping order."""
    orphans = [f for f in _frame_rows(session, project.id) if f.sequence_id is None]
    if not orphans:
        return
    base = max([f.index for f in first.frames], default=-1) + 1
    for i, f in enumerate(sorted(orphans, key=lambda f: f.index)):
        f.sequence_id = first.id
        f.index = base + i
    session.flush()


def ensure_project_sequences(session: Session, project) -> list[SequenceRow]:
    """Auto-group legacy frames: every project ends up with ≥1 sequence.

    Projects created before sequences get a single "Sequence 1" holding
    their frames in flipbook order; orphan frames (no sequence) join the
    first sequence. Returns the sequences in order.
    """
    seqs = list(_sequence_rows(session, project.id))
    if not seqs:
        seqs = [
            SequenceRow(project_id=project.id, name="Sequence 1", index=0)
        ]
        session.add(seqs[0])
        session.flush()
    _adopt_orphans(session, project, seqs[0])
    return seqs


def _get_sequence(session: Session, user: UserRow, sequence_id: str) -> SequenceRow:
    row = session.get(SequenceRow, sequence_id)
    if row is None:
        raise HTTPException(404, "sequence not found")
    ensure_owner(session, user, row.project_id)
    return row


def _resolve_sequence(
    session: Session, user: UserRow, project_id: str, sequence=None
) -> SequenceRow:
    """SequenceRow by @sequence: mention, id, or name (first sequence fallback)."""
    project = own_project(session, user, project_id)
    seqs = ensure_project_sequences(session, project)
    ref = str(sequence or "").strip()
    m = re.search(r"@sequence:([A-Za-z0-9_-]+)", ref)
    if m:
        ref = m.group(1)
    for s in seqs:
        if s.id == ref:
            return s
    lowered = ref.lower()
    for s in seqs:
        if (s.name or "").lower() == lowered:
            return s
    if not ref:
        return seqs[0]
    if len(seqs) == 1:
        return seqs[0]
    names = "; ".join(f"'{s.name}' [sequence {s.id}]" for s in seqs)
    raise HTTPException(400, f"Unknown sequence '{sequence}'. Sequences: {names}.")


def _next_frame_index(session: Session, sequence_id: str) -> int:
    top = session.execute(
        select(FrameRow.index)
        .where(FrameRow.sequence_id == sequence_id)
        .order_by(FrameRow.index.desc())
        .limit(1)
    ).scalars().first()
    return (top + 1) if top is not None else 0


@router.post("/projects/{project_id}/sequences", response_model=Sequence, status_code=201)
def create_sequence(
    project_id: str,
    body: SequenceCreate,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    project = own_project(session, user, project_id)
    seqs = list(_sequence_rows(session, project.id))
    row = SequenceRow(
        project_id=project.id,
        name=(body.name or "Sequence").strip()[:200] or "Sequence",
        index=len(seqs),
    )
    session.add(row)
    session.flush()
    if not seqs:
        # First sequence adopts any pre-sequence frames.
        _adopt_orphans(session, project, row)
    session.commit()
    return row.to_schema()


@router.get("/projects/{project_id}/sequences", response_model=list[Sequence])
def list_sequences(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    project = own_project(session, user, project_id)
    return [s.to_schema() for s in _sequence_rows(session, project.id)]


@router.patch("/sequences/{sequence_id}", response_model=Sequence)
def patch_sequence(
    sequence_id: str,
    body: SequencePatch,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = _get_sequence(session, user, sequence_id)
    if body.name is not None and body.name.strip():
        row.name = body.name.strip()[:200]
    if body.order is not None:
        row.index = body.order
    session.commit()
    return row.to_schema()


@router.delete("/sequences/{sequence_id}", status_code=204)
def delete_sequence(
    sequence_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = _get_sequence(session, user, sequence_id)
    refs = session.execute(
        select(ClipRow).where(
            ClipRow.kind == "frame",
            ClipRow.source_id.in_([f.id for f in row.frames]),
        )
    ).scalars().all()
    if refs and row.frames:
        raise HTTPException(
            409,
            f"sequence is used by {len(refs)} clip(s); remove them first",
        )
    session.delete(row)  # frames + layers cascade via delete-orphan
    session.commit()


@router.post("/sequences/{sequence_id}/duplicate", response_model=Sequence, status_code=201)
def duplicate_sequence(
    sequence_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = _get_sequence(session, user, sequence_id)
    project = own_project(session, user, row.project_id)
    seqs = ensure_project_sequences(session, project)
    copy = SequenceRow(
        project_id=row.project_id,
        name=f"{row.name} copy"[:200] if row.name else "copy",
        index=len(seqs),
    )
    session.add(copy)
    session.flush()
    for frame in sorted(row.frames, key=lambda f: f.index):
        frame_copy = FrameRow(
            project_id=row.project_id,
            sequence_id=copy.id,
            name=frame.name,
            index=frame.index,
            width=frame.width,
            height=frame.height,
        )
        session.add(frame_copy)
        session.flush()
        for layer in sorted(frame.layers, key=lambda l: l.layer_order):
            session.add(
                LayerRow(
                    frame_id=frame_copy.id,
                    name=layer.name,
                    opacity=layer.opacity,
                    visible=layer.visible,
                    locked=layer.locked,
                    blend_mode=layer.blend_mode,
                    layer_order=layer.layer_order,
                    data_url=layer.data_url,
                )
            )
    session.commit()
    session.refresh(copy, ["frames"])
    return copy.to_schema()


@router.patch("/sequences/{sequence_id}/frames/order", response_model=list[DrawFrame])
def order_sequence_frames(
    sequence_id: str,
    body: FrameOrder,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Replace one sequence's frame order with the given id list (exact set)."""
    row = _get_sequence(session, user, sequence_id)
    by_id = {f.id: f for f in row.frames}
    if set(body.frame_ids) != set(by_id) or len(body.frame_ids) != len(by_id):
        raise HTTPException(400, "frame_ids must list exactly the sequence's frames")
    for index, frame_id in enumerate(body.frame_ids):
        by_id[frame_id].index = index
    session.commit()
    return [by_id[fid].to_schema() for fid in body.frame_ids]


@router.post("/scenes/{scene_id}/clips", response_model=Clip, status_code=201)
def add_clip(
    scene_id: str,
    body: ClipCreate,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    scene = _get_scene(session, user, scene_id)
    if body.kind not in CLIP_KINDS:
        raise HTTPException(400, f"unknown clip kind: {body.kind}")
    if body.motion not in MOTIONS:
        raise HTTPException(400, f"unknown motion: {body.motion}")
    if body.kind == "ai":
        raise HTTPException(400, "ai clips land in M4")
    if not body.source_id:
        raise HTTPException(400, "source_id is required")
    _require_clip_source(session, user, body.kind, body.source_id)
    existing = session.execute(select(ClipRow).where(ClipRow.scene_id == scene.id))
    row = ClipRow(
        scene_id=scene.id,
        kind=body.kind,
        source_id=body.source_id,
        duration=max(body.duration, 0.1),
        motion=body.motion,
        order=len(existing.scalars().all()),
    )
    session.add(row)
    session.commit()
    return row.to_schema()


@router.post("/scenes/{scene_id}/clips/batch", response_model=list[Clip])
def add_clips_batch(
    scene_id: str,
    body: ClipBatchCreate,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Add many clips in one call. All-or-nothing: any invalid entry
    rejects the batch before anything is stored."""
    scene = _get_scene(session, user, scene_id)
    if len(body.clips) > MAX_BATCH_CLIPS:
        raise HTTPException(400, f"too many clips ({len(body.clips)} > {MAX_BATCH_CLIPS})")
    for entry in body.clips:
        if entry.kind not in CLIP_KINDS:
            raise HTTPException(400, f"unknown clip kind: {entry.kind}")
        if entry.motion not in MOTIONS:
            raise HTTPException(400, f"unknown motion: {entry.motion}")
        if entry.kind == "ai":
            raise HTTPException(400, "ai clips land in M4")
        if not entry.source_id:
            raise HTTPException(400, "source_id is required")
        _require_clip_source(session, user, entry.kind, entry.source_id)
    existing = session.execute(select(ClipRow).where(ClipRow.scene_id == scene.id))
    order = len(existing.scalars().all())
    rows: list[ClipRow] = []
    for entry in body.clips:
        row = ClipRow(
            scene_id=scene.id,
            kind=entry.kind,
            source_id=entry.source_id,
            duration=max(entry.duration, 0.1),
            motion=entry.motion,
            order=order,
        )
        order += 1
        session.add(row)
        rows.append(row)
    session.commit()
    return [r.to_schema() for r in rows]


@router.patch("/scenes/{scene_id}/clips/order", response_model=list[Clip])
def order_clips(
    scene_id: str,
    body: ClipOrder,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Replace the scene's clip order with the given id list (exact set)."""
    scene = _get_scene(session, user, scene_id)
    current = session.execute(
        select(ClipRow).where(ClipRow.scene_id == scene.id)
    ).scalars().all()
    by_id = {c.id: c for c in current}
    if set(body.clip_ids) != set(by_id) or len(body.clip_ids) != len(by_id):
        raise HTTPException(400, "clip_ids must list exactly the scene's clips")
    for order, clip_id in enumerate(body.clip_ids):
        by_id[clip_id].order = order
    session.commit()
    return [by_id[cid].to_schema() for cid in body.clip_ids]


@router.patch("/clips/{clip_id}", response_model=Clip)
def patch_clip(
    clip_id: str,
    body: ClipPatch,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(ClipRow, clip_id)
    if row is None:
        raise HTTPException(404, "clip not found")
    ensure_owner(session, user, row.scene.project_id)
    if body.duration is not None:
        row.duration = max(body.duration, 0.1)
    if body.motion is not None:
        if body.motion not in MOTIONS:
            raise HTTPException(400, f"unknown motion: {body.motion}")
        row.motion = body.motion
    if body.order is not None:
        row.order = body.order
    if body.start is not None:
        row.start = body.start
    session.commit()
    return row.to_schema()


@router.delete("/clips/{clip_id}", status_code=204)
def delete_clip(
    clip_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(ClipRow, clip_id)
    if row is None:
        raise HTTPException(404, "clip not found")
    ensure_owner(session, user, row.scene.project_id)
    session.delete(row)
    session.commit()


@router.post("/projects/{project_id}/frames", response_model=DrawFrame, status_code=201)
def create_frame(
    project_id: str,
    body: FrameCreate,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    _check_dims(body.width, body.height)
    project = own_project(session, user, project_id)
    seq = _resolve_sequence(session, user, project.id, body.sequence_id)
    row = FrameRow(
        project_id=project.id,
        sequence_id=seq.id,
        name=body.name,
        index=_next_frame_index(session, seq.id),
        width=body.width,
        height=body.height,
    )
    session.add(row)
    session.commit()
    return row.to_schema()


@router.patch("/projects/{project_id}/frames/order", response_model=list[DrawFrame])
def order_frames(
    project_id: str,
    body: FrameOrder,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Replace one sequence's frame order with the given id list (exact set).

    Legacy project-wide route: the ids must all belong to a single
    sequence and match it exactly (single-sequence projects behave as
    before). Prefer PATCH /sequences/{id}/frames/order.
    """
    project = own_project(session, user, project_id)
    current = session.execute(
        select(FrameRow).where(FrameRow.project_id == project.id)
    ).scalars().all()
    by_id = {f.id: f for f in current}
    if len({f.sequence_id for f in current}) > 1:
        raise HTTPException(400, "frame_ids span several sequences — order frames within one sequence")
    if set(body.frame_ids) != set(by_id) or len(body.frame_ids) != len(by_id):
        seq_ids = {by_id[fid].sequence_id for fid in body.frame_ids if fid in by_id}
        if len(seq_ids) == 1:
            seq_id = next(iter(seq_ids))
            seq_frames = [f for f in current if f.sequence_id == seq_id]
            seq_by_id = {f.id: f for f in seq_frames}
            if set(body.frame_ids) == set(seq_by_id) and len(body.frame_ids) == len(seq_by_id):
                for index, frame_id in enumerate(body.frame_ids):
                    seq_by_id[frame_id].index = index
                session.commit()
                return [seq_by_id[fid].to_schema() for fid in body.frame_ids]
        raise HTTPException(400, "frame_ids must list exactly the sequence's frames")
    for index, frame_id in enumerate(body.frame_ids):
        by_id[frame_id].index = index
    session.commit()
    return [by_id[fid].to_schema() for fid in body.frame_ids]


@router.get("/projects/{project_id}/frames", response_model=list[DrawFrame])
def list_frames(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    project = own_project(session, user, project_id)
    res = session.execute(
        select(FrameRow)
        .where(FrameRow.project_id == project.id)
        .order_by(FrameRow.index)
    )
    return [r.to_schema() for r in res.scalars().all()]


@router.get("/frames/{frame_id}", response_model=DrawFrame)
def get_frame(
    frame_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(FrameRow, frame_id)
    if row is None:
        raise HTTPException(404, "frame not found")
    ensure_owner(session, user, row.project_id)
    return row.to_schema()


@router.put("/frames/{frame_id}", response_model=DrawFrame)
def put_frame(
    frame_id: str,
    body: FramePut,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(FrameRow, frame_id)
    if row is None:
        raise HTTPException(404, "frame not found")
    ensure_owner(session, user, row.project_id)
    _check_dims(body.width, body.height)
    if body.name is not None:
        row.name = body.name
    if body.width is not None:
        row.width = body.width
    if body.height is not None:
        row.height = body.height
    if body.layers is not None:
        _check_layers(body.layers)
        session.execute(delete(LayerRow).where(LayerRow.frame_id == row.id))
        for layer in body.layers:
            session.add(
                LayerRow(
                    id=layer.id or uuid_short(),
                    frame_id=row.id,
                    name=layer.name,
                    opacity=layer.opacity,
                    visible=layer.visible,
                    locked=layer.locked,
                    blend_mode=layer.blend_mode,
                    layer_order=layer.layer_order,
                    data_url=layer.data_url,
                )
            )
    session.commit()
    session.refresh(row, ["layers"])
    return row.to_schema()


@router.delete("/frames/{frame_id}", status_code=204)
def delete_frame(
    frame_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(FrameRow, frame_id)
    if row is None:
        raise HTTPException(404, "frame not found")
    ensure_owner(session, user, row.project_id)
    refs = session.execute(
        select(ClipRow).where(
            ClipRow.kind == "frame", ClipRow.source_id == row.id
        )
    ).scalars().all()
    if refs:
        raise HTTPException(
            409, f"frame is used by {len(refs)} clip(s); remove them first"
        )
    session.delete(row)  # layers cascade via delete-orphan
    session.commit()


@router.post("/frames/{frame_id}/duplicate", response_model=DrawFrame, status_code=201)
def duplicate_frame(
    frame_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(FrameRow, frame_id)
    if row is None:
        raise HTTPException(404, "frame not found")
    ensure_owner(session, user, row.project_id)
    if row.sequence_id is None:
        project = own_project(session, user, row.project_id)
        row.sequence_id = ensure_project_sequences(session, project)[0].id
        session.flush()
    copy = FrameRow(
        project_id=row.project_id,
        sequence_id=row.sequence_id,
        name=f"{row.name} copy" if row.name else "copy",
        index=_next_frame_index(session, row.sequence_id),
        width=row.width,
        height=row.height,
    )
    session.add(copy)
    session.flush()
    for layer in sorted(row.layers, key=lambda l: l.layer_order):
        session.add(
            LayerRow(
                id=uuid_short(),
                frame_id=copy.id,
                name=layer.name,
                opacity=layer.opacity,
                visible=layer.visible,
                locked=layer.locked,
                blend_mode=layer.blend_mode,
                layer_order=layer.layer_order,
                data_url=layer.data_url,
            )
        )
    session.commit()
    session.refresh(copy, ["layers"])
    return copy.to_schema()


def parse_media_source(text: str):
    """@-mention or 'page N' into (kind, id).

    Panels ride along: a panel crops to a frame as well as clipping to
    the timeline.
    """
    m = re.search(r"@(media|gen|frame|panel):([A-Za-z0-9_-]+)", text)
    if m:
        return (m.group(1), m.group(2))
    m = re.search(r"\b(?:page|pg)\s*(\d+)\b", text)
    if m:
        return ("page_index", m.group(1))
    return None


def load_media_image(session: Session, user: UserRow, project_id: str, source):
    """Resolve a media source to (png_bytes, label). Raises HTTPException."""
    project = own_project(session, user, project_id)
    parsed = parse_media_source(source) if isinstance(source, str) else None
    if isinstance(source, str) and parsed is None:
        parsed = ("id", source.strip())
    kind, ref = parsed
    if kind == "id":
        page = session.get(PageRow, ref)
        if page is not None and page.project_id == project.id:
            kind, ref = "page", ref
        else:
            job = session.get(JobRow, ref)
            if (
                job is not None
                and job.user_id == user.id
                and job.project_id == project.id
            ):
                kind = "gen"
            else:
                row = session.get(FrameRow, ref)
                if row is not None and row.project_id == project.id:
                    kind = "frame"
                else:
                    raise HTTPException(
                        400,
                        f"Unknown media {ref} - use @media:/@gen:/@frame:/@panel: or 'page N'.",
                    )
    if kind == "page_index":
        pages = session.execute(
            select(PageRow)
            .where(PageRow.project_id == project.id)
            .order_by(PageRow.index)
        ).scalars().all()
        try:
            i = int(ref) - 1
        except (TypeError, ValueError):
            i = -1
        if not 0 <= i < len(pages):
            raise HTTPException(
                400, f"Page {ref} does not exist - this project has {len(pages)}."
            )
        kind, ref = "page", pages[i].id
    if kind == "page" or kind == "media":
        page = session.get(PageRow, ref)
        if page is None or page.project_id != project.id:
            raise HTTPException(400, f"Unknown scan page {ref} in this project.")
        try:
            data = storage.resolve(page.image_path).read_bytes()
        except OSError:
            raise HTTPException(400, f"Scan file for page {page.index + 1} is missing.")
        return (data, f"scan page {page.index + 1}")
    if kind == "panel":
        panel = session.get(PanelRow, ref)
        page = session.get(PageRow, panel.page_id) if panel else None
        if panel is None or page is None or page.project_id != project.id:
            raise HTTPException(400, f"Unknown panel {ref} in this project.")
        try:
            img = Image.open(
                io.BytesIO(storage.resolve(page.image_path).read_bytes())
            ).convert("RGB")
        except Exception:
            raise HTTPException(400, f"Scan file for page {page.index + 1} is missing.")
        w, h = img.size
        x0, y0 = max(0, int(panel.x)), max(0, int(panel.y))
        x1, y1 = min(w, int(panel.x + panel.w)), min(h, int(panel.y + panel.h))
        if x1 <= x0 or y1 <= y0:
            raise HTTPException(400, f"Panel {ref} has an empty box.")
        buf = io.BytesIO()
        img.crop((x0, y0, x1, y1)).save(buf, format="PNG")
        return (
            buf.getvalue(),
            f"panel {panel.reading_order + 1} on page {page.index + 1}",
        )
    if kind == "gen":
        job = session.get(JobRow, ref)
        if job is None or job.user_id != user.id or job.project_id != project.id:
            raise HTTPException(400, f"Unknown generation {ref}.")
        if job.kind != "image":
            raise HTTPException(
                400,
                "Only image generations can become frames - video renders stay in the Generate panel.",
            )
        if job.status != "done" or not job.result_url:
            raise HTTPException(
                400, f"Job {ref} is still {job.status} - ask me to check it when done."
            )
        rel = job.result_url.split("/media/", 1)[-1]
        try:
            data = storage.resolve(rel).read_bytes()
        except OSError:
            raise HTTPException(400, f"Artifact for job {ref} is missing.")
        return (data, f"generation '{job.prompt[:40]}'")
    if kind == "frame":
        row = session.get(FrameRow, ref)
        if row is None or row.project_id != project.id:
            raise HTTPException(400, f"Unknown frame {ref} in this project.")
        buf = io.BytesIO()
        composite_frame_image(row).convert("RGB").save(buf, format="PNG")
        return (buf.getvalue(), f"frame '{row.name or 'Untitled'}'")
    raise HTTPException(
        400, f"Unknown media {ref} - use @media:/@gen:/@frame:/@panel: or 'page N'."
    )


def frame_from_media_source(
    session: Session,
    user: UserRow,
    project_id: str,
    source,
    name: str | None = None,
    sequence=None,
) -> DrawFrame:
    """Create a draw frame from a scan page, panel, generation, or frame.

    Shared by the agent tool, MCP, and the from-media route so manual and
    automatic framing behave identically.
    """
    data, label = load_media_image(session, user, project_id, source)
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        fmt = (img.format or "PNG").upper()
    except Exception:
        raise HTTPException(400, f"Could not decode {label}.")
    longest = max(w, h)
    if longest > MAX_FRAME_DIM:
        scale = MAX_FRAME_DIM / longest
        w, h = max(8, round(w * scale)), max(8, round(h * scale))
    w, h = max(8, w), max(8, h)
    if fmt not in ("PNG", "JPEG", "JPG"):
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        data, fmt = buf.getvalue(), "PNG"
    mime = "image/jpeg" if fmt in ("JPEG", "JPG") else "image/png"
    schema = create_frame(
        project_id,
        FrameCreate(
            name=(name or f"Media: {label}")[:200],
            width=w,
            height=h,
            sequence_id=str(sequence) if sequence else None,
        ),
        session,
        user,
    )
    b64 = base64.b64encode(data).decode()
    session.add(
        LayerRow(
            frame_id=schema.id,
            name="media",
            layer_order=0,
            data_url=f"data:{mime};base64,{b64}",
        )
    )
    session.commit()
    fresh = session.get(FrameRow, schema.id)
    return fresh.to_schema() if fresh is not None else schema


class FrameFromMedia(BaseModel):
    source: str = ""  # @media:/@gen:/@frame:/@panel: id, or 'page N'
    name: str | None = None
    sequence_id: str | None = None


VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov")
MAX_VIDEO_FRAMES = 24


class VideoToFrames(BaseModel):
    sequence_id: str | None = None
    max_frames: int = 12
    width: int = 960
    height: int = 540
    name: str | None = None


def _read_video_job(session: Session, user: UserRow, job_id: str) -> tuple[JobRow, Path]:
    """Finished video job the user owns + its file path (or 4xx)."""
    ref = str(job_id or "").strip()
    for prefix in ("@gen:", "@media:"):
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
            break
    job = session.get(JobRow, ref)
    if job is None or job.kind != "video":
        raise HTTPException(404, "video not found")
    if job.project_id:
        own_project(session, user, job.project_id)
    elif job.user_id != user.id:
        raise HTTPException(404, "video not found")
    if job.status != "done" or not job.result_url:
        raise HTTPException(400, f"video {job.id} is still {job.status} - wait until done")
    path = storage.resolve(job.result_url.split("/media/", 1)[-1])
    if not path.is_file():
        raise HTTPException(404, "video file missing")
    return job, path


@router.post(
    "/projects/{project_id}/frames/from-media",
    response_model=DrawFrame,
    status_code=201,
)
def frame_from_media(
    project_id: str,
    body: FrameFromMedia,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Manual framing: the UI twin of the agent's frame-from-media tool."""
    return frame_from_media_source(
        session, user, project_id, body.source, body.name, body.sequence_id
    )


@router.post("/projects/{project_id}/videos/upload", response_model=Job, status_code=201)
def upload_video(
    project_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Upload a video file so it can join timelines and the draw studio."""
    project = own_project(session, user, project_id)
    ctype = (file.content_type or "").lower()
    name = (file.filename or "").lower()
    if not (ctype.startswith("video/") or name.endswith(VIDEO_EXTENSIONS)):
        raise HTTPException(400, "file must be a video (mp4/webm/mov)")
    try:
        data = storage.read_upload_capped(
            file.file, settings.upload_max_mb * 1024 * 1024
        )
    except storage.UploadTooLargeError:
        raise HTTPException(413, "file too large") from None
    if not data:
        raise HTTPException(400, "empty file")
    try:
        storage.check_quota(project.id, len(data))
    except storage.QuotaExceededError as exc:
        raise HTTPException(413, str(exc)) from None
    rel, url = storage.save_upload(project.id, file.filename or "video.mp4", data)
    width, height, seconds = 0, 0, 0.0
    try:
        cap = cv2.VideoCapture(str(storage.resolve(rel)))
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            seconds = round(total / fps, 2) if fps > 0 else 0.0
        finally:
            cap.release()
    except Exception:
        logger.debug("video probe failed", exc_info=True)
    job = JobRow(
        user_id=user.id,
        project_id=project.id,
        kind="video",
        prompt=file.filename or "upload",
        provider="upload",
        width=width,
        height=height,
        seconds=seconds,
        status="done",
        progress=1.0,
        result_url=url,
    )
    session.add(job)
    session.commit()
    return job.to_schema()


@router.post("/jobs/{job_id}/frames", response_model=list[DrawFrame], status_code=201)
def video_to_frames(
    job_id: str,
    body: VideoToFrames,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Explode a finished video into draw frames, spread across its length.

    Each frame lands in the given sequence (or the project's first one)
    with the still as a fitted "video" layer — a starting point to draw
    over, rotoscope-style.
    """
    job, path = _read_video_job(session, user, job_id)
    if job.project_id is None:
        raise HTTPException(400, "attach the video to a project first")
    project = own_project(session, user, job.project_id)
    _check_dims(body.width, body.height)
    seq = _resolve_sequence(session, user, project.id, body.sequence_id)
    cap = cv2.VideoCapture(str(path))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            raise HTTPException(400, "could not read any frames from the video")
        want = min(max(int(body.max_frames or 1), 1), MAX_VIDEO_FRAMES, total)
        picks = (
            [round(i * (total - 1) / (want - 1)) for i in range(want)]
            if want > 1
            else [0]
        )
        base = (body.name or job.prompt or "Video").strip()[:120] or "Video"
        out: list[DrawFrame] = []
        for k, pos in enumerate(picks):
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ok, bgr = cap.read()
            if not ok or bgr is None:
                continue
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb).convert("RGBA")
            scale = min(body.width / max(1, img.width), body.height / max(1, img.height))
            nw, nh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
            canvas = Image.new("RGBA", (body.width, body.height), (0, 0, 0, 0))
            canvas.paste(img.resize((nw, nh)), ((body.width - nw) // 2, (body.height - nh) // 2))
            buf = io.BytesIO()
            canvas.save(buf, format="PNG")
            data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            row = FrameRow(
                project_id=project.id,
                sequence_id=seq.id,
                name=f"{base} {k + 1}"[:200],
                index=_next_frame_index(session, seq.id),
                width=body.width,
                height=body.height,
            )
            session.add(row)
            session.flush()
            session.add(
                LayerRow(
                    frame_id=row.id,
                    name="video",
                    opacity=100,
                    visible=True,
                    locked=False,
                    blend_mode="normal",
                    layer_order=0,
                    data_url=data_url,
                )
            )
            session.flush()
            out.append(row.to_schema())
    finally:
        cap.release()
    if not out:
        raise HTTPException(400, "could not read any frames from the video")
    session.commit()
    return out


@router.get("/frames/{frame_id}/thumbnail")
def frame_thumbnail(
    frame_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
    px: int = THUMB_MAX_DIM,
):
    """Contact-sheet thumbnail: composited frame, longest side `px`
    (default THUMB_MAX_DIM, clamped 64..1600). Thumbs stay small; the
    preview player asks for more."""
    row = session.get(FrameRow, frame_id)
    if row is None:
        raise HTTPException(404, "frame not found")
    ensure_owner(session, user, row.project_id)
    img = composite_frame_image(row)
    side = min(max(px, 64), 1600)
    img.thumbnail((side, side))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.post("/projects/{project_id}/export", response_model=Job, status_code=202)
def export_project(
    project_id: str,
    body: ExportRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Queue an MP4 render (SPEC §Export). Poll GET /ai/jobs/{id}, then read
    GET /exports/{id} for the report (warnings, actual duration, download)."""
    project = own_project(session, user, project_id)
    if body.scene_id:
        scene = _get_scene(session, user, body.scene_id)
        if scene.project_id != project.id:
            raise HTTPException(400, "scene belongs to another project")
        scenes = [scene]
    else:
        scenes = [s for s in project.scenes if s.clips]
        if len(scenes) > 1:
            names = "; ".join(f"'{s.name}' [scene {s.id}]" for s in scenes)
            raise HTTPException(
                400,
                f"Which timeline? The project has {len(scenes)} timelines with clips — pick one: {names}.",
            )
    clips = [c for s in scenes for c in s.clips]
    total = sum(c.duration for c in clips)
    if not clips or total <= 0:
        raise HTTPException(400, "nothing to render: add clips first")
    if total > MAX_TOTAL_SEC:
        raise HTTPException(400, f"project too long ({total:.0f}s > {MAX_TOTAL_SEC:.0f}s)")
    job = JobRow(
        user_id=user.id,
        project_id=project.id,
        kind="export",
        prompt=json.dumps(
            {
                "fps": body.fps,
                "width": body.width,
                "height": body.height,
                "include_audio": body.include_audio,
                "scene_id": body.scene_id,
            }
        ),
        provider="local",
        width=body.width or 0,
        height=body.height or 0,
    )
    session.add(job)
    session.commit()
    background.add_task(run_export_job, job.id)
    return job.to_schema()


def run_export_job(job_id: str, factory=None) -> None:
    """Execute one export job (called in a background task)."""
    factory = factory or session_factory()
    with factory() as session:
        job = session.get(JobRow, job_id)
        if job is None:
            return
        job.status = "running"
        job.progress = 0.0
        session.commit()
        try:
            params = json.loads(job.prompt or "{}")
        except ValueError:
            params = {}
        last_band = -1

        def on_progress(done: int, total_count: int) -> None:
            nonlocal last_band
            band = int(20 * done / max(total_count, 1))
            if band != last_band:
                last_band = band
                job.progress = round(done / max(total_count, 1), 3)
                session.commit()

        try:
            out = render_project(
                job.project_id,
                session,
                params.get("fps"),
                params.get("width"),
                params.get("height"),
                include_audio=params.get("include_audio", True),
                on_progress=on_progress,
                scene_id=params.get("scene_id"),
            )
        except ValueError as exc:
            job.status = "failed"
            job.error = str(exc)
            session.commit()
            metrics.record_job("failed")
            record_usage(session, job.user_id, "export", "failed")
            return
        except Exception as exc:  # noqa: BLE001 — job must never hang
            job.status = "failed"
            job.error = f"unexpected: {exc}"
            session.commit()
            metrics.record_job("failed")
            record_usage(session, job.user_id, "export", "failed")
            return
        job.status = "done"
        job.progress = 1.0
        job.result_url = out["url"]
        session.commit()
        session.add(
            ExportReportRow(
                job_id=job.id,
                url=out["url"],
                warnings_json=json.dumps(out["warnings"]),
                rendered_sec=out["duration_sec"],
                clips_rendered=out["clips"],
                fps=out["fps"],
                width=out["width"],
                height=out["height"],
                audio_cues=out.get("audio_cues", 0),
                subtitles_vtt_url=out.get("subtitles_vtt_url"),
            )
        )
        session.commit()
        metrics.record_job("done")
        metrics.record_export()
        record_usage(
            session, job.user_id, "export", f"{out['width']}x{out['height']}@{out['fps']}"
        )


def _get_export_job(session: Session, user: UserRow, job_id: str) -> JobRow:
    job = session.get(JobRow, job_id)
    if job is None or job.user_id != user.id or job.kind != "export":
        raise HTTPException(404, "export not found")
    return job


@router.get("/exports/{job_id}", response_model=ExportReport)
def get_export_report(
    job_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    _get_export_job(session, user, job_id)
    report = session.get(ExportReportRow, job_id)
    if report is None:
        raise HTTPException(404, "export report not ready")
    return report.to_schema()


@router.get("/exports/{job_id}/download")
def download_export(
    job_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    job = _get_export_job(session, user, job_id)
    if job.status != "done" or not job.result_url:
        raise HTTPException(404, "export not ready")
    path = settings.media_dir / job.result_url.removeprefix("/media/")
    if not path.is_file():
        raise HTTPException(404, "export file missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)
