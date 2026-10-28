"""Agent chat + one-click auto-direct. Auth required, user-scoped."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from komaverse.agent import auto_direct_core, run_agent_turn
from komaverse.auth import get_current_user
from komaverse.db import get_session
from komaverse.db_models import UserRow
from komaverse.models import Scene

router = APIRouter(prefix="/api/v1")


class AgentChatBody(BaseModel):
    project_id: str | None = None
    message: str = ""
    clip_seconds: float = 2.0


class AgentAction(BaseModel):
    tool: str
    summary: str


class AgentReply(BaseModel):
    reply: str
    project_id: str | None = None
    project_name: str | None = None
    actions: list[AgentAction] = []


class AutoDirectBody(BaseModel):
    seconds: float = 2.0
    motions: list[str] | None = None


@router.post("/agent/chat", response_model=AgentReply)
def chat(
    body: AgentChatBody,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    if not body.message.strip():
        raise HTTPException(400, "message is required")
    seconds = min(max(body.clip_seconds, 0.1), 30.0)
    out = run_agent_turn(body.message, body.project_id, session, user, None, seconds)
    return AgentReply(**out)


@router.post(
    "/projects/{project_id}/auto-direct", response_model=Scene, status_code=201
)
def auto_direct(
    project_id: str,
    body: AutoDirectBody,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    seconds = min(max(body.seconds, 0.1), 30.0)
    scene, _ = auto_direct_core(session, user, project_id, seconds, body.motions)
    return scene
