"""Studio agent — the Komaverse differentiator.

Chat that operates the studio: status, panel detection, auto-direction,
MP4 export, and image generation. Two brains:

- DemoBrain: keyword intents, always works, executes the REAL tools
  (same endpoint functions as the UI, so auth scoping, validation,
  metrics, and credits apply identically).
- CloudBrain: tool-calling LLM (Meta Muse, OpenRouter, or generic cloud,
  via AI_PROVIDER) when its key is set; any failure falls back to DemoBrain.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import re
import threading

from fastapi import HTTPException
from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.orm import Session

from komaverse import storage
from komaverse.ai.providers import (
    MetaProvider,
    ProviderError,
    chat_complete,
    chat_configured,
    get_chat_config,
    chat_complete_with_usage,
    chat_complete_stream,
)
from komaverse.api.ai import COSTS, PROVIDERS, add_entry, cancel_job, get_balance, list_entities, run_ai_job
from komaverse.api.animate import (
    BLEND_MODES,
    MAX_LAYERS,
    MOTIONS,
    TRANSITIONS,
    ClipCreate,
    ClipOrder,
    ClipPatch,
    FrameCreate,
    FrameOrder,
    frame_from_media_source,
    load_media_image,
    SceneCreate,
    ScenePatch,
    SequenceCreate,
    SequencePatch,
    VideoToFrames,
    _next_frame_index,
    _resolve_sequence,
    add_clip,
    create_frame,
    create_scene,
    create_sequence,
    delete_clip,
    delete_frame,
    delete_scene,
    delete_sequence,
    duplicate_frame,
    duplicate_scene,
    duplicate_sequence,
    list_scenes,
    list_sequences,
    order_clips,
    order_frames,
    patch_clip,
    patch_scene,
    patch_sequence,
    run_export_job,
    video_to_frames,
)
from komaverse.api.audio import CueCreate, add_cue, list_takes
from komaverse.api.scans import (
    DetectRequest,
    ProjectCreate,
    create_project,
    detect_panels,
    get_project,
    list_projects,
)
from komaverse.api.subtitles import save_subtitles_track
from komaverse.api.usage import record_usage
from komaverse.config import settings
from komaverse.studio_settings import provider_for
from komaverse.subtitles import SubSegment, segments_to_srt
from komaverse.db_models import (
    AudioTakeRow,
    LayerRow,
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
                pname = page.name or f"PG {page.index + 1}"
                rname = row.name or f"Panel {row.reading_order + 1}"
                lines.append(
                    f"@panel:{rid} = {rname} on {pname} "
                    f"(box x={row.x:.0f} y={row.y:.0f} "
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
            elif kind == "media":
                row = session.get(PageRow, rid)
                if row is None:
                    raise _Missing()
                get_project(row.project_id, session, user)
                mname = row.name or f"Scan page {row.index + 1}"
                lines.append(
                    f"@media:{rid} = {mname} "
                    f"({row.width}x{row.height}, {len(row.panels)} panels)"
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
    "I can: create projects and frames, report project status, detect "
    "panels on all pages, auto-direct a timeline from your panels, add any "
    "panel, frame, or scan page as a clip (\"add page 1 to the timeline\"), "
    "add captions (\"add subtitles: hello 0-2s\"), export MP4, generate "
    "images (\"generate a moonlit robot\") and videos (\"add the video "
    "to the timeline\" once done), cue voice takes "
    "(\"put the take at 2s\"), frame scan pages (\"put page 1 on a frame\"), "
    "manage draw layers (\"add a tone layer\", \"hide the ink layer\"), "
    "reorder frames and clips, run sequences (\"new sequence\") and "
    "timelines (\"duplicate the timeline\"), "
    "and check on running jobs (\"check job abc123\")."
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
    lowered = lowered.replace("pan up", "pan-up").replace("pan down", "pan-down")
    found = [m for m in MOTIONS if m in lowered]
    return found or None


def _parse_transition(text: str) -> str:
    """Transition-into-clip from chat ("with a crossfade", "fade to black")."""
    t = text.lower()
    if "impact" in t or "white flash" in t:
        return "impact"
    if "fade to black" in t or "fade-black" in t or "dip to black" in t:
        return "fade-black"
    if "crossfade" in t or "cross-fade" in t or "cross fade" in t or "dissolve" in t:
        return "crossfade"
    if "wipe" in t:
        return "wipe-left"
    return "none"


def _parse_fade(text: str, which: str) -> float | None:
    """Fade seconds from chat ("fade in 2s", "with fade out") or None."""
    m = re.search(r"fade[-\s]?%s\s*(\d+(?:\.\d+)?)\s*s?" % which, text.lower())
    if m:
        try:
            return min(max(float(m.group(1)), 0.0), 10.0)
        except ValueError:
            return None
    if re.search(r"fade[-\s]?%s\b" % which, text.lower()):
        return 0.5
    return None


def _parse_ratio(text: str) -> tuple[int, int] | None:
    """Image size from chat aspect words; None keeps the 1024 square."""
    t = text.lower()
    if any(k in t for k in ("16:9", "16x9", "wide shot", "wide framing", "horizontal video")):
        return (1280, 720)
    if any(k in t for k in ("9:16", "9x16", "vertical video", "tall format")):
        return (720, 1280)
    if "4:3" in t or "4x3" in t:
        return (1024, 768)
    if "3:4" in t or "3x4" in t:
        return (768, 1024)
    if "1:1" in t or "square format" in t:
        return (1024, 1024)
    return None


def _composition_hint(text: str) -> str:
    """Prompt-level framing hint for video (the API takes no size)."""
    t = text.lower()
    if any(k in t for k in ("16:9", "wide shot", "wide framing", "horizontal", "cinematic")):
        return "Wide 16:9 cinematic framing. "
    if any(k in t for k in ("9:16", "vertical", "tall", "shorts", "reel", "tiktok")):
        return "Vertical 9:16 framing. "
    if "square" in t:
        return "Square 1:1 framing. "
    return ""


def tool_status(session: Session, user: UserRow, project_id: str) -> str:
    p = get_project(project_id, session, user)
    panels = sum(len(page.panels) for page in p.pages)
    clips = sum(len(s.clips) for s in p.scenes)
    return (
        f"'{p.name}': {len(p.pages)} pages, {panels} panels, "
        f"{clips} clips in {len(p.scenes)} scenes, {len(p.frames)} frames."
    )


DETECTORS = ("heuristic", "yolo", "yolo-leoxs22", "cloud", "meta", "openrouter", "sam", "local")


def tool_create_project(session: Session, user: UserRow, name: str) -> str:
    schema = create_project(
        ProjectCreate(name=name.strip() or "Untitled"), session, user
    )
    return f"Created project '{schema.name}' [project {schema.id}] — upload scans to begin."


def tool_create_frame(
    session: Session, user: UserRow, project_id: str, name: str = "", sequence=None
) -> str:
    schema = create_frame(
        project_id,
        FrameCreate(
            name=name.strip() or "Untitled",
            sequence_id=str(sequence) if sequence else None,
        ),
        session,
        user,
    )
    return f"Created frame '{schema.name}' [frame {schema.id}] ({schema.width}x{schema.height})."


def parse_detector(text: str) -> str:
    lowered = text.lower()
    if "leox" in lowered:
        return "yolo-leoxs22"
    for name in sorted(DETECTORS, key=len, reverse=True):
        if name in lowered:
            return name
    return "yolo-leoxs22"


def parse_direction(text: str) -> str:
    lowered = text.lower()
    if "left to right" in lowered or "left-to-right" in lowered or re.search(r"\bltr\b", lowered):
        return "ltr"
    return "rtl"


def tool_detect_all(
    session: Session,
    user: UserRow,
    project_id: str,
    detector: str = "yolo-leoxs22",
    direction: str = "rtl",
) -> str:
    if detector not in DETECTORS:
        detector = "yolo-leoxs22"
    if direction not in ("rtl", "ltr"):
        direction = "rtl"
    p = get_project(project_id, session, user)
    if not p.pages:
        return "No pages to detect — upload a scan first."
    total = 0
    used = detector
    for page in p.pages:
        # Explicit user ask → replace existing panels without the 409 guard.
        res = detect_panels(
            page.id,
            DetectRequest(detector=detector, direction=direction, confirm=True),
            session,
            user,
        )
        used = res.detector_used
        total += len(res.panels)
    if used != detector:
        return f"Detected {total} panels across {len(p.pages)} pages ({detector} unavailable, fell back to {used}, {direction}, free)."
    return f"Detected {total} panels across {len(p.pages)} pages ({used}, {direction}, free)."


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
    session: Session, user: UserRow, project_id: str, job_factory=None, background: bool = False,
    scene=None
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
    if scene is not None:
        try:
            target = _resolve_scene(session, user, project_id, scene)
        except HTTPException as exc:
            detail = str(exc.detail)
            if detail.startswith("unknown scene"):
                return f"Unknown timeline '{scene}'."
            return detail
        scenes = [target]
    else:
        scenes = [s for s in p.scenes if s.clips]
        if len(scenes) > 1:
            names = "; ".join(f"'{s.name}' [scene {s.id}]" for s in scenes)
            return f"Which timeline? {names} — say 'export the NAME timeline'."
    clips = sum(len(s.clips) for s in scenes)
    chained = ""
    if clips == 0:
        panels = sum(len(page.panels) for page in p.pages)
        if panels == 0:
            return "Nothing to export — upload and detect panels first."
        if scene is not None:
            return f"Timeline '{target.name}' has no clips yet — add clips first."
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
                "scene_id": scenes[0].id if len(scenes) == 1 else None,
            }
        ),
        provider="local",
        width=p.canvas_width,
        height=p.canvas_height,
    )
    session.add(job)
    session.commit()
    if background:
        threading.Thread(target=run_export_job, args=(job.id, job_factory), daemon=True).start()
        return f"{chained}Export started ({clips} clips) [job {job.id}] - check it with check_job."
    run_export_job(job.id, job_factory)
    session.refresh(job)
    if job.status != "done":
        return f"Export failed: {job.error or 'unknown error'}"
    report = session.get(ExportReportRow, job.id)
    n = report.clips_rendered if report else clips
    secs = report.rendered_sec if report else 0.0
    return f"{chained}Rendered {n} clips ({secs:.1f}s): {job.result_url}"


REF_TOKEN_RE = re.compile(r"@(frame|panel|media|gen):([A-Za-z0-9_-]+)")

REFERENCE_INTENT_RE = re.compile(
    r"(us(e|ing)\s+(this|that|the|it|@)|as\s+(a\s+)?ref|with\s+(this|that|the|it|@)|"
    r"based\s+on|from\s+this|in\s+the\s+style\s+of|same\s+style|like\s+this|"
    r"look\s+at|see\s+attached|attached|trace|redraw|match\s+(the\s+)?style|"
    r"paint\s+over|reference\s+(image|photo|picture|frame|it|this)|"
    r"edit\s+(this|it|the|that)|change\s+(this|it|that)|fix\s+(this|it|that)|"
    r"continue\s+(this|it|that))",
    re.IGNORECASE,
)


def wants_reference(text: str | None) -> bool:
    """True when the text asks to reuse a frame/panel/scan/generation.

    Bare @-mentions carry no intent: the turn scopes the open frame by
    injecting its token, and the model echoes tokens into prompts, so
    only phrasal intent (or an explicitly picked reference) attaches one.
    """
    return bool(REFERENCE_INTENT_RE.search(text or ""))


def clean_prompt_text(prompt: str) -> str:
    return REF_TOKEN_RE.sub("", prompt or "").strip(" :")


def gather_reference_snapshots(session, user, project_id, text, references=None, entity_ids=None):
    snaps: list = []
    notes: list = []
    seen: set = set()

    def add(key, snap):
        if key not in seen:
            seen.add(key)
            snaps.append(snap)

    def entity_snap(ent):
        return {"id": ent.id, "name": ent.name or "entity", "kind": ent.kind or "prop", "anchor": ent.anchor or "", "frame_id": ent.frame_id}

    entities = list_entities(project_id, session, user)
    by_id = {e.id: e for e in entities}
    for eid in entity_ids or []:
        ent = by_id.get(str(eid))
        if ent is None:
            notes.append(f"Unknown entity {eid} - ignored.")
        else:
            add(("entity", ent.id), entity_snap(ent))
    lowered = (text or "").lower()
    for ent in entities:
        name = (ent.name or "").strip()
        if len(name) >= 3 and re.search(r"\b" + re.escape(name.lower()) + r"\b", lowered):
            add(("entity", ent.id), entity_snap(ent))
    for kind, rid in REF_TOKEN_RE.findall(text or ""):
        if kind == "frame":
            row = session.get(FrameRow, rid)
            if row is None or row.project_id != project_id:
                notes.append(f"Unknown frame {rid} - ignored.")
                continue
            add(("frame", rid), {"id": f"frame:{rid}", "name": f"Frame '{row.name or 'Untitled'}'", "kind": "reference", "anchor": "reference image", "frame_id": rid})
        elif kind == "panel":
            panel = session.get(PanelRow, rid)
            page = session.get(PageRow, panel.page_id) if panel else None
            if panel is None or page is None or page.project_id != project_id:
                notes.append(f"Unknown panel {rid} - ignored.")
                continue
            add(("panel", rid), {"id": f"panel:{rid}", "name": f"Panel {panel.reading_order + 1} on page {page.index + 1}", "kind": "reference", "anchor": "reference image", "panel_id": rid})
        elif kind == "media":
            page = session.get(PageRow, rid)
            if page is None or page.project_id != project_id:
                notes.append(f"Unknown scan {rid} - ignored.")
                continue
            add(("page", rid), {"id": f"page:{rid}", "name": f"Scan page {page.index + 1}", "kind": "reference", "anchor": "reference image", "page_id": rid})
        elif kind == "gen":
            job = session.get(JobRow, rid)
            if job is None or job.user_id != user.id or job.project_id != project_id or job.kind != "image":
                notes.append(f"Unknown generation {rid} - ignored.")
                continue
            if job.status != "done":
                notes.append(f"Generation {rid} is still {job.status} - ignored for now.")
                continue
            add(("gen", rid), {"id": f"gen:{rid}", "name": f"Generation '{(job.prompt or '')[:40]}'", "kind": "reference", "anchor": "reference image", "job_id": rid})
    for ref in references or []:
        if isinstance(ref, dict) and ref.get("kind") and ref.get("id"):
            extra, more = gather_reference_snapshots(session, user, project_id, f"@{ref['kind']}:{ref['id']}")
            for s in extra:
                add((s.get("id"), None), s)
            notes.extend(more)
    return snaps, notes


def _generation_references(raw: str, references=None, fallback_references=None):
    """Merge explicit references with @-tokens from raw text.

    Only @frame: tokens are ever scope-injected into turns, so only
    they need phrasal reuse intent (the model echoes the open-frame
    token into prompts unasked). panel/media/gen tokens are never
    injected: their presence means the user typed or picked them, so
    they always attach. When the text shows intent but names no id,
    turn-level references (picked chips, the open frame on an intent
    turn) fill in. No intent and no frame token: no reference, ever.
    """
    refs = list(references or [])
    tokens = REF_TOKEN_RE.findall(raw or "")
    refs.extend({"kind": kind, "id": rid} for kind, rid in tokens if kind != "frame")
    if wants_reference(raw):
        refs.extend(
            {"kind": kind, "id": rid} for kind, rid in tokens if kind == "frame"
        )
        if not refs:
            for fb in fallback_references or []:
                if isinstance(fb, dict):
                    kind, rid = fb.get("kind"), fb.get("id")
                else:
                    kind, rid = getattr(fb, "kind", None), getattr(fb, "id", None)
                if kind and rid:
                    refs.append({"kind": kind, "id": rid})
    return refs


def tool_generate(
    session: Session,
    user: UserRow,
    project_id: str,
    prompt: str,
    job_factory=None,
    model: str | None = None,
    style: str = "none",
    negative_prompt: str = "",
    reference_frame_id: str | None = None,
    references=None,
    entity_ids=None,
    fallback_references=None,
    background: bool = False,
    width=None,
    height=None,
    attach_frame: bool = False,
) -> str:
    raw = prompt or ""
    prompt = clean_prompt_text(raw)
    if not prompt:
        return "Tell me what to generate - e.g. \"generate a moonlit robot\"."
    refs = _generation_references(raw, references, fallback_references)
    snaps, notes = gather_reference_snapshots(session, user, project_id, prompt, refs, entity_ids)
    provider = provider_for("image")
    if provider not in PROVIDERS:
        provider = "cloud"
    if provider == "cloud" and not settings.ai_api_key:
        provider = "fake"
    if provider == "fake" and settings.is_production:
        return "No AI provider configured."
    cost = COSTS["image"]
    if settings.paywall_enabled:
        if get_balance(session, user.id) < cost:
            return f"Not enough credits (need {cost})."
        add_entry(session, user.id, -cost, "image generation")
    if reference_frame_id is not None:
        frame = session.get(FrameRow, reference_frame_id)
        if frame is None or frame.project_id != project_id:
            return f"Unknown reference frame: {reference_frame_id}"
    if width is None or height is None:
        _ratio = _parse_ratio(prompt)
        if _ratio is not None:
            width, height = _ratio
    try:
        width = min(max(int(width or 1024), 64), 2048)
        height = min(max(int(height or 1024), 64), 2048)
    except (TypeError, ValueError):
        width = height = 1024
    job = JobRow(
        user_id=user.id,
        project_id=project_id,
        kind="image",
        prompt=prompt,
        provider=provider,
        model=model,
        width=width,
        height=height,
        attach_frame=attach_frame,
        style=style or "none",
        negative_prompt=negative_prompt or "",
        reference_frame_id=reference_frame_id,
        entities_json=json.dumps(snaps),
    )
    before = set(
        session.execute(
            select(FrameRow.id).where(FrameRow.project_id == project_id)
        ).scalars().all()
    )
    session.add(job)
    session.commit()
    if background:
        threading.Thread(target=run_ai_job, args=(job.id, job_factory), daemon=True).start()
        ref_bit = f" with {len(snaps)} reference(s)" if snaps else ""
        note_bit = (" " + " ".join(notes)) if notes else ""
        return f"Image job started [job {job.id}]{ref_bit} - check it with check_job.{note_bit}"
    run_ai_job(job.id, job_factory)
    session.refresh(job)
    if job.status == "done":
        spent = f" ({cost} credits)" if settings.paywall_enabled else ""
        after = set(
            session.execute(
                select(FrameRow.id).where(FrameRow.project_id == project_id)
            ).scalars().all()
        )
        new = after - before
        frame_bit = f" [frame {next(iter(new))}]" if len(new) == 1 else ""
        ref_bit = f" with {len(snaps)} reference(s): " + ", ".join(s.get("name", "ref") for s in snaps) if snaps else ""
        note_bit = (" " + " ".join(notes)) if notes else ""
        return (
            f"Generated{' and attached as a frame' if frame_bit else ''}: {job.result_url}{spent} "
            f"[job {job.id}]{frame_bit}{ref_bit}.{note_bit}"
        )
    return f"Generation failed: {job.error}"


def tool_create_video(
    session: Session,
    user: UserRow,
    project_id: str,
    prompt: str,
    seconds: float = 4.0,
    model: str | None = None,
    job_factory=None,
    references=None,
    entity_ids=None,
    fallback_references=None,
) -> str:
    """Start an async OpenRouter video job; watch it with check_job."""
    raw = prompt or ""
    prompt = clean_prompt_text(raw)
    if not prompt:
        return "Tell me what to render - e.g. \"generate a video of a robot walking\"."
    refs = _generation_references(raw, references, fallback_references)
    snaps, notes = gather_reference_snapshots(session, user, project_id, prompt, refs, entity_ids)
    provider = "openrouter"
    if not settings.openrouter_api_key:
        return (
            "No video provider configured (OPENROUTER_API_KEY is missing) — "
            "set it for video jobs, or generate an image instead."
        )
    cost = COSTS["video"]
    if settings.paywall_enabled:
        if get_balance(session, user.id) < cost:
            return f"Not enough credits (need {cost})."
        add_entry(session, user.id, -cost, "video generation")
    job = JobRow(
        user_id=user.id,
        project_id=project_id,
        kind="video",
        prompt=prompt,
        provider=provider,
        model=model,
        seconds=seconds,
        width=320,
        height=240,
        attach_frame=False,
        entities_json=json.dumps(snaps),
    )
    session.add(job)
    session.commit()
    threading.Thread(target=run_ai_job, args=(job.id, job_factory), daemon=True).start()
    ref_bit = f" with {len(snaps)} reference(s)" if snaps else ""
    note_bit = (" " + " ".join(notes)) if notes else ""
    return f"Video job started [job {job.id}]{ref_bit} - check it with check_job.{note_bit}"


def tool_check_job(session: Session, user: UserRow, job_id: str) -> str:
    """Report one job's status — the agent's callback for finished work."""
    job = session.get(JobRow, job_id)
    if job is None or job.user_id != user.id:
        return f"Job {job_id} not found."
    if job.status == "done":
        frame_bit = ""
        if job.kind == "image":
            fid = find_generated_frame(session, job)
            frame_bit = f"[frame {fid}]" if fid else ""
        return (
            f"Job {job.id} ({job.kind}) is done: {job.result_url} [job {job.id}]{frame_bit}"
        )
    if job.status == "failed":
        return f"Job {job.id} ({job.kind}) failed: {job.error or 'unknown error'}"
    return (
        f"Job {job.id} ({job.kind}) is {job.status} "
        f"({int((job.progress or 0) * 100)}%) — still working, check again soon."
    )


ADD_WORDS = ("add", "put", "place", "insert", "animate")
TIMELINE_WORDS = ("timeline", "clip", "scene")
SUBTITLE_WORDS = ("subtitle", "subtitles", "caption", "captions", ".srt")
SUBTITLE_VERBS = ("add", "create", "put", "write", "make")


def _wants_image_timeline(text: str) -> bool:
    if _wants_video(text):
        return False
    return (
        any(k in text for k in ("generate", "create image", "draw me", "make me", "redraw"))
        and any(k in text for k in ADD_WORDS)
        and any(k in text for k in TIMELINE_WORDS)
    )


def _strip_timeline_ask(prompt: str) -> str:
    parts = list(re.finditer(r"\s+(?:and|then)\s+", prompt))
    for m in reversed(parts):
        if re.search(r"\b(timeline|clip|scene|animate|cut|direct)\b", prompt[m.end():]):
            return prompt[:m.start()].strip(" ,.:")
    return prompt


def _parse_scene_ref(text: str) -> str | None:
    m = re.search(r'\bscene\s+"([^"]+)"', text) or re.search(r"\bscene\s+'([^']+)'", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"\bscene\s*(\d+)\b", text)
    if m:
        return f"__index:{m.group(1)}"
    return None


def _resolve_scene(session, user, project_id, scene_ref=None):
    scenes = sorted(list_scenes(project_id, session, user), key=lambda s: s.order)
    if scene_ref:
        if scene_ref.startswith("__index:"):
            try:
                return scenes[int(scene_ref[len("__index:"):]) - 1]
            except (ValueError, IndexError):
                raise HTTPException(404, f"unknown scene: {scene_ref}")
        for s in scenes:
            if s.id == scene_ref or s.name.lower() == scene_ref.lower():
                return s
        raise HTTPException(404, f"unknown scene: {scene_ref}")
    if scenes:
        return scenes[0]
    return create_scene(project_id, SceneCreate(name="Agent cut"), session, user)


def tool_add_clip(
    session,
    user,
    project_id,
    kind,
    source_id,
    scene=None,
    duration=2.0,
    motion="none",
    transition="none",
    transition_duration=None,
    fade_in=None,
    fade_out=None,
) -> str:
    # Fresh reads: this session may have cached relations from an
    # earlier tool call in the same turn.
    session.expire_all()
    if kind not in ("panel", "frame", "media", "video"):
        return f"Timeline clips must be panel, frame, media, or video (not {kind})."
    if motion not in MOTIONS:
        motion = "none"
    if transition not in TRANSITIONS:
        transition = "none"
    try:
        tdur = min(max(float(transition_duration if transition_duration is not None else 0.5), 0.1), 2.0)
    except (TypeError, ValueError):
        tdur = 0.5
    def _fade(v, default=0.0):
        try:
            return min(max(float(default if v is None else v), 0.0), 10.0)
        except (TypeError, ValueError):
            return default
    fi, fo = _fade(fade_in), _fade(fade_out)
    try:
        duration = min(max(float(duration), 0.1), 30.0)
    except (TypeError, ValueError):
        duration = 2.0
    target = _resolve_scene(session, user, project_id, scene)
    clip = add_clip(
        target.id,
        ClipCreate(kind=kind, source_id=source_id, duration=duration, motion=motion, transition=transition, transition_duration=tdur, fade_in=fi, fade_out=fo),
        session,
        user,
    )
    fx = ""
    if transition != "none":
        fx += f", {transition} {tdur:g}s in"
    if fi:
        fx += f", fade in {fi:g}s"
    if fo:
        fx += f", fade out {fo:g}s"
    return f"Added {kind} clip to '{target.name}' ({duration:g}s, {motion}{fx}) [clip {clip.id}]."


def find_generated_frame(session, job):
    frames = session.execute(select(FrameRow).where(FrameRow.project_id == job.project_id, FrameRow.name == f"AI: {(job.prompt or '')[:40]}")).scalars().all()
    return frames[0].id if len(frames) == 1 else None


def resolve_clip_source(text, session, user, project_id):
    m = re.search(r"@(frame|panel|gen|media):([A-Za-z0-9_-]+)", text)
    if m:
        kind, rid = m.group(1), m.group(2)
        if kind == "frame":
            row = session.get(FrameRow, rid)
            if row is None or row.project_id != project_id:
                return f"Unknown frame {rid} in this project."
            return ("frame", rid)
        if kind == "panel":
            panel = session.get(PanelRow, rid)
            page = session.get(PageRow, panel.page_id) if panel else None
            if panel is None or page is None or page.project_id != project_id:
                return f"Unknown panel {rid} in this project."
            return ("panel", rid)
        if kind == "media":
            page = session.get(PageRow, rid)
            if page is None or page.project_id != project_id:
                return f"Unknown scan page {rid} in this project."
            return ("media", rid)
        job = session.get(JobRow, rid)
        if job is None or job.user_id != user.id or job.project_id != project_id or job.kind not in ("image", "video"):
            return f"Unknown generation {rid}."
        if job.kind == "video":
            if job.status != "done":
                return f"Job {rid} is still {job.status} - ask me to check it when done."
            return ("video", rid)
        if job.status != "done":
            return f"Job {rid} is still {job.status} - ask me to check it when done."
        fid = find_generated_frame(session, job)
        if fid is None:
            return f"Could not pin the frame for job {rid} - reference it directly (@frame:...)."
        return ("frame", fid)
    m = re.search(r"\b(?:page|pg)\s*(\d+)\b", text)
    if m:
        p = get_project(project_id, session, user)
        pages = sorted(p.pages, key=lambda pg: pg.index)
        i = int(m.group(1)) - 1
        if not 0 <= i < len(pages):
            return f"Page {m.group(1)} does not exist - this project has {len(pages)}."
        return ("media", pages[i].id)
    return "Say what to add - @frame:/@panel:/@gen:/@media:, 'page N', or 'create a frame and add it'."


def resolve_video_job(text, session, user, project_id):
    m = re.search(r"@gen:([A-Za-z0-9_-]+)", text)
    if m:
        job = session.get(JobRow, m.group(1))
        if job is None or job.user_id != user.id or job.project_id != project_id or job.kind != "video":
            return f"Unknown video generation {m.group(1)}."
        if job.status != "done":
            return f"Job {m.group(1)} is still {job.status} - ask me to check it when done."
        return ("video", job.id)
    jobs = session.execute(select(JobRow).where(JobRow.user_id == user.id, JobRow.project_id == project_id, JobRow.kind == "video", JobRow.status == "done")).scalars().all()
    if not jobs:
        return "No finished video yet - say 'generate a video of ...' first."
    if len(jobs) == 1:
        return ("video", jobs[0].id)
    listing = "; ".join(f"'{j.prompt[:40]}' [job {j.id}]" for j in jobs)
    return f"Which video? Say @gen:... ({listing})."


def parse_caption_parts(message):
    lowered = message.lower()
    trig = next((k for k in ("subtitles:", "subtitle:", "captions:", "caption:", "srt:") if k in lowered), None)
    body = message[lowered.index(trig) + len(trig):] if trig else message
    items = []
    for chunk in re.split(r"[;\n]+", body):
        chx = chunk.strip()
        if not chx:
            continue
        start = end = None
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:s|sec)?\s*(?:-|to|-->|->)\s*(\d+(?:\.\d+)?)\s*(?:s|sec)?", chx)
        if m:
            start, end = float(m.group(1)), float(m.group(2))
            chx = chx[:m.start()] + " " + chx[m.end():]
        else:
            m2 = re.search(r"\bat\s+(\d+(?:\.\d+)?)\s*(?:s|sec)?\b", chx)
            if m2:
                start = float(m2.group(1))
                chx = chx[:m2.start()] + " " + chx[m2.end():]
        text = chx.strip().strip("\"' \t").strip()
        text = re.sub(r"^(add|create|put|write|make)\s+(subtitles?|captions?|srt)\s*:?\s*", "", text, flags=re.IGNORECASE).strip()
        if not text:
            continue
        item = {"text": text}
        if start is not None:
            item["start_sec"] = start
        if end is not None:
            item["end_sec"] = end
        items.append(item)
    if not items:
        return 'Send captions like: add subtitles: "hello" 0-2s; "world" 2-4s (times optional).'
    return items


def normalize_caption_segments(items):
    if isinstance(items, str):
        from komaverse.subtitles import parse_srt as _parse
        try:
            return _parse(items)
        except ValueError as exc:
            return f"Could not read those subtitles: {exc}"
    segs = []
    cursor = 0.0
    for it in items or []:
        if not isinstance(it, dict):
            return "Each caption needs text with optional start_sec/end_sec."
        text = str(it.get("text") or "").strip()
        if not text:
            continue
        if len(text) > 500:
            return f"Caption too long (500 max): {text[:60]}"
        try:
            start = float(it["start_sec"]) if it.get("start_sec") is not None else None
            end = float(it["end_sec"]) if it.get("end_sec") is not None else None
        except (TypeError, ValueError):
            return f"Bad timing on caption: {text[:60]}"
        if start is None:
            start = cursor
        if end is None:
            end = start + 2.0
        if end - start < 0.1:
            return f"Caption shorter than 0.1s: {text[:60]}"
        cursor = end
        segs.append(SubSegment(start=start, end=end, text=text))
    if not segs:
        return 'No captions found - send text with optional start_sec/end_sec.'
    return segs


def tool_add_subtitles(session, user, project_id, segments=None, scene=None):
    target = _resolve_scene(session, user, project_id, scene)
    norm = normalize_caption_segments(segments or [])
    if isinstance(norm, str):
        return norm
    track = save_subtitles_track(session, user, target.id, segments_to_srt(norm).encode())
    n = len(track.segments)
    first, last = track.segments[0].start_sec, track.segments[-1].end_sec
    plural = "s" if n != 1 else ""
    return f"Added {n} caption{plural} to '{target.name}' ({first:.1f}s-{last:.1f}s)."


TAKE_WORDS = ("take", "takes", "audio", "voiceover", "voice", "recording", "sound")
CUE_VERBS = ("cue", "place", "put", "add", "at")


def tool_list_takes(session, user, project_id):
    takes = list_takes(project_id, session, user)
    if not takes:
        return "No takes yet - attach audio in the studio, then ask me to cue it."
    bits = [f"'{t.name or 'Untitled'}' {t.duration_sec:.1f}s [take {t.id}]" for t in takes]
    return f"Takes ({len(bits)}): " + "; ".join(bits)


def resolve_take(text, session, user, project_id):
    takes = list_takes(project_id, session, user)
    if not takes:
        return "No takes yet - attach audio in the studio, then ask me to cue it."
    m = re.search(r"@take:([A-Za-z0-9_-]+)", text)
    if m:
        for t in takes:
            if t.id == m.group(1):
                return t
        return f"Unknown take {m.group(1)}."
    if any(k in text for k in ("latest", "last", "newest", "recent")):
        return takes[-1]
    lowered = text.lower()
    for t in takes:
        if t.name and t.name.lower() in lowered:
            return t
    if len(takes) == 1:
        return takes[0]
    choices = "; ".join(f"'{t.name or 'Untitled'}'" for t in takes)
    return f"Which take? Say @take:..., 'latest', or a name ({choices})."


def tool_add_cue(session, user, project_id, take_id, scene=None, start_sec=0.0, offset_sec=0.0, end_sec=None, lane=0, volume=1.0):
    take = session.get(AudioTakeRow, take_id)
    if take is None or take.project_id != project_id:
        return f"Unknown take {take_id} in this project."
    try:
        start = min(max(float(start_sec), 0.0), 3600.0)
    except (TypeError, ValueError):
        start = 0.0
    target = _resolve_scene(session, user, project_id, scene)
    cue = add_cue(target.id, CueCreate(audio_id=take.id, start_sec=start, offset_sec=offset_sec or 0.0, end_sec=end_sec, lane=lane or 0, volume=volume or 1.0), session, user)
    return f"Cued '{take.name or 'take'}' at {start:g}s in '{target.name}' [cue {cue.id}]."


def tool_transcribe_audio(
    session,
    user,
    project_id,
    take=None,
    scene=None,
    mode="ENDPOINTING",
    save_subtitles=False,
):
    """Transcribe a voice take with Muse Voice Transcribe.

    take is a take id, @take: mention, name, or 'latest'. Returns the
    transcript with per-turn timings; with save_subtitles the turns are
    also written to the scene subtitle track (same SRT path as captions).
    """
    takes = list_takes(project_id, session, user)
    if not takes:
        return "No takes yet - attach audio in the studio, then ask me to transcribe it."
    row = None
    if take:
        for t in takes:
            if t.id == str(take):
                row = session.get(AudioTakeRow, t.id)
                break
    if row is None:
        resolved = resolve_take(str(take or "latest"), session, user, project_id)
        if isinstance(resolved, str):
            return resolved
        row = session.get(AudioTakeRow, resolved.id)
    if row is None or row.project_id != project_id:
        return f"Unknown take {take} in this project."
    try:
        audio = storage.resolve(row.file_path).read_bytes()
    except OSError as exc:
        return f"Could not read take audio: {exc}"
    try:
        res = MetaProvider().transcribe_audio(
            audio,
            row.file_path.replace("\\", "/").split("/")[-1] or "take",
            mode=(mode or "ENDPOINTING").upper(),
        )
    except ProviderError as exc:
        return f"Transcription failed: {exc}"
    record_usage(
        session, user.id, "transcribe", settings.meta_transcribe_model, len(res.get("transcript") or "") // 4
    )
    transcript = (res.get("transcript") or "").strip()
    turns = [t for t in (res.get("turns") or []) if str(t.get("transcript") or "").strip()]
    if not turns and transcript:
        dur = max(float(row.duration_sec or 0.0), 0.1)
        turns = [{"transcript": transcript, "startMs": 0, "endMs": int(dur * 1000)}]
    if not turns:
        return f"No speech detected in '{row.name or 'take'}'."
    segs = []
    for t in turns:
        start = (t.get("startMs") or 0) / 1000
        end = (t.get("endMs") or 0) / 1000
        if end <= start:
            end = start + 2.0
        segs.append(
            {"text": str(t.get("transcript") or "").strip(), "start_sec": start, "end_sec": end}
        )
    head = transcript[:400] + ("..." if len(transcript) > 400 else "")
    if save_subtitles:
        target = _resolve_scene(session, user, project_id, scene)
        norm = normalize_caption_segments(segs)
        if isinstance(norm, str):
            return norm
        save_subtitles_track(session, user, target.id, segments_to_srt(norm).encode())
        return (
            f"Transcribed '{row.name or 'take'}' ({len(segs)} turns) and saved "
            f"as subtitles on '{target.name}': {head}"
        )
    return f"Transcript of '{row.name or 'take'}' ({len(segs)} turns): {head}"


def tool_frame_from_media(session, user, project_id, source, name=None, sequence=None):
    """Draw frame from a scan page, panel, generation, or frame."""
    try:
        schema = frame_from_media_source(
            session, user, project_id, source, name, sequence
        )
    except HTTPException as exc:
        return str(exc.detail)
    return f"Created frame '{schema.name}' [frame {schema.id}]."


# ------------------------------------------------- draw-studio control ---


def _project_frames(session, user, project_id):
    """Owned frames in flipbook order (ownership or 404)."""
    get_project(project_id, session, user)
    return (
        session.execute(
            select(FrameRow)
            .where(FrameRow.project_id == project_id)
            .order_by(FrameRow.index)
        )
        .scalars()
        .all()
    )


def _resolve_frame(session, user, project_id, frame, sequence=None):
    """FrameRow by @frame: mention, id, or name (single-frame shortcut)."""
    frames = _project_frames(session, user, project_id)
    if not frames:
        return "No draw frames yet - create one first ('new frame')."
    if sequence:
        try:
            seq = _resolve_sequence(session, user, project_id, sequence)
        except HTTPException as exc:
            return str(exc.detail)
        frames = [f for f in frames if f.sequence_id == seq.id]
        if not frames:
            return f"Sequence '{seq.name}' has no frames yet."
    ref = str(frame or "").strip()
    m = re.search(r"@frame:([A-Za-z0-9_-]+)", ref)
    if m:
        ref = m.group(1)
    for f in frames:
        if f.id == ref:
            return f
    lowered = ref.lower()
    for f in frames:
        if (f.name or "").lower() == lowered:
            return f
    if len(frames) == 1:
        return frames[0]
    cands = sorted(
        (
            f for f in frames
            if f.name and len(f.name) > 2 and f.name.lower() in ref.lower()
        ),
        key=lambda f: len(f.name),
        reverse=True,
    )
    if cands:
        return cands[0]
    names = "; ".join(f"'{f.name or 'Untitled'}' [frame {f.id}]" for f in frames)
    return f"Unknown frame '{frame}'. Frames: {names}."


def _layer_desc(layer) -> str:
    flags = []
    if not layer.visible:
        flags.append("hidden")
    if layer.locked:
        flags.append("locked")
    extra = f" ({', '.join(flags)})" if flags else ""
    return f"'{layer.name}' [{layer.id}] {layer.opacity}% {layer.blend_mode}{extra}"


def _layer_list(frame) -> str:
    layers = sorted(frame.layers, key=lambda l: l.layer_order)
    if not layers:
        return "no layers"
    return "; ".join(_layer_desc(l) for l in layers)


def _resolve_layer(frame, layer):
    """LayerRow by id or name, or an error string naming the layers."""
    layers = sorted(frame.layers, key=lambda l: l.layer_order)
    if not layers:
        return "That frame has no layers yet."
    ref = str(layer or "").strip()
    for l in layers:
        if l.id == ref:
            return l
    lowered = ref.lower()
    for l in layers:
        if (l.name or "").lower() == lowered:
            return l
    names = "; ".join(f"'{l.name}' [{l.id}]" for l in layers)
    return f"Unknown layer '{layer}'. Layers: {names}."


def _parse_opacity(value):
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return f"Opacity must be 0..100, got {value}."
    if not 0 <= v <= 100:
        return f"Opacity must be 0..100, got {value}."
    return v


def _parse_blend(value):
    """(blend, error): error is None on success (names are strings too)."""
    b = str(value or "normal").strip().lower().replace("_", "-")
    if b not in BLEND_MODES:
        return None, f"Unknown blend mode '{value}'. Valid: {', '.join(BLEND_MODES)}."
    return b, None


def _png_data_url(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _blank_png_data_url(width, height) -> str:
    return _png_data_url(
        Image.new("RGBA", (max(8, width), max(8, height)), (0, 0, 0, 0))
    )


def _layer_rgba(layer, width, height):
    """Layer pixels as RGBA at frame size (transparent when empty)."""
    if layer.data_url and "," in layer.data_url:
        try:
            raw = base64.b64decode(layer.data_url.split(",", 1)[1])
            img = Image.open(io.BytesIO(raw)).convert("RGBA")
            if img.size != (width, height):
                img = img.resize((width, height))
            return img
        except Exception:
            logger.debug("unreadable layer pixels", exc_info=True)
    return Image.new("RGBA", (width, height), (0, 0, 0, 0))


def tool_list_frames(session, user, project_id, sequence=None) -> str:
    # Fresh reads: this session may have cached relations from an
    # earlier tool call in the same turn.
    session.expire_all()
    frames = _project_frames(session, user, project_id)
    if not frames:
        return "No draw frames yet - create one first ('new frame')."
    label = "Draw frames"
    if sequence:
        try:
            seq = _resolve_sequence(session, user, project_id, sequence)
        except HTTPException as exc:
            return str(exc.detail)
        frames = [f for f in frames if f.sequence_id == seq.id]
        if not frames:
            return f"Sequence '{seq.name}' has no frames yet."
        label = f"Frames in '{seq.name}'"
    lines = [
        f"'{f.name or 'Untitled'}' [frame {f.id}] {f.width}x{f.height}, "
        f"{len(f.layers)} layers: {_layer_list(f)}"
        for f in frames
    ]
    return f"{label} ({len(frames)}): " + " | ".join(lines)


_DRAW_COLORS = {
    "black": (0, 0, 0), "white": (255, 255, 255), "ink": (26, 26, 28),
    "paper": (244, 239, 228), "red": (214, 40, 40), "green": (46, 125, 50),
    "blue": (30, 100, 200), "yellow": (235, 200, 40), "orange": (230, 126, 34),
    "purple": (123, 44, 145), "pink": (232, 121, 166), "brown": (121, 72, 40),
    "gray": (128, 128, 128), "grey": (128, 128, 128), "cyan": (0, 180, 180),
    "magenta": (200, 40, 160), "lime": (120, 200, 40),
}


def _parse_draw_color(value):
    """((r, g, b), error): color names or #rgb/#rrggbb, opaque always."""
    s = str(value or "").strip().lower()
    if s in _DRAW_COLORS:
        return _DRAW_COLORS[s], None
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", s)
    if not m:
        return None, (
            f"unknown color '{value}' - name one "
            f"({', '.join(sorted(_DRAW_COLORS))}) or #rrggbb"
        )
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)), None


