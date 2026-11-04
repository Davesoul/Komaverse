"""Agent chat + one-click auto-direct. Auth required, user-scoped."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session

from komaverse.agent import (
    AgentRef,
    auto_direct_core,
    run_agent_turn,
    run_agent_turn_stream,
)
from komaverse.ai.providers import (
    ProviderError,
    chat_configured,
    check_openrouter_model,
    get_chat_config,
)
from komaverse.api.ai import PROVIDERS
from komaverse.auth import get_current_user
from komaverse.config import settings
from komaverse.db import get_session
from komaverse.db_models import ChatTurnRow, UserRow
from komaverse.models import Scene, uuid_short
from komaverse.studio_settings import (
    AI_OVERRIDE_KEYS,
    AI_SECRET_KEYS,
    KIND_PROVIDERS,
    PROVIDER_PREFIX,
    apply_ai_overrides,
    get_ai_overrides,
    model_field,
    provider_for,
    set_ai_override,
)

router = APIRouter(prefix="/api/v1")


class AgentChatBody(BaseModel):
    project_id: str | None = None
    # Conversation thread within the project scope (None = default).
    # Reads and writes both filter by (user, project, thread).
    thread_id: str | None = None
    message: str = ""
    clip_seconds: float = 2.0
    # @-mentions parsed by the client: resolved ownership-scoped into context.
    refs: list[AgentRef] = []
    # Open frame in the client's Draw tab: the turn scopes to it unless the
    # message references another frame.
    current_frame_id: str | None = None


class AgentAction(BaseModel):
    tool: str
    summary: str


class AgentReply(BaseModel):
    reply: str
    project_id: str | None = None
    project_name: str | None = None
    actions: list[AgentAction] = []
    # Which brain answered: "demo" or "cloud:<model>". The dock shows it
    # so a silent cloud fallback is never mistaken for the real model.
    brain: str = "demo"
    # Short cloud failure when the turn fell back to demo (e.g. 402 no
    # credits). Shown in the widget so demo mode never looks stuck.
    cloud_error: str | None = None


class AgentStatusOut(BaseModel):
    provider: str
    cloud: bool
    chat_model: str | None
    vision_model: str | None
    image_model: str | None
    video_model: str | None
    transcribe_model: str | None
    voice_model: str | None
    # Effective provider per kind (chat/vision/image/voice routable, video fixed).
    providers: dict[str, str] = {}
    # Studio override keys currently active (env wins when absent).
    overrides: list[str] = []
    # Secret keys with a non-empty effective value (env or studio row).
    # Presence only — values never leave the server.
    keys_set: list[str] = []


class AgentConfigBody(BaseModel):
    """Studio-wide brain switch (widget). All fields optional; a null
    model leaves it alone while a blank one clears that override back to
    the env default. Per-kind providers route each job family."""

    provider: str | None = None
    chat_provider: str | None = None
    vision_provider: str | None = None
    image_provider: str | None = None
    voice_provider: str | None = None
    chat_model: str | None = None
    vision_model: str | None = None
    image_model: str | None = None
    video_model: str | None = None
    transcribe_model: str | None = None
    voice_model: str | None = None
    # Provider API keys: non-blank stores a studio row (hot, no restart),
    # blank clears back to the env default, null leaves it alone.
    # Values are write-only; reads report set-or-not via keys_set.
    meta_api_key: str | None = None
    openrouter_api_key: str | None = None
    baseten_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    ai_api_key: str | None = None


class ChatTurnOut(BaseModel):
    role: str
    text: str
    thread_id: str | None = None


class ThreadOut(BaseModel):
    """One conversation thread in a project scope (None id = default)."""

    id: str | None
    # First user turn's text (truncated), so the switcher can label it.
    title: str
    updated_at: str | None
    count: int


class ThreadIdOut(BaseModel):
    id: str


class AutoDirectBody(BaseModel):
    seconds: float = 2.0
    motions: list[str] | None = None
    scene_id: str | None = None
    new_scene: bool = False


@router.post("/agent/chat", response_model=AgentReply)
def chat(
    body: AgentChatBody,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    if not body.message.strip():
        raise HTTPException(400, "message is required")
    seconds = min(max(body.clip_seconds, 0.1), 30.0)
    history = _load_history(session, user, body.project_id, thread_id=body.thread_id)
    refs = [r.model_dump() for r in body.refs]
    # The open frame reaches run_agent_turn via current_frame_id, which
    # scopes the turn and - only on reference intent - attaches it.
    out = run_agent_turn(
        body.message, body.project_id, session, user, None, seconds,
        refs=refs, history=history, current_frame_id=body.current_frame_id,
    )
    # Stored under the resolved project (not the requested one): the turn
    # rewrote an unscoped request into its project, so the memory must move
    # with it — reads filter by the same scope, which is what makes
    # history genuinely per project end to end.
    _remember(
        session, user, out.get("project_id"), "user", body.message,
        thread_id=body.thread_id,
    )
    _remember(
        session, user, out.get("project_id"), "agent", out.get("reply", ""),
        out.get("actions"), thread_id=body.thread_id,
    )
    session.commit()
    return AgentReply(**out)


@router.post("/agent/chat/stream")
def chat_stream(
    body: AgentChatBody,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Server-sent events for one agent turn.

    Streams token, tool_start, tool_result, tool, and text events while the
    turn runs, then a final result event with the same shape as /agent/chat.
    History is remembered exactly like the non-streaming endpoint.
    """
    if not body.message.strip():
        raise HTTPException(400, "message is required")
    seconds = min(max(body.clip_seconds, 0.1), 30.0)
    history = _load_history(session, user, body.project_id, thread_id=body.thread_id)
    refs = [r.model_dump() for r in body.refs]

    def generate():
        final: dict | None = None
        for event in run_agent_turn_stream(
            body.message, body.project_id, session, user, None, seconds,
            refs=refs, history=history, current_frame_id=body.current_frame_id,
        ):
            if event["type"] == "result":
                final = {k: v for k, v in event.items() if k != "type"}
            yield f"data: {json.dumps(event)}\n\n"
        if final is not None:
            _remember(
                session, user, final.get("project_id"), "user", body.message,
                thread_id=body.thread_id,
            )
            _remember(
                session, user, final.get("project_id"), "agent", final.get("reply", ""),
                final.get("actions"), thread_id=body.thread_id,
            )
            session.commit()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


