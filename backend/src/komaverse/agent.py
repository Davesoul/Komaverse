"""Studio agent — the Komaverse differentiator.

Chat that operates the studio: status, panel detection, auto-direction,
MP4 export, and image generation. Two brains:

- DemoBrain: keyword intents, always works, executes the REAL tools
  (same endpoint functions as the UI, so auth scoping, validation,
  metrics, and credits apply identically).
- CloudBrain: tool-calling LLM when AI_API_KEY is set; any failure falls
  back to DemoBrain.
"""

from __future__ import annotations

import json
import logging
import re

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from komaverse.api.ai import COSTS, PROVIDERS, add_entry, get_balance, run_ai_job
from komaverse.api.animate import (
    MOTIONS,
    ClipCreate,
    SceneCreate,
    add_clip,
    create_scene,
    list_scenes,
    run_export_job,
)
from komaverse.api.scans import (
    DetectRequest,
    detect_panels,
    get_project,
    list_projects,
)
from komaverse.config import settings
from komaverse.db_models import (
    AudioTakeRow,
    ExportReportRow,
    FrameRow,
    JobRow,
    PageRow,
    PanelRow,
    UserRow,
)
from komaverse.security import limiter
from pydantic import BaseModel

logger = logging.getLogger("komaverse.agent")


class AgentRef(BaseModel):
    """One @-mention parsed by the client: a specific studio entity."""

    kind: str  # frame | panel | take | gen
    id: str


def resolve_refs(
    refs: list[AgentRef | dict] | None, session: Session, user: UserRow
) -> str:
    """Turn @-mentions into ownership-scoped context lines.

    Every id is resolved against the caller's own projects (get_project
    raises 404 unless owned); unknown or foreign ids are named as missing,
    never leaked.
    """
    lines: list[str] = []
    for ref in refs or []:
        if isinstance(ref, dict):
            kind, rid = ref.get("kind"), ref.get("id")
        else:
            kind, rid = ref.kind, ref.id
        try:
            if kind == "frame":
                row = session.get(FrameRow, rid)
                if row is None:
                    raise _Missing()
                get_project(row.project_id, session, user)
                layers = ", ".join(l.name for l in row.layers[:6]) or "no layers"
                lines.append(
                    f"@frame:{rid} = Frame '{row.name or 'Untitled'}' "
                    f"({row.width}x{row.height}, {len(row.layers)} layers: {layers})"
                )
            elif kind == "panel":
                row = session.get(PanelRow, rid)
                if row is None:
                    raise _Missing()
                page = session.get(PageRow, row.page_id)
                if page is None:
                    raise _Missing()
                get_project(page.project_id, session, user)
                lines.append(
                    f"@panel:{rid} = Panel {row.reading_order + 1} on PG "
                    f"{page.index + 1} (box x={row.x:.0f} y={row.y:.0f} "
                    f"w={row.w:.0f} h={row.h:.0f})"
                )
            elif kind == "take":
                row = session.get(AudioTakeRow, rid)
                if row is None:
                    raise _Missing()
                get_project(row.project_id, session, user)
                lines.append(
                    f"@take:{rid} = Voice take '{row.name}' "
                    f"({row.duration_sec:.1f}s, {row.mime or 'audio'})"
                )
            elif kind == "gen":
                row = session.get(JobRow, rid)
                if row is None or row.kind != "image":
                    raise _Missing()
                get_project(row.project_id, session, user) if row.project_id else None
                prompt = (row.prompt or "")[:200]
                lines.append(
                    f"@gen:{rid} = AI generation ({row.status})"
                    + (f": '{prompt}'" if prompt else "")
                )
            else:
                lines.append(f"@{kind}:{rid} (unknown reference kind)")
        except HTTPException:
            lines.append(f"@{kind}:{rid} (not found)")
    return "\n".join(lines)


class _Missing(HTTPException):
    def __init__(self) -> None:
        super().__init__(404, "not found")

DEFAULT_MOTIONS = ["zoom-in", "zoom-out", "pan-left", "pan-right"]
EXPORT_KEYWORDS = ("export", "render", "video", "mp4")
CAPABILITIES = (
    "I can: project status, detect panels on all pages, auto-direct a "
    "timeline from your panels, export MP4, and generate images "
    "(\"generate a moonlit robot\")."
)


# ---------------------------------------------------------------- tools ---
# Each tool calls the real endpoint functions. Returns a human summary.