def tool_manage_layers(
    session, user, project_id, frame, op="list", layer=None, name=None,
    opacity=None, visible=None, locked=None, blend=None, position=None,
    sequence=None, shapes=None,
) -> str:
    """Draw-layer ops: list/add/rename/set/move/remove/duplicate/clear/merge/draw."""
    # Fresh reads: this session may have cached relations from an
    # earlier tool call in the same turn.
    session.expire_all()
    f = _resolve_frame(session, user, project_id, frame, sequence)
    if isinstance(f, str):
        return f
    op = str(op or "list").strip().lower()
    if op == "list":
        return f"Layers on '{f.name or 'Untitled'}': {_layer_list(f)}."
    if op in ("add", "new"):
        if len(f.layers) >= MAX_LAYERS:
            return f"Too many layers ({len(f.layers)}), remove one first."
        label = str(name or "").strip() or f"Layer {len(f.layers) + 1}"
        row = LayerRow(
            frame_id=f.id,
            name=label[:200],
            opacity=100,
            visible=True,
            locked=False,
            blend_mode="normal",
            layer_order=max([l.layer_order for l in f.layers], default=-1) + 1,
            data_url=_blank_png_data_url(f.width, f.height),
        )
        session.add(row)
        session.commit()
        return f"Added layer '{row.name}' [{row.id}] to '{f.name or 'Untitled'}'."
    if op == "draw" and not f.layers:
        target = LayerRow(
            frame_id=f.id,
            name="Layer 1",
            opacity=100,
            visible=True,
            locked=False,
            blend_mode="normal",
            layer_order=0,
            data_url=_blank_png_data_url(f.width, f.height),
        )
        session.add(target)
        session.flush()
    elif op == "draw" and not str(layer or "").strip():
        target = sorted(f.layers, key=lambda l: l.layer_order)[-1]
    else:
        target = _resolve_layer(f, layer)
        if isinstance(target, str):
            return target
    if op == "rename":
        if not str(name or "").strip():
            return "Rename needs a name."
        target.name = str(name).strip()[:200]
        session.commit()
        return f"Renamed layer to '{target.name}'."
    if op == "set":
        changed = []
        if opacity is not None:
            v = _parse_opacity(opacity)
            if isinstance(v, str):
                return v
            target.opacity = v
            changed.append(f"opacity {v}%")
        if visible is not None:
            target.visible = bool(visible)
            changed.append("visible" if target.visible else "hidden")
        if locked is not None:
            target.locked = bool(locked)
            changed.append("locked" if target.locked else "unlocked")
        if blend is not None:
            b, blend_err = _parse_blend(blend)
            if blend_err:
                return blend_err
            target.blend_mode = b
            changed.append(f"blend {b}")
        if not changed:
            return "Set needs opacity, visible, locked, or blend."
        session.commit()
        return f"Layer '{target.name}': {', '.join(changed)}."
    if op == "move":
        try:
            pos = int(position)
        except (TypeError, ValueError):
            return f"Move needs a position number, got {position}."
        layers = sorted(f.layers, key=lambda l: l.layer_order)
        layers = [l for l in layers if l.id != target.id]
        pos = min(max(pos, 0), len(layers))
        layers.insert(pos, target)
        for i, l in enumerate(layers):
            l.layer_order = i
        session.commit()
        return f"Moved '{target.name}' to position {pos} (0 is bottom)."
    if op in ("remove", "delete"):
        if target.locked:
            return f"Layer '{target.name}' is locked - unlock it first."
        session.delete(target)
        session.commit()
        return f"Removed layer '{target.name}'."
    if op in ("duplicate", "copy"):
        if len(f.layers) >= MAX_LAYERS:
            return f"Too many layers ({len(f.layers)}), remove one first."
        row = LayerRow(
            frame_id=f.id,
            name=f"{target.name} copy"[:200],
            opacity=target.opacity,
            visible=target.visible,
            locked=False,
            blend_mode=target.blend_mode,
            layer_order=max([l.layer_order for l in f.layers]) + 1,
            data_url=target.data_url,
        )
        session.add(row)
        session.commit()
        return f"Duplicated '{target.name}' as '{row.name}' [{row.id}]."
    if op == "clear":
        if target.locked:
            return f"Layer '{target.name}' is locked - unlock it first."
        target.data_url = None
        session.commit()
        return f"Cleared the pixels on '{target.name}'."
    if op == "merge":
        if target.locked:
            return f"Layer '{target.name}' is locked - unlock it first."
        layers = sorted(f.layers, key=lambda l: l.layer_order)
        idx = next(i for i, l in enumerate(layers) if l.id == target.id)
        if idx == 0:
            return f"'{target.name}' is already the bottom layer - nothing below to merge into."
        below = layers[idx - 1]
        if below.locked:
            return f"Layer '{below.name}' is locked - unlock it first."
        merged = _layer_rgba(below, f.width, f.height)
        merged.alpha_composite(_layer_rgba(target, f.width, f.height))
        below.data_url = _png_data_url(merged)
        session.delete(target)
        session.commit()
        return f"Merged '{target.name}' down into '{below.name}'."
    if op == "draw":
        if target.locked:
            return f"Layer '{target.name}' is locked - unlock it first."
        if not isinstance(shapes, list) or not shapes:
            return (
                "Draw needs shapes: a list of {type, color, ...} "
                "(stroke, rect, ellipse, fill)."
            )
        if len(shapes) > 100:
            return "Draw takes at most 100 shapes per call."
        W, H = int(f.width), int(f.height)
        jobs = []
        for i, shape in enumerate(shapes):
            if not isinstance(shape, dict):
                return f"Shape {i} must be an object."
            kind = str(shape.get("type") or "").strip().lower()
            color, color_err = _parse_draw_color(shape.get("color") or "ink")
            if color_err:
                return f"Shape {i}: {color_err}."
            rgba = color + (255,)
            if kind == "stroke":
                pts = shape.get("points")
                if not isinstance(pts, list) or len(pts) < 2:
                    return f"Shape {i}: a stroke needs 2+ [x, y] points."
                if len(pts) > 500:
                    return f"Shape {i}: a stroke holds at most 500 points."
                xy = []
                for pt in pts:
                    if (
                        not isinstance(pt, (list, tuple)) or len(pt) != 2
                        or not all(isinstance(v, (int, float)) for v in pt)
                    ):
                        return f"Shape {i}: every point must be [x, y] numbers."
                    xy.append((min(max(pt[0], 0), W), min(max(pt[1], 0), H)))
                try:
                    width = min(max(int(float(shape.get("width", 4))), 1), 64)
                except (TypeError, ValueError):
                    return f"Shape {i}: width must be 1..64."
                jobs.append(("stroke", rgba, width, xy))
            elif kind in ("rect", "ellipse"):
                box = shape.get("box")
                if (
                    not isinstance(box, (list, tuple)) or len(box) != 4
                    or not all(isinstance(v, (int, float)) for v in box)
                    or box[2] < 1 or box[3] < 1
                ):
                    return f"Shape {i}: {kind} needs box [x, y, w, h] with w, h >= 1."
                x, y = min(max(box[0], 0), W), min(max(box[1], 0), H)
                w, h = min(box[2], W), min(box[3], H)
                fill = bool(shape.get("fill", False))
                try:
                    width = min(max(int(float(shape.get("width", 3))), 1), 64)
                except (TypeError, ValueError):
                    return f"Shape {i}: width must be 1..64."
                jobs.append((kind, rgba, width, fill, (x, y, x + w, y + h)))
            elif kind == "fill":
                jobs.append(("fill", rgba))
            else:
                return (
                    f"Shape {i}: unknown type '{shape.get('type')}'. "
                    "Valid: stroke, rect, ellipse, fill."
                )
        img = _layer_rgba(target, W, H)
        d = ImageDraw.Draw(img)
        for job in jobs:
            if job[0] == "stroke":
                _, rgba, width, xy = job
                d.line(xy, fill=rgba, width=width, joint="curve")
            elif job[0] == "fill":
                d.rectangle([0, 0, W, H], fill=job[1])
            else:
                _, rgba, width, fill, box = job
                if job[0] == "rect":
                    if fill:
                        d.rectangle(box, fill=rgba)
                    else:
                        d.rectangle(box, outline=rgba, width=width)
                elif fill:
                    d.ellipse(box, fill=rgba)
                else:
                    d.ellipse(box, outline=rgba, width=width)
        target.data_url = _png_data_url(img)
        session.commit()
        return (
            f"Drew {len(jobs)} shape(s) on layer '{target.name}' [{target.id}] "
            f"of frame '{f.name or 'Untitled'}' [frame {f.id}]."
        )
    return (
        f"Unknown layer op '{op}'. Valid: list, add, rename, set, move, "
        "remove, duplicate, clear, merge, draw."
    )


