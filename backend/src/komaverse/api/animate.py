"""Scenes / clips / frames API + synchronous MP4 export. Auth required;
all data scoped to the current user."""

from __future__ import annotations

import io
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from komaverse.auth import ensure_owner, get_current_user, own_project
from komaverse.config import settings
from komaverse.db import get_session, session_factory
from komaverse.db_models import (
    ClipRow,
    ExportReportRow,
    FrameRow,
    JobRow,
    LayerRow,
    PageRow,
    PanelRow,
    SceneRow,
    UserRow,
)
from komaverse.models import Clip, DrawFrame, ExportReport, Job, Scene, uuid_short
from komaverse.observability import metrics
from komaverse.renderer import MAX_TOTAL_SEC, composite_frame_image, render_project

logger = logging.getLogger("komaverse.animate")

router = APIRouter(prefix="/api/v1")

CLIP_KINDS = ("panel", "frame", "media", "ai")
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


@router.delete("/scenes/{scene_id}", status_code=204)
def delete_scene(
    scene_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = _get_scene(session, user, scene_id)
    session.delete(row)
    session.commit()


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
    existing = session.execute(
        select(FrameRow).where(FrameRow.project_id == project.id)
    )
    row = FrameRow(
        project_id=project.id,
        name=body.name,
        index=len(existing.scalars().all()),
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
    """Replace the project's frame order with the given id list (exact set)."""
    project = own_project(session, user, project_id)
    current = session.execute(
        select(FrameRow).where(FrameRow.project_id == project.id)
    ).scalars().all()
    by_id = {f.id: f for f in current}
    if set(body.frame_ids) != set(by_id) or len(body.frame_ids) != len(by_id):
        raise HTTPException(400, "frame_ids must list exactly the project's frames")
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
    existing = session.execute(
        select(FrameRow).where(FrameRow.project_id == row.project_id)
    )
    copy = FrameRow(
        project_id=row.project_id,
        name=f"{row.name} copy" if row.name else "copy",
        index=len(existing.scalars().all()),
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
    clips = [c for s in project.scenes for c in s.clips]
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
            )
        except ValueError as exc:
            job.status = "failed"
            job.error = str(exc)
            session.commit()
            metrics.record_job("failed")
            return
        except Exception as exc:  # noqa: BLE001 — job must never hang
            job.status = "failed"
            job.error = f"unexpected: {exc}"
            session.commit()
            metrics.record_job("failed")
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