def parse_seconds(text: str) -> float | None:
    """Clip length from chat ("3s", "2.5 seconds") or None when absent."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)\b", text.lower())
    if not m:
        return None
    return min(max(float(m.group(1)), 0.1), 30.0)


def parse_motions(text: str) -> list[str] | None:
    """Motion names from chat ("zoom in, pan left") or None when absent."""
    lowered = text.lower().replace("zoom in", "zoom-in").replace("zoom out", "zoom-out")
    lowered = lowered.replace("pan left", "pan-left").replace("pan right", "pan-right")
    found = [m for m in MOTIONS if m in lowered]
    return found or None


def tool_status(session: Session, user: UserRow, project_id: str) -> str:
    p = get_project(project_id, session, user)
    panels = sum(len(page.panels) for page in p.pages)
    clips = sum(len(s.clips) for s in p.scenes)
    return (
        f"'{p.name}': {len(p.pages)} pages, {panels} panels, "
        f"{clips} clips in {len(p.scenes)} scenes, {len(p.frames)} frames."
    )


def tool_detect_all(session: Session, user: UserRow, project_id: str) -> str:
    p = get_project(project_id, session, user)
    if not p.pages:
        return "No pages to detect — upload a scan first."
    total = 0
    for page in p.pages:
        # Explicit user ask → replace existing panels without the 409 guard.
        res = detect_panels(
            page.id,
            DetectRequest(detector="heuristic", confirm=True),
            session,
            user,
        )
        total += len(res.panels)
    return f"Detected {total} panels across {len(p.pages)} pages (heuristic, free)."


def auto_direct_core(
    session: Session,
    user: UserRow,
    project_id: str,
    seconds: float = 2.0,
    motions: list[str] | None = None,
):
    """All panels → timeline clips in reading order. Returns the Scene."""
    from komaverse.api.animate import MOTIONS

    motions = motions or DEFAULT_MOTIONS
    for motion in motions:
        if motion not in MOTIONS:
            raise HTTPException(400, f"unknown motion: {motion}")
    p = get_project(project_id, session, user)
    panels: list[tuple[int, int, object]] = []
    for page in sorted(p.pages, key=lambda pg: pg.index):
        for panel in sorted(page.panels, key=lambda pn: pn.reading_order):
            panels.append((page.index, panel.reading_order, panel))
    if not panels:
        raise HTTPException(400, "no panels to direct — detect or upload first")
    scenes = list_scenes(project_id, session, user)
    if scenes and not scenes[0].clips:
        target = scenes[0]
    else:
        target = create_scene(
            project_id, SceneCreate(name="Auto cut"), session, user
        )
    existing = {c.source_id for c in target.clips if c.kind == "panel"}
    added = 0
    for i, (_, _, panel) in enumerate(panels):
        if panel.id in existing:
            continue
        add_clip(
            target.id,
            ClipCreate(
                kind="panel",
                source_id=panel.id,
                duration=seconds,
                motion=motions[i % len(motions)],
            ),
            session,
            user,
        )
        added += 1
    refreshed = [s for s in list_scenes(project_id, session, user) if s.id == target.id]
    return refreshed[0], added


def tool_auto_direct(
    session: Session,
    user: UserRow,
    project_id: str,
    seconds: float = 2.0,
    motions: list[str] | None = None,
) -> str:
    scene, added = auto_direct_core(session, user, project_id, seconds, motions)
    if added == 0:
        return f"'{scene.name}' already has every panel — nothing to add."
    return (
        f"Added {added} clips to '{scene.name}' "
        f"({seconds}s each, {', '.join(motions or DEFAULT_MOTIONS)})."
    )


def tool_export(
    session: Session, user: UserRow, project_id: str, job_factory=None
) -> str:
    # Chat-driven exports respect the same 5/min bucket as POST /export.
    retry = limiter.check(f"chat-export:{user.id}", 5, 60)
    if retry is not None:
        raise HTTPException(
            429,
            "export rate limited (5/min) — try again in a few seconds",
            headers={"Retry-After": str(int(retry) + 1)},
        )
    p = get_project(project_id, session, user)
    clips = sum(len(s.clips) for s in p.scenes)
    chained = ""
    if clips == 0:
        panels = sum(len(page.panels) for page in p.pages)
        if panels == 0:
            return "Nothing to export — upload and detect panels first."
        tool_auto_direct(session, user, project_id)
        chained = "No clips, so I cut the timeline first. "
    job = JobRow(
        user_id=user.id,
        project_id=project_id,
        kind="export",
        prompt=json.dumps(
            {
                "fps": p.fps,
                "width": p.canvas_width,
                "height": p.canvas_height,
                "include_audio": True,
            }
        ),
        provider="local",
        width=p.canvas_width,
        height=p.canvas_height,
    )
    session.add(job)
    session.commit()
    run_export_job(job.id, job_factory)
    session.refresh(job)
    if job.status != "done":
        return f"Export failed: {job.error or 'unknown error'}"
    report = session.get(ExportReportRow, job.id)
    n = report.clips_rendered if report else clips
    secs = report.rendered_sec if report else 0.0
    return f"{chained}Rendered {n} clips ({secs:.1f}s): {job.result_url}"


def tool_generate(
    session: Session,
    user: UserRow,
    project_id: str,
    prompt: str,
    job_factory=None,
) -> str:
    provider = (
        settings.ai_provider if settings.ai_provider in PROVIDERS else "cloud"
    )
    if provider == "cloud" and not settings.ai_api_key:
        provider = "fake"
    if provider == "fake" and settings.is_production:
        return "No AI provider configured."
    cost = COSTS["image"]
    if settings.paywall_enabled:
        if get_balance(session, user.id) < cost:
            return f"Not enough credits (need {cost})."
        add_entry(session, user.id, -cost, "image generation")
    job = JobRow(
        user_id=user.id,
        project_id=project_id,
        kind="image",
        prompt=prompt,
        provider=provider,
        width=1024,
        height=1024,
        attach_frame=True,
    )
    session.add(job)
    session.commit()
    run_ai_job(job.id, job_factory)
    session.refresh(job)
    if job.status == "done":
        spent = f" ({cost} credits)" if settings.paywall_enabled else ""
        return (
            f"Generated and attached as a frame: {job.result_url}{spent} "
            f"[job {job.id}]"
        )
    return f"Generation failed: {job.error}"


# ------------------------------------------------------------ DemoBrain ---


def demo_turn(
    message: str,
    project_id: str,
    session: Session,
    user: UserRow,
    job_factory=None,
    clip_seconds: float = 2.0,
    ref_context: str = "",
) -> tuple[str, list[dict]]:
    text = message.lower().strip()
    actions: list[dict] = []

    def run(tool: str, fn, *args):
        try:
            summary = fn(*args)
        except HTTPException as exc:
            summary = str(exc.detail)
        actions.append({"tool": tool, "summary": summary})
        return summary

    if any(k in text for k in ("detect", "find panel", "scan")):
        reply = run("detect_all", tool_detect_all, session, user, project_id)
    elif any(k in text for k in EXPORT_KEYWORDS):
        reply = run("export", tool_export, session, user, project_id, job_factory)
    elif any(k in text for k in ("animate", "timeline", "direct", "clips", "cut")):
        p = get_project(project_id, session, user)
        if sum(len(page.panels) for page in p.pages) == 0 and p.pages:
            run("detect_all", tool_detect_all, session, user, project_id)
            chained = "I detected panels first. "
        else:
            chained = ""
        seconds = parse_seconds(message) or clip_seconds
        motions = parse_motions(message)
        reply = chained + run(
            "auto_direct",
            tool_auto_direct,
            session,
            user,
            project_id,
            seconds,
            motions,
        )
    elif any(k in text for k in ("generate", "create image", "draw me", "make me")):
        prompt = message.strip()
        for prefix in ("generate", "create image", "draw me", "make me"):
            if text.startswith(prefix):
                prompt = message[len(prefix):].strip(" :")
                break
        if not prompt:
            return "Tell me what to generate — e.g. \"generate a moonlit robot\".", []
        reply = run(
            "create_image", tool_generate, session, user, project_id, prompt, job_factory
        )
    elif any(
        k in text for k in ("status", "summary", "what", "show", "list", "how many")
    ):
        reply = run("project_status", tool_status, session, user, project_id)
    else:
        # No tool matched: @-mentions still get an answer grounded in what
        # they point at; otherwise the generic capabilities line.
        if ref_context:
            reply = (
                f"About what you @-mentioned:\n{ref_context}\n"
                f"Tell me what to do with it — e.g. direct it, describe it, "
                f"or export it. {CAPABILITIES}"
            )
        else:
            reply = f"Not sure what you mean. {CAPABILITIES}"
    return reply, actions


# ----------------------------------------------------------- CloudBrain ---

_SYSTEM = (
    "You operate Komaverse, an anime studio. Use tools to act on the user's "
    "project, then summarize briefly. Never invent IDs, counts, or URLs — "
    "only report what tools return."
)

_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "project_status",
            "description": "Summarize pages, panels, clips, and frames.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_all",
            "description": "Detect panels on every page of the project.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "auto_direct",
            "description": "Cut all panels into timeline clips in reading order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "number",
                        "description": "Seconds per clip (0.1-30, default 2).",
                    },
                    "motions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Motion cycle, e.g. [\"zoom-in\", \"pan-left\"]. "
                            "Valid: none, zoom-in, zoom-out, pan-left, pan-right."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_video",
            "description": "Render the timeline to MP4.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_image",
            "description": "Generate an image from a prompt, attached as a frame.",
            "parameters": {
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
                "required": ["prompt"],
            },
        },
    },
]


def cloud_turn(
    message: str,
    project_id: str,
    session: Session,
    user: UserRow,
    job_factory=None,
    ref_context: str = "",
) -> tuple[str, list[dict]]:
    context = tool_status(session, user, project_id)
    user_content = f"Project: {context}\n\n{message}"
    if ref_context:
        user_content = (
            f"Project: {context}\n\nReferenced:\n{ref_context}\n\n{message}"
        )
    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user_content},
    ]
    actions: list[dict] = []
    for _ in range(6):
        res = httpx.post(
            f"{settings.ai_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.ai_api_key}"},
            json={
                "model": settings.ai_chat_model,
                "messages": messages,
                "tools": _TOOLS_SCHEMA,
                "tool_choice": "auto",
            },
            timeout=60,
        )
        res.raise_for_status()
        msg = res.json()["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or "Done.", actions
        messages.append(msg)
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            try:
                if name == "project_status":
                    result = tool_status(session, user, project_id)
                elif name == "detect_all":
                    result = tool_detect_all(session, user, project_id)
                elif name == "auto_direct":
                    try:
                        seconds = min(max(float(args.get("seconds", 2.0)), 0.1), 30.0)
                    except (TypeError, ValueError):
                        seconds = 2.0
                    motions = args.get("motions")
                    if not isinstance(motions, list) or not all(
                        isinstance(m, str) and m in MOTIONS for m in motions
                    ):
                        motions = None
                    result = tool_auto_direct(
                        session, user, project_id, seconds, motions
                    )
                elif name == "export_video":
                    result = tool_export(session, user, project_id, job_factory)
                elif name == "create_image":
                    result = tool_generate(
                        session, user, project_id,
                        str(args.get("prompt") or message), job_factory,
                    )
                else:
                    result = f"unknown tool: {name}"
            except HTTPException as exc:
                result = f"error {exc.status_code}: {exc.detail}"
            actions.append({"tool": name, "summary": result[:200]})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result[:2000],
                }
            )
    return "I ran out of steps — try a smaller request.", actions


# ------------------------------------------------------------------ entry ---


def run_agent_turn(
    message: str,
    project_id: str | None,
    session: Session,
    user: UserRow,
    job_factory=None,
    clip_seconds: float = 2.0,
    refs: list[AgentRef | dict] | None = None,
) -> dict:
    if project_id is not None:
        # Explicit target: ownership check first (404 unless owned).
        schema = get_project(project_id, session, user)
        target = project_id
    else:
        projects = list_projects(session, user)
        if not projects:
            return {
                "reply": "You have no projects yet — create one in the Scan tab first.",
                "project_id": None,
                "project_name": None,
                "actions": [],
            }
        lowered = message.lower()
        named = [
            p
            for p in projects
            if re.search(r"\b" + re.escape(p.name.lower()) + r"\b", lowered)
        ]
        if len(named) == 1:
            target = named[0].id
        elif len(projects) == 1:
            target = projects[0].id
        else:
            names = ", ".join(f"'{p.name}'" for p in projects)
            return {
                "reply": (
                    f"Which project? Open one in the studio first, or name it "
                    f"({names})."
                ),
                "project_id": None,
                "project_name": None,
                "actions": [],
            }
        schema = get_project(target, session, user)
    ref_context = resolve_refs(refs, session, user) if refs else ""
    if settings.ai_api_key and settings.ai_base_url:
        try:
            reply, actions = cloud_turn(
                message, target, session, user, job_factory, ref_context
            )
            return {
                "reply": reply,
                "project_id": target,
                "project_name": schema.name,
                "actions": actions,
            }
        except Exception as exc:
            logger.warning("cloud agent failed (%s); using demo brain", exc)
    reply, actions = demo_turn(
        message, target, session, user, job_factory, clip_seconds, ref_context
    )
    return {
        "reply": f"[{schema.name}] {reply}",
        "project_id": target,
        "project_name": schema.name,
        "actions": actions,
    }