def _tone_png(width, height, density, angle_deg, rgb) -> bytes:
    """Manga screentone: rotated dot grid, coverage ~ density."""
    cell = 14
    radius = max(1.0, cell * math.sqrt(max(0.0, min(1.0, density))) * 0.62)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    reach = math.hypot(width, height) / 2 + cell * 2
    cx, cy = width / 2, height / 2
    n = int(reach / cell) + 1
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            x = cx + i * cell * cos_a - j * cell * sin_a
            y = cy + i * cell * sin_a + j * cell * cos_a
            d.ellipse(
                [x - radius, y - radius, x + radius, y + radius],
                fill=rgb + (255,),
            )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _parse_hex_color(value, default=(17, 17, 17)):
    s = str(value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            pass
    return default


def tool_tone_layer(
    session, user, project_id, frame, density=0.2, angle=20.0,
    color="#111111", opacity=100, blend="multiply", name=None,
    sequence=None,
) -> str:
    """Add a manga screentone dot layer to a draw frame."""
    # Fresh reads: this session may have cached relations from an
    # earlier tool call in the same turn.
    session.expire_all()
    f = _resolve_frame(session, user, project_id, frame, sequence)
    if isinstance(f, str):
        return f
    if len(f.layers) >= MAX_LAYERS:
        return f"Too many layers ({len(f.layers)}), remove one first."
    try:
        dens = min(max(float(density), 0.02), 1.0)
    except (TypeError, ValueError):
        return f"Density must be 0..1, got {density}."
    try:
        ang = float(angle)
    except (TypeError, ValueError):
        return f"Angle must be degrees, got {angle}."
    v = _parse_opacity(opacity)
    if isinstance(v, str):
        return v
    b, blend_err = _parse_blend(blend)
    if blend_err:
        return blend_err
    png = _tone_png(f.width, f.height, dens, ang, _parse_hex_color(color))
    label = str(name or "").strip() or f"Tone {round(dens * 100)}%"
    row = LayerRow(
        frame_id=f.id,
        name=label[:200],
        opacity=v,
        visible=True,
        locked=False,
        blend_mode=b,
        layer_order=max([l.layer_order for l in f.layers], default=-1) + 1,
        data_url="data:image/png;base64," + base64.b64encode(png).decode(),
    )
    session.add(row)
    session.commit()
    return (
        f"Added screentone layer '{row.name}' [{row.id}] to "
        f"'{f.name or 'Untitled'}' ({round(dens * 100)}% dots at {ang:g}, {b})."
    )


def tool_layer_from_media(
    session, user, project_id, frame, source, name=None,
    opacity=100, blend="normal", sequence=None,
) -> str:
    # Fresh reads: this session may have cached relations from an
    # earlier tool call in the same turn.
    session.expire_all()
    """Drop a scan page, panel, generation, or frame onto a frame as a layer."""
    f = _resolve_frame(session, user, project_id, frame, sequence)
    if isinstance(f, str):
        return f
    if len(f.layers) >= MAX_LAYERS:
        return f"Too many layers ({len(f.layers)}), remove one first."
    try:
        data, label = load_media_image(session, user, project_id, source)
    except HTTPException as exc:
        return str(exc.detail)
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return f"Could not decode {label}."
    canvas = Image.new("RGBA", (f.width, f.height), (0, 0, 0, 0))
    scale = min(f.width / max(1, img.width), f.height / max(1, img.height), 1.0)
    nw, nh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    fitted = img.resize((nw, nh)) if scale < 1.0 else img
    canvas.paste(fitted, ((f.width - nw) // 2, (f.height - nh) // 2), fitted)
    v = _parse_opacity(opacity)
    if isinstance(v, str):
        return v
    b, blend_err = _parse_blend(blend)
    if blend_err:
        return blend_err
    row = LayerRow(
        frame_id=f.id,
        name=(str(name or "").strip() or label)[:200],
        opacity=v,
        visible=True,
        locked=False,
        blend_mode=b,
        layer_order=max([l.layer_order for l in f.layers], default=-1) + 1,
        data_url=_png_data_url(canvas),
    )
    session.add(row)
    session.commit()
    return (
        f"Added {label} as layer '{row.name}' [{row.id}] "
        f"on '{f.name or 'Untitled'}'."
    )


def tool_manage_frames(
    session, user, project_id, op, frame=None, name=None, frame_ids=None,
    sequence=None, target=None,
) -> str:
    # Fresh reads: this session may have cached relations from an
    # earlier tool call in the same turn.
    session.expire_all()
    """Flipbook ops: list, duplicate, delete, rename, reorder, move draw frames."""
    op = str(op or "list").strip().lower()
    if op == "list":
        return tool_list_frames(session, user, project_id, sequence)
    if op == "reorder":
        raw = (
            list(frame_ids)
            if isinstance(frame_ids, list)
            else re.findall(r"\[frame ([A-Za-z0-9_-]+)\]", str(frame_ids or frame or ""))
        )
        frames = _project_frames(session, user, project_id)
        by_id = {f.id: f for f in frames}
        by_name = {(f.name or "").lower(): f for f in frames}
        ordered = []
        for item in raw:
            token = str(item).strip()
            m = re.search(r"\[frame ([A-Za-z0-9_-]+)\]", token)
            hit = by_id.get(m.group(1) if m else token) or by_name.get(
                (m.group(1) if m else token).lower()
            )
            if hit is None:
                return f"Unknown frame '{token}'."
            ordered.append(hit)
        if len(ordered) != len(frames) or {f.id for f in ordered} != set(by_id):
            names = "; ".join(
                f"'{f.name or 'Untitled'}' [frame {f.id}]" for f in frames
            )
            return f"Reorder needs every frame exactly once. Frames: {names}."
        order_frames(
            project_id, FrameOrder(frame_ids=[f.id for f in ordered]), session, user
        )
        return "Reordered frames: " + ", ".join(
            f"'{f.name or 'Untitled'}'" for f in ordered
        ) + "."
    f = _resolve_frame(session, user, project_id, frame, sequence)
    if isinstance(f, str):
        return f
    if op == "move":
        if not target:
            return "Move needs a target sequence ('move it to Sequence 2')."
        try:
            dest = _resolve_sequence(session, user, project_id, target)
        except HTTPException as exc:
            return str(exc.detail)
        if f.sequence_id == dest.id:
            return f"'{f.name or 'Untitled'}' is already in '{dest.name}'."
        f.sequence_id = dest.id
        f.index = _next_frame_index(session, dest.id)
        session.commit()
        return f"Moved '{f.name or 'Untitled'}' to sequence '{dest.name}'."
    if op == "duplicate":
        copy = duplicate_frame(f.id, session, user)
        return f"Duplicated '{f.name or 'Untitled'}' as '{copy.name}' [frame {copy.id}]."
    if op in ("delete", "remove"):
        try:
            delete_frame(f.id, session, user)
        except HTTPException as exc:
            return str(exc.detail)
        return f"Deleted frame '{f.name or 'Untitled'}'."
    if op == "rename":
        if not str(name or "").strip():
            return "Rename needs a name."
        f.name = str(name).strip()[:200]
        session.commit()
        return f"Renamed frame to '{f.name}'."
    return "Unknown frame op '{op}'. Valid: list, duplicate, delete, rename, reorder.".format(op=op)


def tool_manage_clips(
    session, user, project_id, op, clip=None, scene=None,
    duration=None, motion=None, clip_ids=None,
    transition=None, transition_duration=None, fade_in=None, fade_out=None,
) -> str:
    # Fresh reads: this session may have cached relations from an
    # earlier tool call in the same turn.
    session.expire_all()
    """Timeline-clip ops: list, remove, set (duration/motion), reorder."""
    op = str(op or "list").strip().lower()
    if op == "list":
        scenes = sorted(list_scenes(project_id, session, user), key=lambda s: s.order)
        if not scenes:
            return "No scenes yet - auto-direct or add a clip first."
        parts = []
        for s in scenes:
            clips = sorted(s.clips, key=lambda c: c.order)
            desc = (
                "; ".join(
                    f"[clip {c.id}] {c.kind} {c.source_id} {c.duration:g}s {c.motion}"
                    for c in clips
                )
                if clips
                else "no clips"
            )
            parts.append(f"'{s.name}' [scene {s.id}]: {desc}")
        return "Timeline: " + " | ".join(parts)
    if op == "reorder":
        try:
            target = _resolve_scene(session, user, project_id, scene)
        except HTTPException as exc:
            return str(exc.detail)
        raw = (
            list(clip_ids)
            if isinstance(clip_ids, list)
            else re.findall(r"\[clip ([A-Za-z0-9_-]+)\]", str(clip_ids or clip or ""))
        )
        ids = []
        for item in raw:
            m = re.search(r"\[clip ([A-Za-z0-9_-]+)\]", str(item))
            ids.append(m.group(1) if m else str(item).strip())
        try:
            order_clips(target.id, ClipOrder(clip_ids=ids), session, user)
        except HTTPException as exc:
            return str(exc.detail)
        return f"Reordered {len(ids)} clips on '{target.name}'."
    cid = str(clip or "").strip()
    m = re.search(r"\[clip ([A-Za-z0-9_-]+)\]", cid)
    if m:
        cid = m.group(1)
    else:
        m2 = re.search(r"\bclip\s+([A-Za-z0-9_-]+)", cid)
        if m2:
            cid = m2.group(1)
    if op in ("remove", "delete"):
        if not cid:
            return "Remove needs a clip id - ask me to list the timeline first."
        try:
            delete_clip(cid, session, user)
        except HTTPException as exc:
            return str(exc.detail)
        return f"Removed clip {cid}."
    if op == "set":
        if not cid:
            return "Set needs a clip id."
        body = {}
        if duration is not None:
            try:
                body["duration"] = min(max(float(duration), 0.1), 30.0)
            except (TypeError, ValueError):
                return f"Duration must be seconds, got {duration}."
        if motion is not None:
            if str(motion) not in MOTIONS:
                return f"Unknown motion '{motion}'. Valid: {', '.join(MOTIONS)}."
            body["motion"] = str(motion)
        if transition is not None:
            if str(transition) not in TRANSITIONS:
                return f"Unknown transition '{transition}'. Valid: {', '.join(TRANSITIONS)}."
            body["transition"] = str(transition)
        if transition_duration is not None:
            try:
                body["transition_duration"] = min(max(float(transition_duration), 0.1), 2.0)
            except (TypeError, ValueError):
                return f"Transition seconds, got {transition_duration}."
        for key, val in (("fade_in", fade_in), ("fade_out", fade_out)):
            if val is not None:
                try:
                    body[key] = min(max(float(val), 0.0), 10.0)
                except (TypeError, ValueError):
                    return f"Fade seconds, got {val}."
        if not body:
            return "Set needs duration, motion, transition, or fades."
        try:
            updated = patch_clip(cid, ClipPatch(**body), session, user)
        except HTTPException as exc:
            return str(exc.detail)
        return f"Clip {cid}: {updated.duration:g}s, {updated.motion}."
    return (
        f"Unknown clip op '{op}'. Valid: list, remove, set, reorder."
    )


def tool_manage_sequences(
    session, user, project_id, op="list", sequence=None, name=None,
) -> str:
    """Animation-shot ops: list, add, rename, duplicate, delete sequences."""
    # Fresh reads: this session may have cached relations from an
    # earlier tool call in the same turn.
    session.expire_all()
    op = str(op or "list").strip().lower()
    if op == "list":
        seqs = list_sequences(project_id, session, user)
        if not seqs:
            return "No sequences yet - create one ('new sequence')."
        lines = [
            f"'{s.name}' [sequence {s.id}] ({len(s.frames)} frames)"
            for s in seqs
        ]
        return f"Sequences ({len(seqs)}): " + " | ".join(lines)
    if op == "add":
        label = str(name or "").strip()
        if not label:
            label = f"Sequence {len(list_sequences(project_id, session, user)) + 1}"
        schema = create_sequence(project_id, SequenceCreate(name=label), session, user)
        return f"Created sequence '{schema.name}' [sequence {schema.id}]."
    try:
        seq = _resolve_sequence(session, user, project_id, sequence)
    except HTTPException as exc:
        return str(exc.detail)
    if op == "rename":
        if not str(name or "").strip():
            return "Rename needs a name."
        schema = patch_sequence(seq.id, SequencePatch(name=str(name).strip()), session, user)
        return f"Renamed sequence to '{schema.name}'."
    if op == "duplicate":
        schema = duplicate_sequence(seq.id, session, user)
        return (
            f"Duplicated '{seq.name}' as '{schema.name}' "
            f"[sequence {schema.id}] ({len(schema.frames)} frames)."
        )
    if op in ("delete", "remove"):
        try:
            delete_sequence(seq.id, session, user)
        except HTTPException as exc:
            return str(exc.detail)
        return f"Deleted sequence '{seq.name}'."
    return f"Unknown sequence op '{op}'. Valid: list, add, rename, duplicate, delete."


def tool_manage_timelines(
    session, user, project_id, op="list", scene=None, name=None,
) -> str:
    """Timeline ops: list, add, rename, duplicate, delete scenes."""
    # Fresh reads: this session may have cached relations from an
    # earlier tool call in the same turn.
    session.expire_all()
    op = str(op or "list").strip().lower()
    if op == "list":
        scenes = sorted(list_scenes(project_id, session, user), key=lambda s: s.order)
        if not scenes:
            return "No timelines yet - create one ('new timeline')."
        lines = [
            f"'{s.name}' [scene {s.id}] ({len(s.clips)} clips, {len(s.cues)} cues)"
            for s in scenes
        ]
        return f"Timelines ({len(scenes)}): " + " | ".join(lines)
    if op == "add":
        label = str(name or "").strip()
        if not label:
            label = f"Timeline {len(list_scenes(project_id, session, user)) + 1}"
        schema = create_scene(project_id, SceneCreate(name=label), session, user)
        return f"Created timeline '{schema.name}' [scene {schema.id}]."
    if not scene:
        return "Which timeline? " + tool_manage_timelines(
            session, user, project_id, "list"
        )
    try:
        target = _resolve_scene(session, user, project_id, scene)
    except HTTPException as exc:
        return str(exc.detail)
    if op == "rename":
        if not str(name or "").strip():
            return "Rename needs a name."
        schema = patch_scene(target.id, ScenePatch(name=str(name).strip()), session, user)
        return f"Renamed timeline to '{schema.name}'."
    if op == "duplicate":
        schema = duplicate_scene(target.id, session, user)
        return (
            f"Duplicated '{target.name}' as '{schema.name}' "
            f"[scene {schema.id}] ({len(schema.clips)} clips)."
        )
    if op in ("delete", "remove"):
        delete_scene(target.id, session, user)
        return f"Deleted timeline '{target.name}'."
    return f"Unknown timeline op '{op}'. Valid: list, add, rename, duplicate, delete."


def tool_video_to_frames(session, user, project_id, job, sequence=None, max_frames=12) -> str:
    """Explode a finished video into draw frames in a sequence."""
    # Fresh reads: this session may have cached relations from an
    # earlier tool call in the same turn.
    session.expire_all()
    try:
        n = min(max(int(max_frames or 12), 1), 24)
    except (TypeError, ValueError):
        return f"max_frames must be 1..24, got {max_frames}."
    try:
        frames = video_to_frames(
            str(job or ""),
            VideoToFrames(
                sequence_id=str(sequence) if sequence else None,
                max_frames=n,
            ),
            session,
            user,
        )
    except HTTPException as exc:
        return str(exc.detail)
    listed = "; ".join(f"'{f.name}' [frame {f.id}]" for f in frames)
    return f"Exploded the video into {len(frames)} draw frames: {listed}."


def tool_cancel_job(session, user, job) -> str:
    """Stop a queued/running job; queued work never runs, running work
    is discarded on completion."""
    session.expire_all()
    ref = str(job or "").strip().strip("[]")
    if ref.startswith("job "):
        ref = ref[4:]
    if not ref:
        return "Give me a job id ([job abc123]) to cancel."
    try:
        stopped = cancel_job(ref, session, user)
    except HTTPException as exc:
        return str(exc.detail)
    return f"Cancelled {stopped.kind} [job {stopped.id}] — it won't land in the library."


CREATE_PROJECT_PHRASES = (
    "create project",
    "create a project",
    "new project",
    "make a project",
    "make project",
)
CREATE_FRAME_PHRASES = (
    "create frame",
    "create a frame",
    "new frame",
    "add frame",
)
JOB_STATUS_WORDS = ("check", "status", "done", "ready", "finished", "finish", "progress")


def _wants_video(text: str) -> bool:
    """A render request ("generate a video of ...") vs an export ("export the video")."""
    return "video" in text and any(
        k in text for k in ("generate", "create", "make me", "draw me")
    )


def parse_project_name(message: str) -> str:
    """Project name from chat ("create project Shark Tale") or Untitled."""
    lowered = message.lower()
    for phrase in CREATE_PROJECT_PHRASES:
        if phrase in lowered:
            name = message[lowered.index(phrase) + len(phrase):].strip(' :"\'')
            return name[:80] or "Untitled"
    return "Untitled"


def parse_job_id(message: str) -> str | None:
    """A [job id] token from chat, or None when the user gave none."""
    m = re.search(r"\[job ([A-Za-z0-9_-]+)\]|job ([A-Za-z0-9_-]+)", message)
    if not m:
        return None
    return m.group(1) or m.group(2)


# ------------------------------------------------------------ DemoBrain ---


def _parse_quoted(text) -> list:
    return re.findall(r'"([^"]+)"', text) + re.findall(r"'([^']+)'", text)


def _demo_layer_name(message):
    quoted = _parse_quoted(message)
    if quoted:
        return quoted[0]
    m = re.search(r"(?:the\s+)?([A-Za-z0-9 _-]+?)\s+layer\b", message, re.IGNORECASE)
    if m:
        cand = re.sub(
            r"^(?:(?:hide|show|unhide|lock|unlock|delete|remove|move|rename|duplicate|copy|merge|clear|set|add|make|put|the|a|an)\s+)+",
            "",
            m.group(1).strip(),
            flags=re.IGNORECASE,
        ).strip()
        return cand or None
    return None


def _demo_layer_turn(session, user, project_id, message):
    """Demo-brain layer control: verbs map to manage_layers ops."""
    text = message.lower()
    name = _demo_layer_name(message)
    if "merge" in text:
        return tool_manage_layers(session, user, project_id, frame=message, op="merge", layer=name)
    if any(k in text for k in ("duplicate", "copy")):
        return tool_manage_layers(session, user, project_id, frame=message, op="duplicate", layer=name)
    if "rename" in text:
        quoted = _parse_quoted(message)
        old = name or (quoted[0] if quoted else None)
        new = quoted[-1] if quoted and quoted[-1] != old else None
        if not new:
            m = re.search(r"\bto\s+([A-Za-z0-9 _-]+?)\s*$", message, re.IGNORECASE)
            new = m.group(1).strip() if m else None
        return tool_manage_layers(session, user, project_id, frame=message, op="rename", layer=old, name=new)
    if any(k in text for k in ("delete", "remove", "erase")):
        return tool_manage_layers(session, user, project_id, frame=message, op="remove", layer=name)
    if "clear" in text:
        return tool_manage_layers(session, user, project_id, frame=message, op="clear", layer=name)
    if "hide" in text:
        return tool_manage_layers(session, user, project_id, frame=message, op="set", layer=name, visible=False)
    if "show" in text or "unhide" in text:
        return tool_manage_layers(session, user, project_id, frame=message, op="set", layer=name, visible=True)
    if "unlock" in text:
        return tool_manage_layers(session, user, project_id, frame=message, op="set", layer=name, locked=False)
    if "lock" in text:
        return tool_manage_layers(session, user, project_id, frame=message, op="set", layer=name, locked=True)
    if any(k in text for k in ("opacity", "transparent", "fade")) or re.search(r"\d\s*%", text):
        m = re.search(r"(\d+)\s*%", message)
        return tool_manage_layers(session, user, project_id, frame=message, op="set", layer=name, opacity=int(m.group(1)) if m else 100)
    if any(k in text for k in ("reorder", "move", "position", "backward", "forward", "front", "back", "bottom", "top")):
        m = re.search(r"(?:position|to)\s+(\d+)", text)
        if m:
            pos = int(m.group(1))
        elif "bottom" in text or "back" in text:
            pos = 0
        else:
            pos = 999
        return tool_manage_layers(session, user, project_id, frame=message, op="move", layer=name, position=pos)
    found = next((b for b in BLEND_MODES if b in text), None)
    if found:
        return tool_manage_layers(session, user, project_id, frame=message, op="set", layer=name, blend=found)
    if any(k in text for k in ("add", "new", "create")):
        quoted = _parse_quoted(message)
        return tool_manage_layers(session, user, project_id, frame=message, op="add", name=quoted[0] if quoted else None)
    return tool_manage_layers(session, user, project_id, frame=message, op="list")


def _demo_frame_turn(session, user, project_id, message):
    """Demo-brain flipbook control: duplicate/delete/rename/reorder/list."""
    text = message.lower()
    quoted = _parse_quoted(message)
    if any(k in text for k in ("duplicate", "copy")):
        return tool_manage_frames(session, user, project_id, "duplicate", message, sequence=_demo_seq_ref(message))
    if any(k in text for k in ("delete", "remove")):
        return tool_manage_frames(session, user, project_id, "delete", message, sequence=_demo_seq_ref(message))
    if "rename" in text:
        m = re.search(r"\bto\s+([A-Za-z0-9 _-]+?)\s*$", message, re.IGNORECASE)
        new = quoted[-1] if quoted else (m.group(1).strip() if m else None)
        return tool_manage_frames(session, user, project_id, "rename", message, new, sequence=_demo_seq_ref(message))
    if "reorder" in text or ("order" in text and "frame" in text):
        ids = re.findall(r"\[frame ([A-Za-z0-9_-]+)\]", message)
        if not ids:
            return ("Say the new order with frame ids, e.g. 'reorder frames: "
                    "[frame abc], [frame def]' (see 'list frames').")
        return tool_manage_frames(session, user, project_id, "reorder", frame_ids=ids, sequence=_demo_seq_ref(message))
    return tool_list_frames(session, user, project_id)


def _demo_clip_turn(session, user, project_id, message):
    """Demo-brain clip control: remove/set/reorder/list."""
    text = message.lower()
    if "reorder" in text or ("order" in text and "clip" in text):
        ids = re.findall(r"\[clip ([A-Za-z0-9_-]+)\]", message)
        if not ids:
            return ("Say the new clip order with ids, e.g. 'reorder clips: "
                    "[clip abc], [clip def]'.")
        return tool_manage_clips(session, user, project_id, "reorder",
                                 scene=_parse_scene_ref(message), clip_ids=ids)
    if any(k in text for k in ("remove", "delete")):
        m = re.search(r"\[clip ([A-Za-z0-9_-]+)\]", message)
        cid = m.group(1) if m else None
        if not cid:
            m2 = re.search(r"\bclip\s+([A-Za-z0-9_-]+)", text)
            cid = m2.group(1) if m2 and m2.group(1) != "clips" else None
        if not cid:
            return "Which clip? " + tool_manage_clips(session, user, project_id, "list")
        return tool_manage_clips(session, user, project_id, "remove", clip=cid)
    if any(k in text for k in ("list", "show", "what")):
        return tool_manage_clips(session, user, project_id, "list")
    secs = parse_seconds(message)
    motions = parse_motions(message)
    trans = _parse_transition(message)
    fi = _parse_fade(message, "in")
    fo = _parse_fade(message, "out")
    if secs is not None or motions or trans != "none" or fi is not None or fo is not None or any(
        k in text for k in ("slower", "faster", "longer", "shorter", "motion", "duration", "transition", "crossfade", "dissolve", "fade", "wipe")
    ):
        m = re.search(r"\[clip ([A-Za-z0-9_-]+)\]", message)
        if not m:
            return "Which clip? " + tool_manage_clips(session, user, project_id, "list")
        return tool_manage_clips(session, user, project_id, "set", clip=m.group(1),
                                 duration=secs, motion=(motions or [None])[0],
                                 transition=trans if trans != "none" else None, fade_in=fi, fade_out=fo)
    return tool_manage_clips(session, user, project_id, "list")


def _demo_seq_ref(message):
    quoted = _parse_quoted(message)
    if quoted:
        return quoted[0]
    m = re.search(
        r"(?:in|to|on|of|called)\s+(?:the\s+)?sequence\s+([A-Za-z0-9 _-]+?)(?:\s*$)",
        message,
        re.IGNORECASE,
    )
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = re.search(r"sequence\s+(\d+)", message, re.IGNORECASE)
    if m:
        return "Sequence " + m.group(1)
    return None


def _demo_sequence_turn(session, user, project_id, message):
    """Demo-brain sequence control: verbs map to manage_sequences ops."""
    text = message.lower()
    if any(k in text for k in ("duplicate", "copy")):
        return tool_manage_sequences(session, user, project_id, "duplicate", _demo_seq_ref(message))
    if any(k in text for k in ("delete", "remove")):
        return tool_manage_sequences(session, user, project_id, "delete", _demo_seq_ref(message))
    if "rename" in text:
        quoted = _parse_quoted(message)
        m = re.search(r"\bto\s+([A-Za-z0-9 _-]+?)\s*$", message, re.IGNORECASE)
        new = quoted[-1] if quoted else (m.group(1).strip() if m else None)
        return tool_manage_sequences(session, user, project_id, "rename", _demo_seq_ref(message), new)
    if any(k in text for k in ("new", "create", "add")):
        quoted = _parse_quoted(message)
        named = quoted[0] if quoted else None
        if not named:
            m = re.search(r"called\s+([A-Za-z0-9 _-]+?)(?:\s*$)", message, re.IGNORECASE)
            named = m.group(1).strip() if m else None
        return tool_manage_sequences(session, user, project_id, "add", None, named)
    return tool_manage_sequences(session, user, project_id, "list")


def _demo_scene_ref(message):
    quoted = _parse_quoted(message)
    if quoted:
        return quoted[0]
    m = re.search(
        r"(?:the\s+)?([A-Za-z0-9 _-]+?)\s+timeline\b", message, re.IGNORECASE
    )
    if m and m.group(1).strip():
        cand = m.group(1).strip()
        cand = re.sub(
            r"^(?:(?:duplicate|copy|delete|remove|rename|create|new|add|list|show|switch|open|the|a|an)\s+)+",
            "", cand, flags=re.IGNORECASE,
        ).strip()
        return cand or None
    return None


def _demo_video_turn(session, user, project_id, message):
    """Demo-brain video-to-frames: explode a finished video into a sequence."""
    text = message.lower()
    m = re.search(r"@gen:([A-Za-z0-9_-]+)", message)
    ref = m.group(1) if m else None
    if not ref:
        m2 = re.search(r"\bvideo\s+([A-Za-z0-9_-]+)", text)
        ref = m2.group(1) if m2 else None
    if not ref:
        return "Which video? Mention it with @gen: or give me its id (see Media)."
    row = session.get(JobRow, ref)
    if row is not None and row.kind != "video":
        return tool_frame_from_media(session, user, project_id, f"@gen:{ref}", None)
    m = re.search(r"(\d+)\s*frames?", text)
    n = int(m.group(1)) if m else 12
    return tool_video_to_frames(session, user, project_id, ref, _demo_seq_ref(message), n)


def _demo_timeline_turn(session, user, project_id, message):
    """Demo-brain timeline control: verbs map to manage_timelines ops."""
    text = message.lower()
    if any(k in text for k in ("duplicate", "copy")):
        return tool_manage_timelines(session, user, project_id, "duplicate", _demo_scene_ref(message))
    if any(k in text for k in ("delete", "remove")):
        return tool_manage_timelines(session, user, project_id, "delete", _demo_scene_ref(message))
    if "rename" in text:
        quoted = _parse_quoted(message)
        m = re.search(r"\bto\s+([A-Za-z0-9 _-]+?)\s*$", message, re.IGNORECASE)
        new = quoted[-1] if quoted else (m.group(1).strip() if m else None)
        return tool_manage_timelines(session, user, project_id, "rename", _demo_scene_ref(message), new)
    if any(k in text for k in ("new", "create", "add")):
        quoted = _parse_quoted(message)
        named = quoted[0] if quoted else None
        if not named:
            m = re.search(r"called\s+([A-Za-z0-9 _-]+?)(?:\s*$)", message, re.IGNORECASE)
            named = m.group(1).strip() if m else None
        return tool_manage_timelines(session, user, project_id, "add", None, named)
    if any(k in text for k in ("list", "show", "what", "switch", "open")):
        return tool_manage_timelines(session, user, project_id, "list") + (
            " Open the Render tab and pick it in Timelines — I can't switch your tab for you."
        )
    return tool_manage_timelines(session, user, project_id, "list")


_DRAW_SHAPE_WORDS = (
    "circle", "ellipse", "oval", "ring", "dot",
    "square", "rectangle", "box", "line", "stroke",
)


def _wants_draw_shape(text: str) -> bool:
    """A paint request for the draw canvas, not AI generation."""
    if "video" in text or "generat" in text:
        return False
    return (
        any(k in text for k in ("draw", "paint", "sketch"))
        and any(k in text for k in _DRAW_SHAPE_WORDS)
    )


def _demo_draw_turn(session, user, project_id, message):
    """Paint one centered shape on a draw layer (demo brain)."""
    text = message.lower()
    color = next(
        (c for c in _DRAW_COLORS if re.search(r"\b" + c + r"\b", text)), "ink"
    )
    f = _resolve_frame(session, user, project_id, message)
    if isinstance(f, str):
        return f
    W, H = int(f.width), int(f.height)
    scale = 0.36 if "big" in text else 0.15 if "small" in text else 0.25
    cx = W * 0.2 if "left" in text else W * 0.8 if "right" in text else W / 2
    cy = H * 0.2 if "top" in text else H * 0.8 if "bottom" in text else H / 2
    r = min(W, H) * scale
    if "dot" in text:
        shape = {
            "type": "ellipse", "color": color,
            "box": [cx - r / 3, cy - r / 3, r * 2 / 3, r * 2 / 3], "fill": True,
        }
    elif any(k in text for k in ("circle", "ellipse", "oval", "ring")):
        shape = {
            "type": "ellipse", "color": color,
            "box": [cx - r, cy - r, r * 2, r * 2], "fill": False, "width": 6,
        }
    elif any(k in text for k in ("square", "rectangle", "box")):
        side = r * 2
        shape = {
            "type": "rect", "color": color,
            "box": [cx - side / 2, cy - side / 2, side, side],
            "fill": False, "width": 6,
        }
    else:
        shape = {
            "type": "stroke", "color": color, "width": 6,
            "points": [[cx - r, cy - r], [cx + r, cy + r]],
        }
    return tool_manage_layers(
        session, user, project_id, frame=message, op="draw", shapes=[shape]
    )


def demo_turn(
    message: str,
    project_id: str,
    session: Session,
    user: UserRow,
    job_factory=None,
    clip_seconds: float = 2.0,
    ref_context: str = "",
    history: list[dict] | None = None,
    fallback_references=None,
) -> tuple[str, list[dict]]:
    text = message.lower().strip()
    actions: list[dict] = []
    # history: accepted for API parity and persistence; keyword intents
    # answer the latest message, while the dock shows the full thread.

    def run(tool: str, fn, *args, **kw):
        try:
            summary = fn(*args, **kw)
        except HTTPException as exc:
            summary = str(exc.detail)
        actions.append({"tool": tool, "summary": summary})
        return summary

    if any(p in text for p in CREATE_PROJECT_PHRASES):
        reply = run(
            "create_project",
            tool_create_project,
            session,
            user,
            parse_project_name(message),
        )
    elif any(k in text for k in ("cancel", "stop", "abort")) and "job" in text:
        reply = run("cancel_job", tool_cancel_job, session, user, parse_job_id(message))
    elif ("job" in text or "video" in text) and any(k in text for k in JOB_STATUS_WORDS):
        jid = parse_job_id(message)
        if not jid:
            resolved = resolve_video_job(message, session, user, project_id)
            if isinstance(resolved, tuple):
                reply = run("check_job", tool_check_job, session, user, resolved[1])
            else:
                pending = session.execute(select(JobRow.id).where(JobRow.user_id == user.id, JobRow.project_id == project_id, JobRow.kind == "video", JobRow.status != "done")).scalars().first()
                if pending:
                    reply = f"Job {pending} is still running - ask me again in a bit."
                else:
                    reply = resolved
        else:
            reply = run("check_job", tool_check_job, session, user, jid)
    elif _wants_draw_shape(text):
        reply = run("manage_layers", _demo_draw_turn, session, user, project_id, message)
    elif _wants_video(text):
        prompt = message.strip()
        for prefix in ("generate", "create", "draw me", "make me"):
            if text.startswith(prefix):
                prompt = message[len(prefix):].strip(" :")
                break
        reply = run(
            "create_video",
            tool_create_video,
            session,
            user,
            project_id,
            prompt,
            parse_seconds(message) or 4.0,
            None,
            job_factory,
            fallback_references=fallback_references,
        )
        if any(k in text for k in TIMELINE_WORDS):
            reply += " When it is done, say 'add the video to the timeline'."
    elif _wants_image_timeline(text):
        prompt = message.strip()
        for prefix in ("generate", "create image", "draw me", "make me", "redraw"):
            if text.startswith(prefix):
                prompt = message[len(prefix):].strip(" :")
                break
        prompt = _strip_timeline_ask(prompt)
        if not prompt:
            reply = 'Tell me what to generate and add - e.g. "generate a moonlit robot and add it to the timeline".'
        else:
            reply = run("create_image", tool_generate, session, user, project_id, prompt, job_factory, fallback_references=fallback_references, attach_frame=True)
            m = re.search(r"\[frame ([A-Za-z0-9_-]+)\]", actions[-1]["summary"])
            if not m:
                reply += " I could not place it on the timeline - say which frame to add."
            else:
                chained = run("add_clip", tool_add_clip, session, user, project_id, "frame", m.group(1), _parse_scene_ref(message), parse_seconds(message) or 2.0, (parse_motions(message) or ["none"])[0], _parse_transition(message), None,
                                                        _parse_fade(message, "in"), _parse_fade(message, "out"))
                reply = f"{reply} {chained}"
    elif any(k in text for k in ("detect", "find panel", "scan")):
        reply = run("detect_all", tool_detect_all, session, user, project_id, parse_detector(text), parse_direction(text))
    elif ("video" in text or "@gen:" in text) and any(k in text for k in ("frame", "frames", "draw", "explode", "rotoscope", "still", "stills")) and not any(k in text for k in ("timeline", "timelines", "clip", "clips", "scene")):
        reply = run("video_to_frames", _demo_video_turn, session, user, project_id, message)
    elif "video" in text and any(k in text for k in TIMELINE_WORDS) and any(k in text for k in ("add", "put", "place", "cut")):
        resolved = resolve_video_job(text, session, user, project_id)
        if isinstance(resolved, str):
            reply = resolved
        else:
            reply = run("add_clip", tool_add_clip, session, user, project_id, "video", resolved[1], _parse_scene_ref(message), parse_seconds(message) or 2.0, (parse_motions(message) or ["none"])[0], _parse_transition(message), None,
                                                        _parse_fade(message, "in"), _parse_fade(message, "out"))
    elif any(k in text for k in EXPORT_KEYWORDS):
        reply = run("export", tool_export, session, user, project_id, job_factory)
    elif any(k in text for k in TAKE_WORDS) and any(k in text for k in ("list", "show", "what", "have", "many")):
        reply = run("list_takes", tool_list_takes, session, user, project_id)
    
    elif any(k in text for k in ("transcribe", "transcription", "speech to text", "speech-to-text")):
        resolved = resolve_take(message, session, user, project_id)
        if isinstance(resolved, str):
            reply = resolved
        else:
            save = any(k in text for k in ("subtitle", "caption", "srt"))
            reply = run("transcribe_audio", tool_transcribe_audio, session, user, project_id, resolved.id, _parse_scene_ref(message), "ENDPOINTING", save)
    elif any(k in text for k in SUBTITLE_WORDS) and any(k in text for k in SUBTITLE_VERBS):
        parts = parse_caption_parts(message)
        if isinstance(parts, str):
            reply = parts
        else:
            reply = run("add_subtitles", tool_add_subtitles, session, user, project_id, parts, _parse_scene_ref(message))
    elif (
        (re.search(r"@(frame|panel|gen|media):", text) or re.search(r"\b(?:page|pg)\s*\d+", text) or any(p in text for p in CREATE_FRAME_PHRASES))
        and (any(k in text for k in TIMELINE_WORDS) or any(k in text for k in ("animate", "direct", "cut")))
    ):
        if any(p in text for p in CREATE_FRAME_PHRASES) and not re.search(r"@(frame|panel|gen|media):", text) and not re.search(r"\b(?:page|pg)\s*\d+", text):
            created = run("create_frame", tool_create_frame, session, user, project_id)
            m = re.search(r"\[frame ([A-Za-z0-9_-]+)\]", actions[-1]["summary"])
            if not m:
                reply = created
            else:
                reply = f"{created} " + run("add_clip", tool_add_clip, session, user, project_id, "frame", m.group(1), _parse_scene_ref(message), parse_seconds(message) or 2.0, (parse_motions(message) or ["none"])[0], _parse_transition(message), None,
                                                        _parse_fade(message, "in"), _parse_fade(message, "out"))
        else:
            resolved = resolve_clip_source(text, session, user, project_id)
            if isinstance(resolved, str):
                if "video" in text:
                    resolved = resolve_video_job(text, session, user, project_id)
                if isinstance(resolved, str):
                    reply = resolved
            else:
                kind, source_id = resolved
                reply = run("add_clip", tool_add_clip, session, user, project_id, kind, source_id, _parse_scene_ref(message), parse_seconds(message) or 2.0, (parse_motions(message) or ["none"])[0], _parse_transition(message), None,
                                                        _parse_fade(message, "in"), _parse_fade(message, "out"))
    elif (
        any(k in text for k in TAKE_WORDS)
        and any(k in text for k in CUE_VERBS + ("timeline", "scene", "second", "voiceover"))
        and "take a look" not in text
        and "retake" not in text
    ):
        resolved = resolve_take(text, session, user, project_id)
        if isinstance(resolved, str):
            reply = resolved
        else:
            reply = run("add_cue", tool_add_cue, session, user, project_id, resolved.id, _parse_scene_ref(message), parse_seconds(message) or 0.0)
    elif "sequence" in text and any(k in text for k in ("new", "create", "add", "list", "show", "duplicate", "copy", "delete", "remove", "rename")):
        reply = run("manage_sequences", _demo_sequence_turn, session, user, project_id, message)
    elif "timeline" in text and any(k in text for k in ("new", "create", "duplicate", "copy", "delete", "remove", "rename", "list", "show", "switch", "open")) and not any(k in text for k in ("video", "take", "clip", "frame", "panel", "caption", "subtitle", "export")):
        reply = run("manage_timelines", _demo_timeline_turn, session, user, project_id, message)
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
    elif any(k in text for k in ("generate", "create image", "draw me", "make me", "redraw")):
        prompt = message.strip()
        for prefix in ("generate", "create image", "draw me", "make me", "redraw"):
            if text.startswith(prefix):
                prompt = message[len(prefix):].strip(" :")
                break
        add_it = bool(re.search(r"\s+(?:and|then)\s+add\s+(it|this|that|them)\s*[.!,;:]*$", prompt, re.IGNORECASE))
        if add_it:
            prompt = re.sub(r"\s+(?:and|then)\s+add\s+(it|this|that|them)\s*[.!,;:]*$", "", prompt, flags=re.IGNORECASE).strip(" ,.:")
        if not prompt:
            return "Tell me what to generate — e.g. \"generate a moonlit robot\".", []
        reply = run(
            "create_image", tool_generate, session, user, project_id, prompt, job_factory, attach_frame=add_it,
            fallback_references=fallback_references,
        )
        if add_it:
            m = re.search(r"\[frame ([A-Za-z0-9_-]+)\]", actions[-1]["summary"])
            if m:
                chained = run("add_clip", tool_add_clip, session, user, project_id, "frame", m.group(1), _parse_scene_ref(message), parse_seconds(message) or 2.0, (parse_motions(message) or ["none"])[0], _parse_transition(message), None,
                                                        _parse_fade(message, "in"), _parse_fade(message, "out"))
                reply = f"{reply} {chained}"
            else:
                reply += " I could not place it on the timeline - say which frame to add."
    elif "screentone" in text or ("tone" in text and "frame" in text):
        m = re.search(r"(\d+)\s*%", text)
        density = min(max(int(m.group(1)), 2), 100) / 100 if m else 0.2
        reply = run("tone_layer", tool_tone_layer, session, user, project_id, message, density)
    elif "layer" in text:
        reply = run("manage_layers", _demo_layer_turn, session, user, project_id, message)
    elif (
        any(k in text for k in ("list frames", "show frames", "show me frames", "what frames", "how many frames", "my frames"))
        or (
            re.search(r"\bframes?\b", text)
            and not any(k in text for k in ("timeline", "clip", "clips"))
            and any(k in text for k in ("duplicate", "delete", "remove", "rename", "reorder", "order", "copy"))
        )
    ):
        reply = run("manage_frames", _demo_frame_turn, session, user, project_id, message)
    elif "clip" in text and any(k in text for k in ("remove", "delete", "reorder", "order", "list", "show", "slower", "faster", "longer", "shorter", "motion", "duration", "second")):
        reply = run("manage_clips", _demo_clip_turn, session, user, project_id, message)
    elif "frame" in text and (
        re.search(r"\b(?:page|pg)\s*\d+", text)
        or ("from" in text and any(k in text for k in ("page", "scan", "media", "generation", "gen", "photo", "picture")))
        or "duplicate" in text
        or (
            re.search(r"@(media|gen|frame|panel):", text)
            # Action verb outside the mention itself: a bare @frame
            # token (e.g. the injected current-frame context) is not
            # a request. Word-boundary + scrubbed token so hex ids
            # can never match a verb.
            and re.search(
                r"\b(?:duplicate|create|add|place|put|use|make|from|attach|import|load|open|show|frame)\b",
                re.sub(r"@(?:media|gen|frame|panel):[A-Za-z0-9_-]+", " ", text),
            )
        )
    ):
        reply = run("frame_from_media", tool_frame_from_media, session, user, project_id, message, None)
    elif any(p in text for p in CREATE_FRAME_PHRASES):
        reply = run("create_frame", tool_create_frame, session, user, project_id)
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
    "only report what tools return. Earlier turns in this conversation are "
    "included before the latest message - use them as context. "
    "Greetings, thanks, and other smalltalk get a words-only reply — "
    "call no tools unless the user asks for an action, a change, or "
    "information not already in context. Reference images attach only "
    "when the user asked to reuse a frame, panel, scan, generation, "
    "or entity — never assume one."
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
            "name": "create_project",
            "description": "Create a new project and switch to it.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_frame",
            "description": "Create a blank draw frame in the project, optionally in one sequence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "sequence": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_clip",
            "description": "Add one panel, frame, scan-page, or video clip to a scene timeline. Video source_id is a finished generation job id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "source_id": {"type": "string"},
                    "scene": {"type": "string"},
                    "duration": {"type": "number"},
                    "motion": {"type": "string"},
                    "transition": {"type": "string"},
                    "transition_duration": {"type": "number"},
                    "fade_in": {"type": "number"},
                    "fade_out": {"type": "number"},
                },
                "required": ["kind", "source_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_subtitles",
            "description": "Add caption segments to a scene subtitle track (burned into MP4).",
            "parameters": {
                "type": "object",
                "properties": {
                    "segments": {},
                    "scene": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_all",
            "description": "Detect panels on every page of the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "detector": {"type": "string"},
                    "direction": {"type": "string"},
                },
            },
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
            "description": "Render one timeline to MP4 (default: the only timeline with clips).",
            "parameters": {
                "type": "object",
                "properties": {"scene": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_image",
            "description": (
                "Generate an image from a prompt into the media gallery. "
                "Attach a reference only when the user asked to reuse one: "
                "pass their @frame:/@panel:/@media:/@gen: mentions as "
                "references, named entities as entity_ids - never paste "
                "@-tokens into the prompt, never invent references."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Clean scene text, no @-mentions."},
                    "model": {"type": "string"},
                    "style": {"type": "string"},
                    "negative_prompt": {"type": "string"},
                    "reference_frame_id": {"type": "string"},
                    "references": {"type": "array", "description": "[{kind, id}] for user-mentioned refs to reuse (frame|panel|media|gen). Omit otherwise."},
                    "entity_ids": {"type": "array", "description": "Named entity ids to keep on-model. Omit otherwise."},
                    "width": {"type": "number"},
                    "height": {"type": "number"},
                    "attach_frame": {"type": "boolean", "description": "True only when the user explicitly asks for a draw frame. Omit otherwise."},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_video",
            "description": (
                "Start an async video render from a prompt. The job runs "
                "in the background — use check_job to see when it is done. "
                "Attach a reference only when the user asked to reuse one, "
                "via references - never paste @-tokens into the prompt."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Clean scene text, no @-mentions."},
                    "seconds": {"type": "number"},
                    "model": {"type": "string"},
                    "references": {"type": "array", "description": "[{kind, id}] for user-mentioned refs to reuse. Omit otherwise."},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_takes",
            "description": "List the project's voice takes with ids and durations.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_cue",
            "description": "Place a voice take on a scene timeline at a start time. take_id comes from list_takes or @take: mentions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "take_id": {"type": "string"},
                    "scene": {"type": "string"},
                    "start_sec": {"type": "number"},
                    "end_sec": {"type": "number"},
                    "lane": {"type": "number"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transcribe_audio",
            "description": "Transcribe a voice take to text with Muse Voice Transcribe. take_id comes from list_takes or @take: mentions; set save_subtitles to also write the turns to the scene subtitle track.",
            "parameters": {
                "type": "object",
                "properties": {
                    "take_id": {"type": "string"},
                    "scene": {"type": "string"},
                    "mode": {"type": "string"},
                    "save_subtitles": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "frame_from_media",
            "description": "Create a draw frame from a scan page, detected panel, image generation, or frame (@media:/@gen:/@frame:/@panel: id, or 'page N').",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "name": {"type": "string"},
                    "sequence": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_job",
            "description": (
                "Report one job's status and result URL. Poll this after "
                "create_video until the job is done."
            ),
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_frames",
            "description": "List the project's draw frames with their layers, optionally one sequence.",
            "parameters": {
                "type": "object",
                "properties": {"sequence": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_layers",
            "description": (
                "Draw-layer ops on one frame (frame id, @frame: mention, or name). "
                "Ops: list, add, rename, set (opacity/visible/locked/blend), "
                "move (position 0 is bottom), remove, duplicate, clear, merge down, "
                "draw (paint shapes: stroke/rect/ellipse/fill in frame pixels; "
                "omitted layer means the top layer)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "frame": {"type": "string"},
                    "op": {"type": "string"},
                    "layer": {"type": "string"},
                    "name": {"type": "string"},
                    "opacity": {"type": "number"},
                    "visible": {"type": "boolean"},
                    "locked": {"type": "boolean"},
                    "blend": {"type": "string"},
                    "position": {"type": "number"},
                    "sequence": {"type": "string"},
                    "shapes": {
                        "type": "array",
                        "description": (
                            "draw op: up to 100 shapes, each "
                            "{type: stroke|rect|ellipse|fill, color (name or #rrggbb), "
                            "width 1..64, points [[x, y], ...] for stroke, "
                            "box [x, y, w, h] for rect/ellipse, fill true to paint solid}."
                        ),
                    },
                },
                "required": ["frame", "op"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tone_layer",
            "description": (
                "Add a manga screentone dot layer to a draw frame "
                "(density 0..1, angle in degrees)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "frame": {"type": "string"},
                    "density": {"type": "number"},
                    "angle": {"type": "number"},
                    "color": {"type": "string"},
                    "opacity": {"type": "number"},
                    "blend": {"type": "string"},
                    "name": {"type": "string"},
                    "sequence": {"type": "string"},
                },
                "required": ["frame"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "layer_from_media",
            "description": (
                "Drop a scan page, panel, generation, or frame onto a draw "
                "frame as a new layer. Source is an @media:/@gen:/@frame:/@panel: "
                "id or 'page N'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "frame": {"type": "string"},
                    "source": {"type": "string"},
                    "name": {"type": "string"},
                    "sequence": {"type": "string"},
                    "opacity": {"type": "number"},
                    "blend": {"type": "string"},
                },
                "required": ["frame", "source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_frames",
            "description": (
                "Flipbook ops: list, duplicate, delete, rename, reorder "
                "(frame_ids lists every frame id once)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string"},
                    "frame": {"type": "string"},
                    "name": {"type": "string"},
                    "frame_ids": {"type": "array"},
                    "sequence": {"type": "string"},
                    "target": {"type": "string"},
                },
                "required": ["op"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_clips",
            "description": (
                "Timeline-clip ops: list, remove, set (duration/motion/transition/fades), "
                "reorder (clip_ids lists every clip id once)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string"},
                    "clip": {"type": "string"},
                    "scene": {"type": "string"},
                    "duration": {"type": "number"},
                    "motion": {"type": "string"},
                    "transition": {"type": "string"},
                    "transition_duration": {"type": "number"},
                    "fade_in": {"type": "number"},
                    "fade_out": {"type": "number"},
                    "clip_ids": {"type": "array"},
                },
                "required": ["op"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_sequences",
            "description": (
                "Animation-shot ops: list, add, rename, duplicate, delete "
                "frame sequences."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string"},
                    "sequence": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["op"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_timelines",
            "description": (
                "Timeline ops: list, add, rename, duplicate, delete scenes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {"type": "string"},
                    "scene": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["op"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "video_to_frames",
            "description": (
                "Explode a finished video (@gen: id) into draw frames in a "
                "sequence (max 24)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job": {"type": "string"},
                    "sequence": {"type": "string"},
                    "max_frames": {"type": "number"},
                },
                "required": ["job"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_job",
            "description": (
                "Stop a queued or running image/video/export job ([job id]). "
                "Queued work never runs; running work is discarded."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job": {"type": "string"},
                },
                "required": ["job"],
            },
        },
    },
]


def _complete_once(messages, base_url, api_key, model):
    """One non-streaming completion as a single batch."""
    msg, usage = chat_complete_with_usage(
        base_url=base_url, api_key=api_key, model=model,
        messages=messages, tools=_TOOLS_SCHEMA,
    )
    return msg, usage, []


def _complete_stream(messages, base_url, api_key, model):
    """One streaming completion; streamed tokens ride with the message."""
    tokens: list[str] = []
    msg, usage = None, {"total": 0}
    for kind, payload in chat_complete_stream(
        base_url=base_url, api_key=api_key, model=model,
        messages=messages, tools=_TOOLS_SCHEMA,
    ):
        if kind == "token":
            tokens.append(payload)
        else:
            msg, usage = payload
    return msg, usage, tokens


def _cloud_brain() -> str:
    try:
        _, _, _brain_model = get_chat_config()
        return f"cloud:{_brain_model}"
    except ProviderError:
        return f"cloud:{provider_for('chat')}"


def _finish_turn(target, session, user, reply, actions, brain, cloud_error=None):
    """Adopt created projects and shape the turn dict (sync and stream share)."""
    target = _adopt_created_project(actions, session, user, target)
    schema = get_project(target, session, user)
    if brain == "demo":
        return {
            "reply": f"[{schema.name}] {reply}",
            "project_id": target,
            "project_name": schema.name,
            "actions": actions,
            "brain": brain,
            "cloud_error": cloud_error,
        }
    return {
        "reply": reply,
        "project_id": target,
        "project_name": schema.name,
        "actions": actions,
        "brain": brain,
    }


def _exec_cloud_tool(
    name, args, *, message, session, user, project_id, job_factory,
    turn_references=None,
) -> str:
    """Run one model-requested tool; shared by sync turns and streams."""
    try:
        if name == "project_status":
            result = tool_status(session, user, project_id)
        elif name == "create_project":
            result = tool_create_project(
                session, user, str(args.get("name") or "Untitled")
            )
        elif name == "create_frame":
            result = tool_create_frame(
                session, user, project_id, str(args.get("name") or ""),
                args.get("sequence"),
            )
        elif name == "detect_all":
            result = tool_detect_all(
                session,
                user,
                project_id,
                str(args.get("detector") or "heuristic"),
                str(args.get("direction") or "rtl"),
            )
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
            result = tool_export(
                session, user, project_id, job_factory,
                scene=args.get("scene"),
            )
        elif name == "add_clip":
            kind = str(args.get("kind") or "")
            source = str(args.get("source_id") or "")
            err = ""
            if not source:
                resolved = resolve_clip_source(message, session, user, project_id)
                if isinstance(resolved, tuple):
                    kind, source = resolved
                elif "video" in message.lower():
                    resolved = resolve_video_job(message, session, user, project_id)
                    if isinstance(resolved, tuple):
                        kind, source = resolved
                    else:
                        err = resolved
                else:
                    err = resolved
            if not source:
                result = err or "add_clip needs kind (panel|frame|media|video) and source_id."
            else:
                try:
                    secs = min(max(float(args.get("duration", 2.0)), 0.1), 30.0)
                except (TypeError, ValueError):
                    secs = 2.0
                motion = str(args.get("motion") or "none")
                if motion not in MOTIONS:
                    motion = "none"
                result = tool_add_clip(
                    session, user, project_id,
                    str(args.get("kind") or "frame"),
                    str(args["source_id"]),
                    args.get("scene"),
                    secs, motion,
                    str(args.get("transition") or "none"),
                    args.get("transition_duration"),
                    args.get("fade_in"),
                    args.get("fade_out"),
                )
        elif name == "add_subtitles":
            result = tool_add_subtitles(
                session, user, project_id, args.get("segments"), args.get("scene")
            )
        elif name == "create_image":
            result = tool_generate(
                session,
                user,
                project_id,
                str(args.get("prompt") or message),
                job_factory,
                model=args.get("model"),
                style=str(args.get("style") or "none"),
                negative_prompt=str(args.get("negative_prompt") or ""),
                reference_frame_id=args.get("reference_frame_id"),
                references=args.get("references"),
                entity_ids=args.get("entity_ids"),
                fallback_references=turn_references,
                width=args.get("width"), height=args.get("height"),
                attach_frame=bool(args.get("attach_frame")),
            )
        elif name == "create_video":
            try:
                seconds = min(max(float(args.get("seconds", 4.0)), 0.5), 10.0)
            except (TypeError, ValueError):
                seconds = 4.0
            result = tool_create_video(
                session,
                user,
                project_id,
                str(args.get("prompt") or message),
                seconds,
                args.get("model"),
                job_factory,
                args.get("references"),
                args.get("entity_ids"),
                fallback_references=turn_references,
            )
        elif name == "list_takes":
            result = tool_list_takes(session, user, project_id)
        elif name == "add_cue":
            take_id = str(args.get("take_id") or "")
            if not take_id:
                resolved = resolve_take(message, session, user, project_id)
                if isinstance(resolved, str):
                    result = resolved
                else:
                    take_id = resolved.id
            if take_id:
                try:
                    start = min(max(float(args.get("start_sec", 0.0)), 0.0), 3600.0)
                except (TypeError, ValueError):
                    start = 0.0
                result = tool_add_cue(
                    session, user, project_id, take_id,
                    args.get("scene"), start,
                    args.get("end_sec"), 0,
                    int(args.get("lane") or 0), 1.0,
                )
        elif name == "transcribe_audio":
            take_id = str(args.get("take_id") or "")
            if not take_id:
                resolved = resolve_take(message, session, user, project_id)
                if isinstance(resolved, str):
                    result = resolved
                else:
                    take_id = resolved.id
            if take_id:
                result = tool_transcribe_audio(
                    session, user, project_id, take_id,
                    args.get("scene"), str(args.get("mode") or "ENDPOINTING"),
                    bool(args.get("save_subtitles")),
                )
        elif name == "frame_from_media":
            result = tool_frame_from_media(
                session, user, project_id,
                str(args.get("source") or message),
                args.get("name"), args.get("sequence"),
            )
        elif name == "check_job":
            result = tool_check_job(
                session, user, str(args.get("job_id") or "")
            )
        elif name == "list_frames":
            result = tool_list_frames(
                session, user, project_id, args.get("sequence")
            )
        elif name == "manage_layers":
            result = tool_manage_layers(
                session, user, project_id,
                args.get("frame"), args.get("op", "list"),
                args.get("layer"), args.get("name"),
                args.get("opacity"), args.get("visible"),
                args.get("locked"), args.get("blend"),
                args.get("position"), args.get("sequence"),
                args.get("shapes"),
            )
        elif name == "tone_layer":
            result = tool_tone_layer(
                session, user, project_id, args.get("frame"),
                args.get("density", 0.2), args.get("angle", 20.0),
                args.get("color", "#111111"),
                args.get("opacity", 100),
                args.get("blend", "multiply"), args.get("name"),
                args.get("sequence"),
            )
        elif name == "layer_from_media":
            result = tool_layer_from_media(
                session, user, project_id, args.get("frame"),
                str(args.get("source") or message),
                args.get("name"), args.get("opacity", 100),
                args.get("blend", "normal"), args.get("sequence"),
            )
        elif name == "manage_frames":
            result = tool_manage_frames(
                session, user, project_id, str(args.get("op") or "list"),
                args.get("frame"), args.get("name"), args.get("frame_ids"),
                args.get("sequence"), args.get("target"),
            )
        elif name == "manage_clips":
            result = tool_manage_clips(
                session, user, project_id, str(args.get("op") or "list"),
                args.get("clip"), args.get("scene"),
                args.get("duration"), args.get("motion"),
                args.get("clip_ids"),
                args.get("transition"), args.get("transition_duration"),
                args.get("fade_in"), args.get("fade_out"),
            )
        elif name == "manage_sequences":
            result = tool_manage_sequences(
                session, user, project_id, str(args.get("op") or "list"),
                args.get("sequence"), args.get("name"),
            )
        elif name == "manage_timelines":
            result = tool_manage_timelines(
                session, user, project_id, str(args.get("op") or "list"),
                args.get("scene"), args.get("name"),
            )
        elif name == "video_to_frames":
            result = tool_video_to_frames(
                session, user, project_id,
                str(args.get("job") or ""),
                args.get("sequence"), args.get("max_frames", 12),
            )
        elif name == "cancel_job":
            result = tool_cancel_job(session, user, str(args.get("job") or ""))
        else:
            result = f"unknown tool: {name}"
    except HTTPException as exc:
        result = f"error {exc.status_code}: {exc.detail}"
    return result


def _cloud_loop(
    step, message, project_id, session, user, job_factory,
    ref_context="", history=None, open_frame=None, turn_references=None,
):
    """One cloud-brain loop for the sync turn and the SSE stream.

    step(messages, base_url, api_key, model) returns (msg, usage, tokens);
    the loop yields live token/tool events and ends with a result event.
    """
    context = tool_status(session, user, project_id)
    frame_line = f"\n\n{open_frame}" if open_frame else ""
    user_content = f"Project: {context}{frame_line}\n\n{message}"
    if ref_context:
        user_content = (
            f"Project: {context}{frame_line}\n\nReferenced:\n{ref_context}\n\n{message}"
        )
    past = [
        {
            "role": "user" if h.get("role") == "user" else "assistant",
            "content": str(h.get("text") or "")[:600],
        }
        for h in (history or [])[-8:]
    ]
    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM},
        *past,
        {"role": "user", "content": user_content},
    ]
    actions: list[dict] = []
    base_url, api_key, model = get_chat_config()
    total_tokens = 0
    for _ in range(6):
        msg, usage, tokens = step(messages, base_url, api_key, model)
        for text in tokens:
            yield {"type": "token", "text": text}
        total_tokens += usage.get("total", 0)
        calls = msg.get("tool_calls") or []
        if not calls:
            reply_text = msg.get("content") or "Done."
            record_usage(session, user.id, "chat", model, total_tokens or (len(message) + len(reply_text)) // 4)
            yield {"type": "result", "reply": reply_text, "actions": actions}
            return
        messages.append(msg)
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            yield {"type": "tool_start", "tool": name}
            result = _exec_cloud_tool(
                name, args, message=message, session=session, user=user,
                project_id=project_id, job_factory=job_factory,
                turn_references=turn_references,
            )
            summary = result[:200]
            actions.append({"tool": name, "summary": summary})
            yield {
                "type": "tool_result",
                "tool": name,
                "summary": summary,
                "job_ids": re.findall(r"\[job ([A-Za-z0-9_-]+)\]", result),
            }
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result[:2000],
                }
            )
    record_usage(session, user.id, "chat", model, total_tokens or (len(message) + 40) // 4)
    yield {"type": "result", "reply": "I ran out of steps \u2014 try a smaller request.", "actions": actions}

def _resolve_turn(message, project_id, session, user, current_frame_id, refs):
    """Scope a turn to its project and open frame (sync and stream share).

    Returns {"early": out} when the turn answers without tools.
    """
    if project_id is not None:
        # Explicit target: ownership check first (404 unless owned).
        schema = get_project(project_id, session, user)
        target = project_id
    else:
        projects = list_projects(session, user)
        if not projects:
            if any(p in message.lower() for p in CREATE_PROJECT_PHRASES):
                summary = tool_create_project(
                    session, user, parse_project_name(message)
                )
                m = re.search(r"\[project ([A-Za-z0-9_-]+)\]", summary)
                created = m.group(1) if m else None
                cname = parse_project_name(message)
                if created:
                    try:
                        cname = get_project(created, session, user).name
                    except HTTPException:
                        created = None
                record_usage(session, user.id, "chat", "demo", (len(message) + len(summary)) // 4)
                return {"early": {
                    "reply": summary,
                    "project_id": created,
                    "project_name": cname if created else None,
                    "actions": [{"tool": "create_project", "summary": summary}],
                    "brain": "demo",
                }}
            return {"early": {
                "reply": "You have no projects yet — say 'create project X' "
                "or make one on the dashboard.",
                "project_id": None,
                "project_name": None,
                "actions": [],
                "brain": "demo",
            }}
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
            return {"early": {
                "reply": (
                    f"Which project? Open one in the studio first, or name it "
                    f"({names})."
                ),
                "project_id": None,
                "project_name": None,
                "actions": [],
                "brain": "demo",
            }}
        schema = get_project(target, session, user)
    open_frame: str | None = None
    if current_frame_id:
        frames = _project_frames(session, user, target)
        current = next((f for f in frames if f.id == current_frame_id), None)
        if current is not None:
            open_frame = (
                f"Open draw frame: '{current.name or 'Untitled'}' "
                f"[frame {current_frame_id}] - draw tools target it "
                "when the request names no other frame."
            )
        if "@frame:" not in message:
            # Notion of current frame: scope the turn to the open frame
            # unless the message names another one. The id must belong to
            # this project, so a forged id degrades to disambiguation.
            named = [
                f for f in frames
                if f.name and len(f.name) > 2 and f.name.lower() in message.lower()
            ]
            if not named and current is not None:
                message = f"{message} @frame:{current_frame_id}"
    if (
        current_frame_id
        and wants_reference(message)
        and not any(
            (r.get("kind") if isinstance(r, dict) else r.kind) == "frame"
            for r in refs or []
        )
    ):
        # The open frame becomes an explicit reference only when the
        # request asks to reuse one; bare mentions stay scope-only so
        # generations never inherit a phantom reference frame.
        refs = [*(refs or []), {"kind": "frame", "id": current_frame_id}]
    ref_context = resolve_refs(refs, session, user) if refs else ""
    return {"message": message, "target": target, "refs": refs, "open_frame": open_frame, "ref_context": ref_context}

def run_agent_turn_stream(
    message, project_id, session, user, job_factory=None, clip_seconds=2.0,
    refs=None, history=None, current_frame_id=None,
):
    """Yield live turn events; the last event carries the finished turn.

    Event types: token, tool_start, tool_result, tool, text, result, error.
    A result event holds the same fields run_agent_turn returns.
    """
    try:
        resolved = _resolve_turn(
            message, project_id, session, user, current_frame_id, refs
        )
    except HTTPException as exc:
        yield {"type": "error", "status": exc.status_code, "message": str(exc.detail)}
        return
    if "early" in resolved:
        yield {"type": "result", **resolved["early"]}
        return
    message = resolved["message"]
    target = resolved["target"]
    refs = resolved["refs"]
    open_frame = resolved["open_frame"]
    ref_context = resolved["ref_context"]
    cloud_error: str | None = None
    if chat_configured():
        try:
            result: dict = {}
            for event in _cloud_loop(
                _complete_stream, message, target, session, user, job_factory,
                ref_context, history, open_frame, turn_references=refs,
            ):
                if event["type"] == "result":
                    result = event
                else:
                    yield event
            yield {
                "type": "result",
                **_finish_turn(
                    target, session, user, result["reply"],
                    result["actions"], _cloud_brain(),
                ),
            }
            return
        except Exception as exc:
            logger.warning("cloud agent failed (%s); using demo brain", exc)
            cloud_error = f"{type(exc).__name__}: {exc}"[:160]
    reply, actions = demo_turn(
        message, target, session, user, job_factory, clip_seconds, ref_context,
        history, fallback_references=refs,
    )
    for action in actions:
        yield {
            "type": "tool",
            "tool": action.get("tool"),
            "summary": action.get("summary"),
        }
    yield {"type": "text", "text": reply}
    record_usage(session, user.id, "chat", "demo", (len(message) + len(reply)) // 4)
    yield {
        "type": "result",
        **_finish_turn(target, session, user, reply, actions, "demo", cloud_error),
    }


def cloud_turn(
    message: str,
    project_id: str,
    session: Session,
    user: UserRow,
    job_factory=None,
    ref_context: str = "",
    history: list[dict] | None = None,
    open_frame: str | None = None,
    turn_references=None,
) -> tuple[str, list[dict]]:
    reply, actions = "", []
    for event in _cloud_loop(
        _complete_once, message, project_id, session, user, job_factory,
        ref_context, history, open_frame, turn_references,
    ):
        if event["type"] == "result":
            reply, actions = event["reply"], event["actions"]
    return reply, actions


# ------------------------------------------------------------------ entry ---


def _adopt_created_project(
    actions: list[dict], session: Session, user: UserRow, target: str
) -> str:
    """A create_project tool call moves the conversation to the new project."""
    if actions and actions[0].get("tool") == "create_project":
        m = re.search(r"\[project ([A-Za-z0-9_-]+)\]", actions[0].get("summary", ""))
        if m:
            try:
                get_project(m.group(1), session, user)
                return m.group(1)
            except HTTPException:
                pass
    return target


def run_agent_turn(
    message: str,
    project_id: str | None,
    session: Session,
    user: UserRow,
    job_factory=None,
    clip_seconds: float = 2.0,
    refs: list[AgentRef | dict] | None = None,
    history: list[dict] | None = None,
    current_frame_id: str | None = None,
) -> dict:
    resolved = _resolve_turn(message, project_id, session, user, current_frame_id, refs)
    if "early" in resolved:
        return resolved["early"]
    message = resolved["message"]
    target = resolved["target"]
    refs = resolved["refs"]
    open_frame = resolved["open_frame"]
    ref_context = resolved["ref_context"]
    cloud_error: str | None = None
    if chat_configured():
        try:
            reply, actions = cloud_turn(
                message, target, session, user, job_factory, ref_context,
                history, open_frame=open_frame, turn_references=refs,
            )
            return _finish_turn(target, session, user, reply, actions, _cloud_brain())
        except Exception as exc:
            logger.warning("cloud agent failed (%s); using demo brain", exc)
            # Surfaced (not just logged): the widget shows why the turn
            # fell back instead of looking stuck in demo mode.
            cloud_error = f"{type(exc).__name__}: {exc}"[:160]
    reply, actions = demo_turn(
        message, target, session, user, job_factory, clip_seconds, ref_context,
        history, fallback_references=refs,
    )
    record_usage(session, user.id, "chat", "demo", (len(message) + len(reply)) // 4)
    return _finish_turn(target, session, user, reply, actions, "demo", cloud_error)

