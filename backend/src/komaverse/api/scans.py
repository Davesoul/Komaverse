"""Projects / pages / panels API. All endpoints require auth; data is
scoped to the current user (cross-user ids return 404, never 403)."""

from __future__ import annotations

import io
import math

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from komaverse import storage
from komaverse.auth import ensure_owner, get_current_user, own_project
from komaverse.config import settings
from komaverse.db import get_session
from komaverse.security import limiter
from komaverse.db_models import (
    ClipRow,
    ExportReportRow,
    JobRow,
    PageRow,
    PanelRow,
    ProjectRow,
    SceneRow,
    UserRow,
)
from komaverse.detect import run_detector
from komaverse.models import Page, Panel, Project
from komaverse.observability import metrics

router = APIRouter(prefix="/api/v1")

MAX_IMAGE_DIM = 12000  # per side; beyond this scans are rejected, not scaled


class ProjectCreate(BaseModel):
    name: str = "Untitled"


class StorageOut(BaseModel):
    bytes_used: int
    quota_bytes: int


class DetectRequest(BaseModel):
    detector: str = "heuristic"  # heuristic | cloud (falls back to heuristic)
    direction: str = "rtl"  # rtl (manga) | ltr (western)
    confirm: bool = False  # required when the page already has panels


class PanelOrder(BaseModel):
    panel_ids: list[str]


class DetectResponse(BaseModel):
    panels: list[Panel]
    detector_used: str
    fell_back: bool = False


class PanelCreate(BaseModel):
    x: float = 0
    y: float = 0
    w: float = 100
    h: float = 100


class PanelPatch(BaseModel):
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    reading_order: int | None = None


def _get_page(session: Session, user: UserRow, page_id: str) -> PageRow:
    row = session.get(PageRow, page_id)
    if row is None:
        raise HTTPException(404, "page not found")
    ensure_owner(session, user, row.project_id)
    return row


def _check_box(x: float, y: float, w: float, h: float, page: PageRow) -> None:
    """Reject non-finite, degenerate, or out-of-page panel boxes."""
    for value in (x, y, w, h):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise HTTPException(400, "panel box must hold finite numbers")
    if w < 1 or h < 1:
        raise HTTPException(400, "panel width/height must be >= 1px")
    if x < 0 or y < 0:
        raise HTTPException(400, "panel x/y must be >= 0")
    if page.width and x + w > page.width:
        raise HTTPException(400, "panel extends past the page's right edge")
    if page.height and y + h > page.height:
        raise HTTPException(400, "panel extends past the page's bottom edge")


