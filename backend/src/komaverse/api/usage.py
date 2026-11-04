"""Usage telemetry: persisted per-action events + per-period summary.

The in-memory Prometheus counters reset on restart and jobs/ledger carry
no timestamps, so the dashboard reads here instead: one row per metered
action (chat turn, panel detect, image/video job, export) with the model
or detector used, estimated chat tokens, and credit cost.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from komaverse.auth import get_current_user, own_project
from komaverse.db import get_session
from komaverse.db_models import ProjectRow, UsageEventRow, UserRow

router = APIRouter(prefix="/api/v1")

ALLOWED_DAYS = (0, 7, 30, 90)


class UsagePoint(BaseModel):
    day: str
    calls: int
    tokens: int
    cost: float


class UsageSlice(BaseModel):
    kind: str
    label: str
    calls: int
    tokens: int
    cost: float


class ProjectSlice(BaseModel):
    project_id: str
    name: str
    calls: int
    tokens: int
    cost: float


class UsageSummary(BaseModel):
    days: int
    since: str | None
    project_id: str | None = None
    totals: dict[str, int]
    tokens_total: int
    cost_total: float
    by_label: list[UsageSlice]
    by_project: list[ProjectSlice] = []
    series: list[UsagePoint]


def record_usage(
    session: Session,
    user_id: str,
    kind: str,
    label: str = "",
    tokens: int = 0,
    cost: float = 0.0,
    project_id: str = "",
) -> None:
    """Append one usage event. Commits standalone so turns that never
    commit (chat) still show up on the dashboard."""
    session.add(
        UsageEventRow(
            user_id=user_id,
            project_id=project_id or "",
            kind=kind,
            label=label[:200],
            tokens=max(int(tokens), 0),
            cost=cost,
        )
    )
    session.commit()


def summarize(
    session: Session, user_id: str, days: int, project_id: str | None = None
) -> UsageSummary:
    if days not in ALLOWED_DAYS:
        raise HTTPException(400, "days must be one of 0 (all), 7, 30, 90")
    cutoff = datetime.now() - timedelta(days=days) if days else None
    query = select(UsageEventRow).where(UsageEventRow.user_id == user_id)
    if cutoff is not None:
        query = query.where(UsageEventRow.created_at >= cutoff)
    if project_id:
        query = query.where(UsageEventRow.project_id == project_id)
    rows = session.execute(query).scalars().all()

    totals: dict[str, int] = {}
    tokens_total = 0
    cost_total = 0.0
    slices: dict[tuple[str, str], list] = {}
    projects: dict[str, list] = {}
    days_map: dict[str, list] = {}
    for row in rows:
        totals[row.kind] = totals.get(row.kind, 0) + 1
        tokens_total += row.tokens or 0
        cost_total += row.cost or 0.0
        key = (row.kind, row.label or "—")
        bucket = slices.setdefault(key, [0, 0, 0.0])
        bucket[0] += 1
        bucket[1] += row.tokens or 0
        bucket[2] += row.cost or 0.0
        if not project_id:
            pkey = row.project_id or ""
            pbucket = projects.setdefault(pkey, [0, 0, 0.0])
            pbucket[0] += 1
            pbucket[1] += row.tokens or 0
            pbucket[2] += row.cost or 0.0
        day = (row.created_at or datetime.now()).date().isoformat()
        cell = days_map.setdefault(day, [0, 0, 0.0])
        cell[0] += 1
        cell[1] += row.tokens or 0
        cell[2] += row.cost or 0.0

    span = days or ((datetime.now().date() - min_days(rows)).days + 1 if rows else 1)
    series = []
    today = datetime.now().date()
    for back in range(min(span, 366) - 1, -1, -1):
        day = (today - timedelta(days=back)).isoformat()
        calls, tokens, cost = days_map.get(day, [0, 0, 0.0])
        series.append(UsagePoint(day=day, calls=calls, tokens=tokens, cost=cost))

    by_label = [
        UsageSlice(kind=kind, label=label, calls=c, tokens=t, cost=cost)
        for (kind, label), (c, t, cost) in sorted(
            slices.items(), key=lambda kv: -kv[1][0]
        )
    ]
    by_project: list[ProjectSlice] = []
    if not project_id and projects:
        names = {
            pid: (name or "Untitled")
            for pid, name in session.execute(
                select(ProjectRow.id, ProjectRow.name).where(
                    ProjectRow.user_id == user_id
                )
            ).all()
        }
        by_project = [
            ProjectSlice(
                project_id=pid,
                name=names.get(pid, "Outside projects") if pid else "Outside projects",
                calls=c,
                tokens=t,
                cost=cost,
            )
            for (pid, (c, t, cost)) in sorted(
                projects.items(), key=lambda kv: -kv[1][0]
            )
        ]
    return UsageSummary(
        days=days,
        since=cutoff.date().isoformat() if cutoff else None,
        project_id=project_id,
        totals=totals,
        tokens_total=tokens_total,
        cost_total=cost_total,
        by_label=by_label,
        by_project=by_project,
        series=series,
    )


def min_days(rows):
    return min((r.created_at or datetime.now()).date() for r in rows)


@router.get("/usage/summary", response_model=UsageSummary)
def usage_summary(
    days: int = 7,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
    project_id: str | None = None,
):
    if project_id:
        own_project(session, user, project_id)
    return summarize(session, user.id, days, project_id)
