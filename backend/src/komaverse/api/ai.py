"""AI generation jobs + credit ledger. Auth required; jobs, ledger and
frames are scoped to the current user."""

from __future__ import annotations

import base64
import io
import json
import logging
import random
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from komaverse.ai.direction import (
    ENTITY_KINDS,
    build_final_prompt,
)
from komaverse.ai.providers import (
    ProviderError,
    chat_complete,
    check_openrouter_model,
    get_chat_config,
    get_provider,
    list_openrouter_models,
)
from komaverse import storage
from komaverse.api.usage import record_usage
from komaverse.auth import ensure_owner, get_current_user
from komaverse.config import settings
from komaverse.db import get_session, session_factory
from komaverse.db_models import (
    EntityRow,
    FrameRow,
    JobRow,
    LayerRow,
    LedgerRow,
    PageRow,
    PanelRow,
    UserRow,
)
from komaverse.models import CreditEntry, Entity, Job
from komaverse.observability import metrics
from komaverse.renderer import composite_frame_image

router = APIRouter(prefix="/api/v1")
billing_router = APIRouter(prefix="/api/v1")
logger = logging.getLogger("komaverse.ai")

COSTS = {"image": 5, "video": 25}
PROVIDERS = ("cloud", "comfyui", "fake", "local", "meta", "openrouter")


class GenerateRequest(BaseModel):
    kind: str = "image"  # image, or video (openrouter provider only)
    prompt: str = ""
    project_id: str | None = None
    provider: str = Field(default_factory=lambda: settings.ai_provider)
    model: str | None = None  # openrouter model id (catalogued); None = default
    width: int = 1024
    height: int = 1024
    seconds: float = 2.0
    attach_frame: bool = True
    seed: int | None = None  # None = random per job (stored on the job)
    negative_prompt: str = ""
    enhance: bool = False  # LLM rewrite before sending (fail-soft)
    style: str = "none"  # STYLE_PRESETS key
    camera_move: str = "none"  # CAMERA_MOVES key (video only)
    entity_ids: list[str] = Field(default_factory=list)
    reference_frame_id: str | None = None  # start frame (video) / redraw (image)


class EntityCreate(BaseModel):
    kind: str = "character"  # character | field | prop
    name: str = ""
    anchor: str = ""
    frame_id: str | None = None


_REF_PX = 768  # reference snapshots are guidance, not artwork
_MAX_REFS = 3
_MAX_SEED = 2**31 - 1


def _frame_png(session: Session, project_id: str, frame_id: str) -> bytes | None:
    """Composite one draw frame to a small PNG for provider references."""
    row = session.get(FrameRow, frame_id)
    if row is None or row.project_id != project_id:
        return None
    try:
        img = composite_frame_image(row)
        img.thumbnail((_REF_PX, _REF_PX))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _as_ref_png(data: bytes) -> bytes | None:
    """Normalize arbitrary image bytes to a small guidance PNG."""
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((_REF_PX, _REF_PX))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _panel_png(session: Session, project_id: str, panel_id: str) -> bytes | None:
    """Crop one detected panel to a small PNG for provider references."""
    panel = session.get(PanelRow, panel_id)
    page = session.get(PageRow, panel.page_id) if panel else None
    if panel is None or page is None or page.project_id != project_id:
        return None
    try:
        data = storage.resolve(page.image_path).read_bytes()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        x, y = min(max(int(panel.x), 0), w), min(max(int(panel.y), 0), h)
        bw, bh = min(int(panel.w), w - x), min(int(panel.h), h - y)
        if bw < 2 or bh < 2:
            return None
        return _as_ref_png(_crop_bytes(img, x, y, bw, bh))
    except (OSError, ValueError):
        return None


def _crop_bytes(img, x: int, y: int, w: int, h: int) -> bytes:
    buf = io.BytesIO()
    img.crop((x, y, x + w, y + h)).save(buf, format="PNG")
    return buf.getvalue()


def _page_png(session: Session, project_id: str, page_id: str) -> bytes | None:
    """A whole scan page, shrunk, for provider references."""
    page = session.get(PageRow, page_id)
    if page is None or page.project_id != project_id:
        return None
    try:
        return _as_ref_png(storage.resolve(page.image_path).read_bytes())
    except OSError:
        return None