@router.post("/projects", response_model=Project)
def create_project(
    body: ProjectCreate,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = ProjectRow(user_id=user.id, name=body.name or "Untitled")
    session.add(row)
    session.flush()
    session.add(SceneRow(project_id=row.id, name="Scene 1", order=0))
    session.commit()
    return row.to_schema()


@router.get("/projects", response_model=list[Project])
def list_projects(
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    res = session.execute(select(ProjectRow).where(ProjectRow.user_id == user.id))
    return [r.to_schema() for r in res.scalars().all()]


@router.get("/projects/{project_id}", response_model=Project)
def get_project(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    return own_project(session, user, project_id).to_schema()


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Delete a project with everything in it (DB rows + media files).

    Pages, scenes, frames, and takes cascade; jobs/reports and their AI
    outputs are removed explicitly since they live outside the cascade.
    """
    project = own_project(session, user, project_id)
    jobs = (
        session.execute(
            select(JobRow).where(
                JobRow.project_id == project.id, JobRow.user_id == user.id
            )
        )
        .scalars()
        .all()
    )
    for job in jobs:
        for suffix in ("png", "mp4"):
            storage.remove_file(f"ai/{job.id}.{suffix}")
        session.execute(
            delete(ExportReportRow).where(ExportReportRow.job_id == job.id)
        )
        session.delete(job)
    session.delete(project)
    session.commit()
    storage.remove_project_files(project.id)


@router.get("/projects/{project_id}/storage", response_model=StorageOut)
def project_storage(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    project = own_project(session, user, project_id)
    return StorageOut(
        bytes_used=storage.project_usage_bytes(project.id),
        quota_bytes=settings.project_quota_mb * 1024 * 1024,
    )


@router.delete("/pages/{page_id}", status_code=204)
def delete_page(
    page_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    page = _get_page(session, user, page_id)
    panel_ids = [p.id for p in page.panels]
    if panel_ids:
        refs = (
            session.execute(
                select(ClipRow).where(
                    ClipRow.kind == "panel", ClipRow.source_id.in_(panel_ids)
                )
            )
            .scalars()
            .all()
        )
        if refs:
            raise HTTPException(
                409,
                f"page's panels are used by {len(refs)} clip(s); remove them first",
            )
    project_id, rel = page.project_id, page.image_path
    session.delete(page)  # panels cascade
    session.flush()
    rest = (
        session.execute(
            select(PageRow)
            .where(PageRow.project_id == project_id)
            .order_by(PageRow.index)
        )
        .scalars()
        .all()
    )
    for index, prow in enumerate(rest):
        prow.index = index
    session.commit()
    storage.remove_file(rel)


@router.post("/projects/{project_id}/pages", response_model=Page)
def upload_page(
    project_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    project = own_project(session, user, project_id)
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "file must be an image")
    try:
        data = storage.read_upload_capped(
            file.file, settings.upload_max_mb * 1024 * 1024
        )
    except storage.UploadTooLargeError:
        raise HTTPException(413, "file too large") from None
    if not data:
        raise HTTPException(400, "empty file")
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
    except Exception:
        raise HTTPException(400, "cannot decode image") from None
    if width > MAX_IMAGE_DIM or height > MAX_IMAGE_DIM:
        raise HTTPException(
            400, f"image too large (max {MAX_IMAGE_DIM}px per side)"
        )
    try:
        storage.check_quota(project.id, len(data))
    except storage.QuotaExceededError as exc:
        raise HTTPException(413, str(exc)) from None
    rel, url = storage.save_upload(project.id, file.filename or "page", data)
    existing = session.execute(
        select(PageRow).where(PageRow.project_id == project.id)
    )
    index = len(existing.scalars().all())
    row = PageRow(
        project_id=project.id,
        index=index,
        image_path=rel,
        image_url=url,
        width=width,
        height=height,
    )
    session.add(row)
    session.commit()
    return row.to_schema()


@router.post("/pages/{page_id}/detect", response_model=DetectResponse)
def detect_panels(
    page_id: str,
    body: DetectRequest,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    page = _get_page(session, user, page_id)
    if body.detector not in ("heuristic", "cloud"):
        raise HTTPException(400, f"unknown detector: {body.detector}")
    if body.direction not in ("rtl", "ltr"):
        raise HTTPException(400, f"unknown direction: {body.direction}")
    existing = session.execute(
        select(PanelRow).where(PanelRow.page_id == page.id)
    ).scalars().all()
    if existing and not body.confirm:
        raise HTTPException(
            409,
            f"page already has {len(existing)} panel(s); set confirm to replace them",
        )
    if body.detector == "cloud":
        retry = limiter.check(f"cloud-detect:{user.id}", 10, 60)
        if retry is not None:
            raise HTTPException(
                429,
                "cloud detect rate limited",
                headers={"Retry-After": str(int(retry) + 1)},
            )
    try:
        boxes, used, fell_back = run_detector(
            body.detector, storage.resolve(page.image_path), body.direction
        )
    except ValueError:
        raise HTTPException(404, "page image missing") from None
    metrics.record_detect(used)
    session.execute(delete(PanelRow).where(PanelRow.page_id == page.id))
    panels: list[Panel] = []
    for order, box in enumerate(boxes):
        row = PanelRow(
            page_id=page.id,
            x=box["x"],
            y=box["y"],
            w=box["w"],
            h=box["h"],
            reading_order=order,
        )
        session.add(row)
        session.flush()  # populate row.id (client-side default)
        panels.append(
            Panel(
                id=row.id,
                page_id=page.id,
                x=row.x,
                y=row.y,
                w=row.w,
                h=row.h,
                reading_order=order,
                crop_url=f"/api/v1/panels/{row.id}/preview",
            )
        )
    session.commit()
    return DetectResponse(panels=panels, detector_used=used, fell_back=fell_back)


@router.post("/pages/{page_id}/panels", response_model=Panel, status_code=201)
def add_panel(
    page_id: str,
    body: PanelCreate,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    page = _get_page(session, user, page_id)
    _check_box(body.x, body.y, body.w, body.h, page)
    existing = (
        session.execute(select(PanelRow).where(PanelRow.page_id == page.id))
        .scalars()
        .all()
    )
    order = max([p.reading_order for p in existing], default=-1) + 1
    row = PanelRow(
        page_id=page.id, x=body.x, y=body.y, w=body.w, h=body.h, reading_order=order
    )
    session.add(row)
    session.commit()
    return row.to_schema()


@router.patch("/panels/{panel_id}", response_model=Panel)
def patch_panel(
    panel_id: str,
    body: PanelPatch,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(PanelRow, panel_id)
    if row is None:
        raise HTTPException(404, "panel not found")
    ensure_owner(session, user, row.page.project_id)
    merged = {
        "x": body.x if body.x is not None else row.x,
        "y": body.y if body.y is not None else row.y,
        "w": body.w if body.w is not None else row.w,
        "h": body.h if body.h is not None else row.h,
    }
    if any(getattr(body, f) is not None for f in ("x", "y", "w", "h")):
        _check_box(merged["x"], merged["y"], merged["w"], merged["h"], row.page)
    for field in ("x", "y", "w", "h"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    if body.reading_order is not None:
        if body.reading_order < 0:
            raise HTTPException(400, "reading_order must be >= 0")
        row.reading_order = body.reading_order
    session.commit()
    return row.to_schema()


@router.patch("/pages/{page_id}/panels/order", response_model=list[Panel])
def order_panels(
    page_id: str,
    body: PanelOrder,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Replace the page's reading order with the given id list (exact set)."""
    page = _get_page(session, user, page_id)
    current = (
        session.execute(select(PanelRow).where(PanelRow.page_id == page.id))
        .scalars()
        .all()
    )
    by_id = {p.id: p for p in current}
    if set(body.panel_ids) != set(by_id) or len(body.panel_ids) != len(by_id):
        raise HTTPException(400, "panel_ids must list exactly the page's panels")
    for order, panel_id in enumerate(body.panel_ids):
        by_id[panel_id].reading_order = order
    session.commit()
    return [by_id[pid].to_schema() for pid in body.panel_ids]


@router.get("/panels/{panel_id}/preview")
def panel_preview(
    panel_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Contact-sheet crop of one panel, longest side 320px."""
    row = session.get(PanelRow, panel_id)
    if row is None or row.page is None:
        raise HTTPException(404, "panel not found")
    ensure_owner(session, user, row.page.project_id)
    try:
        img = Image.open(storage.resolve(row.page.image_path)).convert("RGB")
    except Exception:
        raise HTTPException(404, "panel image missing") from None
    iw, ih = img.size
    x, y = min(max(int(row.x), 0), iw - 1), min(max(int(row.y), 0), ih - 1)
    x2 = min(x + max(int(row.w), 1), iw)
    y2 = min(y + max(int(row.h), 1), ih)
    crop = img.crop((x, y, max(x2, x + 1), max(y2, y + 1)))
    crop.thumbnail((320, 320))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.delete("/panels/{panel_id}", status_code=204)
def delete_panel(
    panel_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(PanelRow, panel_id)
    if row is None:
        raise HTTPException(404, "panel not found")
    ensure_owner(session, user, row.page.project_id)
    refs = session.execute(
        select(ClipRow).where(
            ClipRow.kind == "panel", ClipRow.source_id == row.id
        )
    ).scalars().all()
    if refs:
        raise HTTPException(
            409, f"panel is used by {len(refs)} clip(s); remove them first"
        )
    page_id = row.page_id
    session.delete(row)
    session.flush()
    rest = (
        session.execute(
            select(PanelRow)
            .where(PanelRow.page_id == page_id)
            .order_by(PanelRow.reading_order)
        )
        .scalars()
        .all()
    )
    for order, prow in enumerate(rest):
        prow.reading_order = order
    session.commit()
