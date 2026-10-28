"""Ops surface: readiness, Prometheus metrics, status, client error funnel."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from komaverse import __version__
from komaverse.auth import get_current_user
from komaverse.config import settings
from komaverse.db import get_session
from komaverse.db_models import JobRow, LedgerRow, ProjectRow, UserRow
from komaverse.observability import (
    check_readiness,
    metrics,
    recent_errors,
    uptime_seconds,
)

router = APIRouter()

import logging

logger = logging.getLogger("komaverse.client")


class ClientLogBody(BaseModel):
    level: str = "error"
    message: str = ""
    url: str = ""


class StatusOut(BaseModel):
    version: str
    uptime_sec: float
    users: int
    projects: int
    jobs: int
    credits_issued: int
    paywall_enabled: bool = False
    recent_errors: list[dict]


@router.get("/ready")
def ready():
    from fastapi.responses import JSONResponse

    ok, checks = check_readiness(settings.database_url, settings.media_dir)
    return JSONResponse(
        {"status": "ready" if ok else "degraded", "checks": checks},
        status_code=200 if ok else 503,
    )


@router.get("/metrics")
def prometheus_metrics():
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(metrics.render(__version__))


@router.get("/api/v1/status", response_model=StatusOut)
def status(
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    def count(model):
        return session.execute(select(func.count()).select_from(model)).scalar() or 0

    issued = (
        session.execute(
            select(func.coalesce(func.sum(LedgerRow.delta), 0)).where(
                LedgerRow.delta > 0
            )
        ).scalar()
        or 0
    )
    return StatusOut(
        version=__version__,
        uptime_sec=round(uptime_seconds(), 1),
        users=count(UserRow),
        projects=count(ProjectRow),
        jobs=count(JobRow),
        credits_issued=int(issued),
        paywall_enabled=settings.paywall_enabled,
        recent_errors=recent_errors()[:10],
    )


@router.post("/api/v1/client-log", status_code=202)
def client_log(body: ClientLogBody):
    level = body.level if body.level in ("info", "warn", "error") else "error"
    message = (body.message or "")[:500]
    url = (body.url or "")[:200]
    logger.log(
        {"info": 20, "warn": 30, "error": 40}[level],
        "client %s: %s %s",
        level,
        message,
        url,
    )
    return {"status": "accepted"}