def _job_png(
    session: Session, user_id: str, project_id: str, job_id: str
) -> bytes | None:
    """A finished image generation's artifact, for variation chains."""
    row = session.get(JobRow, job_id)
    if (
        row is None
        or row.user_id != user_id
        or row.project_id != project_id
        or row.kind != "image"
        or row.status != "done"
        or not row.result_url
    ):
        return None
    try:
        rel = row.result_url.split("/media/", 1)[-1]
        return _as_ref_png(storage.resolve(rel).read_bytes())
    except OSError:
        return None


def _enhance_prompt(prompt: str) -> str:
    """One chat call to enrich a thin prompt; fail-soft to the original."""
    try:
        base_url, api_key, model = get_chat_config()
        msg = chat_complete(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "Rewrite the user prompt as one dense visual paragraph for an image/video model. Keep every named subject. Reply with the rewrite only.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if isinstance(content, list):  # multimodal content blocks
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        text = str(content).strip()
        return text or prompt
    except Exception:
        return prompt


class BalanceOut(BaseModel):
    balance: int
    entries: list[CreditEntry]


class GrantRequest(BaseModel):
    amount: int = 100
    reason: str = "dev grant"


def get_balance(session: Session, user_id: str) -> int:
    total = session.execute(
        select(func.coalesce(func.sum(LedgerRow.delta), 0)).where(
            LedgerRow.user_id == user_id
        )
    ).scalar()
    return int(total or 0)


def add_entry(
    session: Session, user_id: str, delta: int, reason: str, job_id: str | None = None
) -> LedgerRow:
    row = LedgerRow(user_id=user_id, delta=delta, reason=reason, job_id=job_id)
    session.add(row)
    return row


def run_ai_job(job_id: str, factory=None) -> None:
    """Execute one job synchronously (called in a background task)."""
    factory = factory or session_factory()
    with factory() as session:
        job = session.get(JobRow, job_id)
        if job is None:
            return
        job.status = "running"
        job.progress = 0.1
        session.commit()
        try:
            provider = get_provider(job.provider)
            try:
                entities = json.loads(job.entities_json or "[]")
            except ValueError:
                entities = []
            if not isinstance(entities, list):
                entities = []
            entities = [e for e in entities if isinstance(e, dict)]
            final = build_final_prompt(
                prompt=job.prompt,
                entities=entities,
                style=job.style or "none",
                camera_move=job.camera_move or "none",
                kind=job.kind,
                negative_prompt=job.negative_prompt or "",
            )
            if job.enhance:
                final = _enhance_prompt(final)
            job.final_prompt = final
            seed_used = (
                job.seed if job.seed is not None else random.randint(0, _MAX_SEED)
            )
            job.seed = seed_used
            # Visual guidance, cheapest-first: explicit start frame, then
            # entity snapshots in order. Capped — each is a full upload.
            ref_pngs: list[bytes] = []
            if job.reference_frame_id and job.project_id:
                png = _frame_png(session, job.project_id, job.reference_frame_id)
                if png is not None:
                    ref_pngs.append(png)
            for ent in entities:
                if len(ref_pngs) >= _MAX_REFS:
                    break
                fid = ent.get("frame_id")
                if fid and job.project_id:
                    png = _frame_png(session, job.project_id, str(fid))
                elif ent.get("panel_id") and job.project_id:
                    png = _panel_png(session, job.project_id, str(ent["panel_id"]))
                elif ent.get("page_id") and job.project_id:
                    png = _page_png(session, job.project_id, str(ent["page_id"]))
                elif ent.get("job_id"):
                    png = _job_png(session, job.user_id, job.project_id or "", str(ent["job_id"]))
                else:
                    png = None
                if png is not None:
                    ref_pngs.append(png)
            if job.kind == "image":
                data = provider.generate_image(
                    final,
                    job.width,
                    job.height,
                    model=job.model,
                    seed=seed_used,
                    negative_prompt=job.negative_prompt or "",
                    reference_images=tuple(ref_pngs),
                )
                suffix = "png"
            elif job.kind == "video":
                start, rest = (
                    (ref_pngs[0], ref_pngs[1:]) if ref_pngs else (None, [])
                )
                data = provider.generate_video(
                    final,
                    job.seconds,
                    model=job.model,
                    seed=seed_used,
                    start_frame=start,
                    reference_images=tuple(rest),
                )
                suffix = "mp4"
            else:
                raise ProviderError(f"unknown job kind: {job.kind}")
        except ProviderError as exc:
            job.status = "failed"
            job.error = str(exc)
            # Refund only what generate() debited: with the paywall off no
            # debit happened, so an unconditional refund would mint credits.
            if settings.paywall_enabled:
                add_entry(
                    session,
                    job.user_id,
                    COSTS.get(job.kind, 0),
                    f"refund {job.id}",
                    job.id,
                )
            session.commit()
            metrics.record_job("failed")
            record_usage(
                session, job.user_id, job.kind, f"{job.provider}:{job.model or 'default'}"
            )
            return
        except Exception as exc:  # noqa: BLE001 — job must never hang
            job.status = "failed"
            job.error = f"unexpected: {exc}"
            if settings.paywall_enabled:
                add_entry(
                    session,
                    job.user_id,
                    COSTS.get(job.kind, 0),
                    f"refund {job.id}",
                    job.id,
                )
            session.commit()
            metrics.record_job("failed")
            record_usage(
                session, job.user_id, job.kind, f"{job.provider}:{job.model or 'default'}"
            )
            return

        # The PNG below is the immutable job artifact (result_url); the
        # base64 layer is the editable copy. Two lifecycles, not waste:
        # deleting the frame must not break the job gallery and vice versa.
        rel = Path("ai") / f"{job.id}.{suffix}"
        dest = settings.media_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        job.result_url = f"/media/{rel.as_posix()}"
        if job.attach_frame and job.project_id and suffix == "png":
            frame = FrameRow(
                project_id=job.project_id, name=f"AI: {job.prompt[:40]}"
            )
            session.add(frame)
            session.flush()
            b64 = base64.b64encode(data).decode()
            session.add(
                LayerRow(
                    frame_id=frame.id,
                    name="ai",
                    layer_order=0,
                    data_url=f"data:image/png;base64,{b64}",
                )
            )
        job.status = "done"
        job.progress = 1.0
        session.commit()
        metrics.record_job("done")
        record_usage(
            session,
            job.user_id,
            job.kind,
            f"{job.provider}:{job.model or 'default'}",
            0,
            COSTS.get(job.kind, 0) if settings.paywall_enabled else 0.0,
        )


@router.post("/ai/generate", response_model=Job, status_code=202)
def generate(
    body: GenerateRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    if body.kind not in COSTS:
        raise HTTPException(400, f"unknown kind: {body.kind}")
    if body.provider not in PROVIDERS:
        raise HTTPException(400, f"unknown provider: {body.provider}")
    if body.kind == "video" and body.provider != "openrouter":
        raise HTTPException(
            400, "video generation needs provider=openrouter "
            "(meta has no video endpoint)"
        )
    if body.model and body.provider not in ("openrouter", "local"):
        raise HTTPException(400, "per-model choice is openrouter/local-only")
    if body.provider == "openrouter" and body.model:
        try:
            check_openrouter_model(body.kind, body.model)
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from exc
    if not body.prompt.strip():
        raise HTTPException(400, "prompt is required")
    if body.seed is not None and not (0 <= body.seed <= _MAX_SEED):
        raise HTTPException(400, f"seed must be 0..{_MAX_SEED}")
    if len(body.entity_ids) > 8:
        raise HTTPException(400, "at most 8 entities per generation")
    try:
        # Validates style/camera ids up front (unknown ids never bill).
        build_final_prompt(
            prompt="x",
            entities=[],
            style=body.style,
            camera_move=body.camera_move,
            kind=body.kind,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.project_id:
        ensure_owner(session, user, body.project_id)
    snapshots: list[dict] = []
    if body.entity_ids or body.reference_frame_id:
        if not body.project_id:
            raise HTTPException(400, "entities and start frames need a project")
        seen: set[str] = set()
        for eid in body.entity_ids:
            if eid in seen:
                continue
            seen.add(eid)
            ent = session.get(EntityRow, eid)
            if (
                ent is None
                or ent.project_id != body.project_id
                or ent.user_id != user.id
            ):
                raise HTTPException(400, f"unknown entity: {eid}")
            snapshots.append(
                {
                    "id": ent.id,
                    "name": ent.name,
                    "kind": ent.kind,
                    "anchor": ent.anchor or "",
                    "frame_id": ent.frame_id,
                }
            )
        if body.reference_frame_id:
            frame = session.get(FrameRow, body.reference_frame_id)
            if frame is None or frame.project_id != body.project_id:
                raise HTTPException(
                    400, f"unknown frame: {body.reference_frame_id}"
                )
    cost = COSTS[body.kind]
    if settings.paywall_enabled and get_balance(session, user.id) < cost:
        raise HTTPException(402, f"insufficient credits (need {cost})")
    if settings.paywall_enabled:
        add_entry(session, user.id, -cost, f"{body.kind} generation")
    job = JobRow(
        user_id=user.id,
        project_id=body.project_id,
        kind=body.kind,
        prompt=body.prompt,
        provider=body.provider,
        model=body.model,
        width=min(max(body.width, 64), 2048),
        height=min(max(body.height, 64), 2048),
        seconds=min(max(body.seconds, 0.5), 10.0),
        attach_frame=body.attach_frame,
        seed=body.seed,
        negative_prompt=body.negative_prompt,
        style=body.style,
        camera_move=body.camera_move,
        enhance=body.enhance,
        reference_frame_id=body.reference_frame_id,
        entities_json=json.dumps(snapshots),
    )
    session.add(job)
    session.commit()
    background.add_task(run_ai_job, job.id)
    return job.to_schema()


@router.get("/ai/jobs/{job_id}", response_model=Job)
def get_job(
    job_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(JobRow, job_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "job not found")
    return row.to_schema()


@router.get("/ai/models")
def ai_models(user: UserRow = Depends(get_current_user)):
    """Curated OpenRouter model catalog + server defaults for the picker."""
    return list_openrouter_models()


@router.get("/projects/{project_id}/entities", response_model=list[Entity])
def list_entities(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    ensure_owner(session, user, project_id)
    res = session.execute(
        select(EntityRow)
        .where(EntityRow.project_id == project_id, EntityRow.user_id == user.id)
        .order_by(EntityRow.id)
    )
    return [r.to_schema() for r in res.scalars().all()]


@router.post(
    "/projects/{project_id}/entities", response_model=Entity, status_code=201
)
def create_entity(
    project_id: str,
    body: EntityCreate,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    ensure_owner(session, user, project_id)
    if body.kind not in ENTITY_KINDS:
        raise HTTPException(400, f"unknown entity kind: {body.kind}")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "entity name is required")
    if body.frame_id:
        frame = session.get(FrameRow, body.frame_id)
        if frame is None or frame.project_id != project_id:
            raise HTTPException(400, f"unknown frame: {body.frame_id}")
    row = EntityRow(
        user_id=user.id,
        project_id=project_id,
        kind=body.kind,
        name=name[:80],
        anchor=body.anchor.strip(),
        frame_id=body.frame_id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.to_schema()


@router.delete("/entities/{entity_id}", status_code=204)
def delete_entity(
    entity_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(EntityRow, entity_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "entity not found")
    session.delete(row)
    session.commit()


@router.get("/ai/jobs", response_model=list[Job])
def list_jobs(
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    res = session.execute(
        select(JobRow).where(JobRow.user_id == user.id).order_by(JobRow.id)
    )
    return [r.to_schema() for r in res.scalars().all()]


@billing_router.get("/billing/me", response_model=BalanceOut)
def my_balance(
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    res = session.execute(select(LedgerRow).where(LedgerRow.user_id == user.id))
    entries = [r.to_schema() for r in res.scalars().all()]
    return BalanceOut(balance=sum(e.delta for e in entries), entries=entries[-50:])


@billing_router.post("/billing/grant", response_model=BalanceOut)
def grant(
    body: GrantRequest,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    if settings.is_production:
        raise HTTPException(403, "grants are disabled in production")
    if body.amount <= 0 or body.amount > 100000:
        raise HTTPException(400, "amount out of range")
    add_entry(session, user.id, body.amount, body.reason or "grant")
    session.commit()
    return my_balance(session, user)