_HISTORY_LIMIT = 10
_HISTORY_KEEP = 100
_MAX_TURN_CHARS = 2000


def _history_scope(user, project_id, thread_id=None):
    cond = ChatTurnRow.user_id == user.id
    scope = (
        ChatTurnRow.project_id == project_id
        if project_id
        else ChatTurnRow.project_id.is_(None)
    )
    thread = (
        ChatTurnRow.thread_id == thread_id
        if thread_id
        else ChatTurnRow.thread_id.is_(None)
    )
    return cond & scope & thread


def _load_history(session, user, project_id, limit=_HISTORY_LIMIT, thread_id=None):
    """Recent turns oldest-first for one user+project+thread scope (the memory)."""
    rows = (
        session.execute(
            select(ChatTurnRow)
            .where(_history_scope(user, project_id, thread_id))
            .order_by(desc(ChatTurnRow.created_at), desc(ChatTurnRow.id))
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [
        {
            "role": r.role,
            "text": r.text,
            "at": r.created_at.isoformat(timespec="minutes") if r.created_at else "",
            "thread_id": r.thread_id,
        }
        for r in reversed(rows)
    ]


def _remember(session, user, project_id, role, text, actions=None, thread_id=None):
    """Store one turn and prune the scope past the keep limit.

    Agent rows also keep the tool calls they ran (JSON), so the
    history tool reports operations — not just words.
    """
    stored_actions = None
    if actions:
        try:
            import json as _json

            stored_actions = _json.dumps(
                [
                    {"tool": a.get("tool", ""), "summary": str(a.get("summary") or "")[:300]}
                    for a in actions
                    if isinstance(a, dict) and a.get("tool")
                ][:12]
            )
        except (TypeError, ValueError):
            stored_actions = None
    session.add(
        ChatTurnRow(
            user_id=user.id,
            project_id=project_id,
            thread_id=thread_id,
            role=role,
            text=(text or "")[:_MAX_TURN_CHARS],
            actions=stored_actions,
        )
    )
    session.flush()
    stale = (
        session.execute(
            select(ChatTurnRow.id)
            .where(_history_scope(user, project_id, thread_id))
            .order_by(desc(ChatTurnRow.created_at), desc(ChatTurnRow.id))
            .offset(_HISTORY_KEEP)
        )
        .scalars()
        .all()
    )
    if stale:
        session.execute(delete(ChatTurnRow).where(ChatTurnRow.id.in_(stale)))


@router.get("/agent/history", response_model=list[ChatTurnOut])
def agent_history(
    project_id: str | None = None,
    thread_id: str | None = None,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    return [
        ChatTurnOut(role=h["role"], text=h["text"], thread_id=h["thread_id"])
        for h in _load_history(session, user, project_id, limit=50, thread_id=thread_id)
    ]


@router.delete("/agent/history", status_code=204)
def clear_agent_history(
    project_id: str | None = None,
    thread_id: str | None = None,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    session.execute(
        delete(ChatTurnRow).where(_history_scope(user, project_id, thread_id))
    )
    session.commit()
    return None


@router.get("/agent/threads", response_model=list[ThreadOut])
def list_agent_threads(
    project_id: str | None = None,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Conversation threads in one project scope, newest first.

    Threads are the distinct thread_id values stored on this user's turns
    (NULL rows predate threads and read as the default thread). The title
    is the thread's first user turn, truncated for the switcher.
    """
    cond = ChatTurnRow.user_id == user.id
    cond = cond & (
        ChatTurnRow.project_id == project_id
        if project_id
        else ChatTurnRow.project_id.is_(None)
    )
    rows = (
        session.execute(
            select(
                ChatTurnRow.thread_id,
                ChatTurnRow.role,
                ChatTurnRow.text,
                ChatTurnRow.created_at,
            )
            .where(cond)
            .order_by(ChatTurnRow.created_at.asc(), ChatTurnRow.id.asc())
        )
        .all()
    )
    agg: dict[str | None, dict] = {}
    for tid, role, text, at in rows:
        entry = agg.setdefault(tid, {"count": 0, "updated_at": None, "title": None})
        entry["count"] += 1
        entry["updated_at"] = at
        if entry["title"] is None and role == "user" and (text or "").strip():
            entry["title"] = (text or "").strip().split("\n")[0][:48]
    out = [
        ThreadOut(
            id=tid,
            title=entry["title"] or "Untitled thread",
            updated_at=(
                entry["updated_at"].isoformat(timespec="minutes")
                if entry["updated_at"]
                else None
            ),
            count=entry["count"],
        )
        for tid, entry in agg.items()
    ]
    out.sort(key=lambda t: t.updated_at or "", reverse=True)
    return out


@router.post("/agent/threads", response_model=ThreadIdOut, status_code=201)
def create_agent_thread(
    project_id: str | None = None,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Mint a fresh thread id for this project scope.

    The id carries no rows until the first turn is stored under it, so it
    only appears in the list once the conversation starts — the widget
    adopts it as the active thread immediately.
    """
    _ = (project_id, session, user)
    return ThreadIdOut(id=uuid_short())


@router.delete("/agent/threads/{thread_id}", status_code=204)
def delete_agent_thread(
    thread_id: str,
    project_id: str | None = None,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Delete one non-default thread's turns (the default thread clears
    through DELETE /agent/history instead)."""
    session.execute(
        delete(ChatTurnRow).where(_history_scope(user, project_id, thread_id))
    )
    session.commit()
    return None


@router.get("/agent/status", response_model=AgentStatusOut)
def agent_status(
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Chat brain parameters for the widget (never any keys)."""
    provider = settings.ai_provider if settings.ai_provider in PROVIDERS else "cloud"
    providers = {
        "chat": provider_for("chat"),
        "vision": provider_for("vision"),
        "image": provider_for("image"),
        "video": "openrouter",
        "transcribe": "meta",
        "voice": provider_for("voice"),
    }
    try:
        chat_model = get_chat_config()[2]
    except ProviderError:
        # Unconfigured key: still report the selected model (env or studio
        # override) so the widget shows what WILL run once keyed.
        chat_model = {
            "meta": settings.meta_chat_model,
            "openrouter": settings.openrouter_chat_model,
            "baseten": settings.baseten_chat_model,
            "cloud": settings.ai_chat_model,
            "local": settings.local_chat_model or None,
        }.get(providers["chat"])
    images = {
        "openrouter": settings.openrouter_image_model,
        "meta": settings.meta_image_model,
        "cloud": settings.ai_image_model,
        "local": settings.local_image_model or None,
    }
    visions = {
        "openrouter": settings.openrouter_vision_model,
        "meta": settings.meta_vision_model,
        "cloud": settings.ai_vision_model,
        "local": settings.local_vision_model or None,
    }
    videos = {"openrouter": settings.openrouter_video_model}
    transcribes = {"meta": settings.meta_transcribe_model}
    voices = {
        "elevenlabs": settings.elevenlabs_voice_model,
        "free-tts": settings.freetts_voice_model,
    }
    return AgentStatusOut(
        provider=provider,
        cloud=chat_configured(),
        chat_model=chat_model,
        vision_model=visions.get(providers["vision"]),
        image_model=images.get(providers["image"]),
        video_model=videos.get(providers["video"]),
        transcribe_model=transcribes.get(providers["transcribe"]),
        voice_model=voices.get(providers["voice"]),
        providers=providers,
        overrides=sorted(get_ai_overrides(session).keys()),
        keys_set=sorted(k for k in AI_SECRET_KEYS if getattr(settings, k, None)),
    )


@router.put("/agent/config", response_model=AgentStatusOut)
def agent_config(
    body: AgentConfigBody,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Switch the studio brain from the widget (per-kind providers + models).

    Studio-wide and persisted: rows overlay the environment hot, so no
    restart is needed. Unknown providers and off-catalog OpenRouter
    image/video models fail loudly instead of billing surprises. Video is
    OpenRouter-only end to end, so it takes a model but no provider;
    transcription is Meta-only the same way. Voice (TTS) routes between
    ElevenLabs cloud and the local free-tts server like chat/vision/image.
    """
    if body.provider is not None:
        text = body.provider.strip().lower()
        if text:
            if text not in PROVIDERS:
                raise HTTPException(
                    400,
                    f'unknown provider "{body.provider}" (want: {", ".join(PROVIDERS)})',
                )
            set_ai_override(session, "ai_provider", text)
        else:
            # Blank provider clears the override back to the env default.
            set_ai_override(session, "ai_provider", None)
    # Phase 1: per-kind providers, so phase 2 maps models against them.
    for kind in ("chat", "vision", "image", "voice"):
        wanted = getattr(body, f"{kind}_provider")
        if wanted is None:
            continue
        text = wanted.strip().lower()
        if text:
            allowed = KIND_PROVIDERS[kind]
            if text not in allowed:
                raise HTTPException(
                    400,
                    f'provider "{wanted}" cannot run {kind} (want: {", ".join(allowed)})',
                )
            set_ai_override(session, f"{kind}_provider", text)
        else:
            set_ai_override(session, f"{kind}_provider", None)
    apply_ai_overrides(session)
    # Phase 1.5: provider API keys. Non-blank stores a studio row (hot,
    # no restart); blank clears back to the env default; null is untouched.
    for key in AI_SECRET_KEYS:
        wanted = getattr(body, key, None)
        if wanted is None:
            continue
        set_ai_override(session, key, wanted.strip() or None)
    apply_ai_overrides(session)
    # Phase 2: models against the now-effective providers.
    for kind in ("chat", "vision", "image", "video", "transcribe", "voice"):
        wanted = getattr(body, f"{kind}_model")
        if wanted is None:
            continue
        effective = provider_for(kind)
        field = model_field(effective, kind)
        if field is None:
            raise HTTPException(
                400, f'provider "{effective}" has no selectable {kind} model'
            )
        text = wanted.strip()
        if not text:
            # Blank clears the kind across every provider, so no dormant
            # row resurrects an old model on a later provider switch.
            # (Video has no meta/ai/local fields — allowlist decides.)
            for prefix in PROVIDER_PREFIX.values():
                key = f"{prefix}{kind}_model"
                if key in AI_OVERRIDE_KEYS:
                    set_ai_override(session, key, None)
            continue
        if effective == "openrouter" and kind in ("image", "video"):
            try:
                check_openrouter_model(kind, text)
            except ProviderError as exc:
                raise HTTPException(400, str(exc))
        set_ai_override(session, field, text)
    apply_ai_overrides(session)
    return agent_status(session, user)


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
    scene, _ = auto_direct_core(
        session, user, project_id, seconds, body.motions,
        scene_id=body.scene_id, new_scene=body.new_scene,
    )
    return scene
