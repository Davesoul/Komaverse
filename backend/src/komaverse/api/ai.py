"""AI generation jobs + credit ledger. Auth required; jobs, ledger and
frames are scoped to the current user."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from komaverse.ai.providers import ProviderError, get_provider
from komaverse.auth import ensure_owner, get_current_user
from komaverse.config import settings
from komaverse.db import get_session, session_factory
from komaverse.db_models import FrameRow, JobRow, LayerRow, LedgerRow, UserRow
from komaverse.models import CreditEntry, Job
from komaverse.observability import metrics

router = APIRouter(prefix="/api/v1")
billing_router = APIRouter(prefix="/api/v1")
logger = logging.getLogger("komaverse.ai")

COSTS = {"image": 5}
PROVIDERS = ("cloud", "comfyui", "fake")


class GenerateRequest(BaseModel):
    kind: str = "image"  # image only; video generation is not available yet
    prompt: str = ""
    project_id: str | None = None
    provider: str = Field(default_factory=lambda: settings.ai_provider)
    width: int = 1024
    height: int = 1024
    seconds: float = 2.0
    attach_frame: bool = True


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
            if job.kind == "image":
                data = provider.generate_image(job.prompt, job.width, job.height)
                suffix = "png"
            elif job.kind == "video":
                data = provider.generate_video(job.prompt, job.seconds)
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


@router.post("/ai/generate", response_model=Job, status_code=202)
def generate(
    body: GenerateRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    if body.kind == "video":
        raise HTTPException(400, "video generation is not available yet")
    if body.kind not in COSTS:
        raise HTTPException(400, f"unknown kind: {body.kind}")
    if body.provider not in PROVIDERS:
        raise HTTPException(400, f"unknown provider: {body.provider}")
    if not body.prompt.strip():
        raise HTTPException(400, "prompt is required")
    if body.project_id:
        ensure_owner(session, user, body.project_id)
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
        width=min(max(body.width, 64), 2048),
        height=min(max(body.height, 64), 2048),
        seconds=min(max(body.seconds, 0.5), 10.0),
        attach_frame=body.attach_frame,
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
