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
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException
from html.parser import HTMLParser
from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.orm import Session

from komaverse import storage
from komaverse.ai.direction import genre_suffix
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
from komaverse.api.audio import CueCreate, SynthCreate, add_cue, list_takes, synthesize_take
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
    ChatTurnRow,
    LayerRow,
    ExportReportRow,
    FrameRow,
    JobRow,
    PageRow,
    PanelRow,
    SequenceRow,
    UserRow,
)


def _fmt_when(dt) -> str:
    """UTC 'YYYY-MM-DD HH:MM' stamp for history/observability lines."""
    try:
        if getattr(dt, "tzinfo", None):
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "unknown time"


def tool_history(
    session: Session,
    user: UserRow,
    project_id: str | None,
    limit=10,
    thread_id: str | None = None,
) -> str:
    """Recent chat turns with dates and the tool actions each ran."""
    try:
        n = min(max(int(limit or 10), 1), 20)
    except (TypeError, ValueError):
        n = 10
    cond = ChatTurnRow.user_id == user.id
    cond = cond & (
        ChatTurnRow.project_id == project_id
        if project_id
        else ChatTurnRow.project_id.is_(None)
    )
    cond = cond & (
        ChatTurnRow.thread_id == thread_id
        if thread_id
        else ChatTurnRow.thread_id.is_(None)
    )
    rows = (
        session.execute(
            select(ChatTurnRow)
            .where(cond)
            .order_by(ChatTurnRow.created_at.desc(), ChatTurnRow.id.desc())
            .limit(n)
        )
        .scalars()
        .all()
    )
    rows = list(reversed(rows))
    if not rows:
        return "no history yet in this scope."
    lines = [f"Last {len(rows)} turn(s):"]
    for r in rows:
        lines.append(
            f"[{_fmt_when(r.created_at)}] {r.role}: {(r.text or '')[:200]}"
        )
        try:
            acts = json.loads(r.actions) if r.actions else []
        except (TypeError, ValueError):
            acts = []
        for a in acts[:6]:
            if isinstance(a, dict) and a.get("tool"):
                lines.append(f"  -> {a['tool']}: {str(a.get('summary') or '')[:120]}")
    return "\n".join(lines)


def tool_recent_jobs(session: Session, user: UserRow, project_id: str | None, limit=10) -> str:
    """Recent operations (generations, exports, transcriptions) with dates."""
    try:
        n = min(max(int(limit or 10), 1), 20)
    except (TypeError, ValueError):
        n = 10
    cond = JobRow.user_id == user.id
    if project_id:
        cond = cond & (JobRow.project_id == project_id)
    rows = (
        session.execute(
            select(JobRow)
            .where(cond)
            .order_by(JobRow.created_at.desc(), JobRow.id.desc())
            .limit(n)
        )
        .scalars()
        .all()
    )
    if not rows:
        return "no jobs yet in this scope."
    lines = [f"Last {len(rows)} job(s):"]
    for j in rows:
        state = "ready" if j.status == "done" and j.result_url else j.status
        lines.append(
            f"[{_fmt_when(j.created_at)}] {j.kind} {state}: "
            f"{(j.prompt or '')[:100]} [job {j.id}]"
        )
    return "\n".join(lines)
from komaverse.security import limiter
from komaverse.skills import SkillError, list_skills, read_skill
from pydantic import BaseModel

logger = logging.getLogger("komaverse.agent")


class AgentRef(BaseModel):
    """One @-mention parsed by the client: a specific studio entity."""

    kind: str  # frame | panel | media | take | gen | sequence (+ role for video slots)
    id: str
    role: str = "reference"  # start | end | reference (video first/last frame slots)


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
                if row is None or row.kind not in ("image", "video"):
                    raise _Missing()
                get_project(row.project_id, session, user) if row.project_id else None
                prompt = (row.prompt or "")[:200]
                lock = ", ".join(
                    f"{label}: {value}"
                    for label, value in (
                        ("style", row.style),
                        ("genre", row.genre),
                        ("seed", row.seed),
                    )
                    if value is not None and value != "none"
                )
                kind_label = "video" if row.kind == "video" else "generation"
                dur = f", {float(row.seconds or 0):.1f}s" if row.kind == "video" else ""
                lines.append(
                    f"@gen:{rid} = AI {kind_label} ({row.status}{dur})"
                    + (f": '{prompt}'" if prompt else "")
                    + (f" [locked {lock}]" if lock else "")
                )
            elif kind == "sequence":
                row = session.get(SequenceRow, rid)
                if row is None:
                    raise _Missing()
                get_project(row.project_id, session, user)
                frames = [f for f in _project_frames(session, user, row.project_id) if f.sequence_id == row.id]
                layers = sum(len(f.layers) for f in frames)
                lines.append(
                    f"@sequence:{rid} = Sequence '{row.name or 'Untitled'}' "
                    f"({len(frames)} frames, {layers} layers)"
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


#: Spelled-out lengths ("four seconds") count like digits. Bare words
#: stay out on purpose: "two heroes" names no length, only an explicit
#: number word plus a time unit does.
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30,
}
_WORD_SECONDS_RE = re.compile(
    r"\b(" + "|".join(_WORD_NUMBERS) + r")\s*(seconds?|secs?|s|minutes?|mins?)\b",
    re.IGNORECASE,
)


def parse_seconds(text: str) -> float | None:
    """Clip length from chat ("3s", "2.5 seconds", "four seconds")."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)\b", text.lower())
    if m:
        return min(max(float(m.group(1)), 0.1), 30.0)
    m = _WORD_SECONDS_RE.search(text or "")
    if not m:
        return None
    value = float(_WORD_NUMBERS[m.group(1).lower()])
    if m.group(2).lower().startswith("min"):
        value *= 60.0
    return min(max(value, 0.1), 30.0)


#: The user handed blank dimensions to the agent ("decide the rest",
#: "go"): documented defaults apply, no questions. Checked before every
#: ask-gate so an answer turn can never re-ask what it was told to fill.
_DELEGATED_RE = re.compile(
    r"\bgo\b"
    r"|\b(decide|whatever|you\s+choose|your\s+call|go\s+ahead|surprise\s+me|"
    r"i\s+don'?t\s+mind|up\s+to\s+you|use\s+whatever|your\s+choice|"
    r"you\s+decide|as\s+you\s+like|dealers?\s+choice|defaults?)\b",
    re.IGNORECASE,
)


def _delegates_decisions(text: str | None) -> bool:
    return bool(_DELEGATED_RE.search(text or ""))


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


#: Orientation framing that names no usable format. Bare nouns stay out on
#: purpose: "a portrait of X" or "town square" must not interrogate the
#: user — only explicit framing ("make it wide", "which ratio") asks.
ORIENTATION_RE = re.compile(
    r"\b(wide|widescreen|ultrawide|vertical|horizontal|panorama|panoramic)\b"
    r"|\b(portrait|landscape)\s+(orientation|mode|format)\b"
    r"|\baspect\s+ratio\b|\bratio\b|\bresolution\b"
    r"|\b(what|which)\s+(size|format)\b",
    re.IGNORECASE,
)
#: Quality words that answer the resolution question (image only). Bare
#: "high" stays out on purpose: "a high mountain" names no quality tier,
#: only explicit quality talk ("high quality", "draft", "4k") counts.
QUALITY_RE = re.compile(
    r"\b(draft|standard|hd|uhd|4k|8k)\b"
    r"|\b(high|low)\s*[- ]\s*(quality|res|resolution)\b"
    r"|\b(high|low)\s+(quality|res|resolution)\b"
    r"|\b(512|768|1024|2048)\s*px\b",
    re.IGNORECASE,
)
#: Framing hints that steer the wide/portrait default for "go" answers.
_WIDE_HINT_RE = re.compile(r"wide|horizontal|landscape|panorama", re.IGNORECASE)
_TALL_HINT_RE = re.compile(r"tall|vertical|portrait", re.IGNORECASE)
#: Duration words without a number (video only - images have no length).
DURATION_RE = re.compile(
    r"\b(short|shorter|long|longer|brief|quick|duration|seconds?|minutes?|extended|condensed)\b",
    re.IGNORECASE,
)
#: Sound words in a video request. The video model renders silent — sound
#: is a voiceover take or timeline audio instead, so these always ask.
SOUND_RE = re.compile(
    r"\b(with|with\s+the|add|include|including)\s+(sound|music|audio|song|narration|voiceover|voices?|dialogue|sfx)\b"
    r"|\b(soundtrack|background\s+music|theme\s+song|talking|speaking|saying\s+something)\b",
    re.IGNORECASE,
)
#: Resolution tiers + explicit WxH for video size (None = provider default).
VIDEO_RES_RE = re.compile(r"\b(480p|720p|1080p|full\s*hd|4k|uhd)\b", re.IGNORECASE)
VIDEO_SIZE_RE = re.compile(r"\b(\d{3,4})\s*[x×]\s*(\d{3,4})\b")


def _parse_video_size(text: str) -> tuple[int, int] | None:
    """(w, h) from explicit WxH or a res tier + orientation.

    None means provider default (cheapest): the call sends no size.
    """
    m = VIDEO_SIZE_RE.search(text or "")
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if 64 <= w <= 3840 and 64 <= h <= 3840:
            return (w, h)
        return None
    m = VIDEO_RES_RE.search(text or "")
    if not m:
        return None
    tier = m.group(1).lower().replace(" ", "")
    if tier == "fullhd":
        tier = "1080p"
    t = (text or "").lower()
    if "1:1" in t or "square" in t:
        return {"480p": (480, 480), "720p": (720, 720), "1080p": (1080, 1080),
                "4k": (2160, 2160), "uhd": (2160, 2160)}[tier]
    if bool(_TALL_HINT_RE.search(text or "")) or "9:16" in t:
        return {"480p": (480, 854), "720p": (720, 1280), "1080p": (1080, 1920),
                "4k": (2160, 3840), "uhd": (2160, 3840)}[tier]
    return {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080),
            "4k": (3840, 2160), "uhd": (3840, 2160)}[tier]


def _size_str(size: tuple[int, int] | None) -> str | None:
    return f"{size[0]}x{size[1]}" if size else None
#: Main image of a fetched page (og:image / twitter:image), either
#: attribute order. Relative URLs stay out: only absolute http(s) art
#: rides into chat replies.
_OG_IMAGE_RE = re.compile(
    r"<meta\s[^>]*?(?:property=[\"']og:image[\"']|name=[\"'](?:og:image|twitter:image)[\"'])[^>]*?content=[\"']([^\"']+)[\"']"
    r"|<meta\s[^>]*?content=[\"']([^\"']+)[\"'][^>]*?(?:property=[\"']og:image[\"']|name=[\"'](?:og:image|twitter:image)[\"'])",
    re.IGNORECASE,
)


def _page_main_image(html: str) -> str:
    m = _OG_IMAGE_RE.search(html or "")
    if not m:
        return ""
    url = (m.group(1) or m.group(2) or "").strip()
    return url if url.startswith("http") else ""


#: Style talk: "in the style of X" carries its value, the rest may not.
STYLE_TALK_RE = re.compile(
    r"\bstyles?\b|\bstylish\b|\bstyliz(?:ed|ing)\b|\bstylis(?:ed|ing)\b",
    re.IGNORECASE,
)
STYLE_VALUE_RE = re.compile(
    r"style\s+of\s+\w|style\s*:\s*\w|styliz(?:ed|ing)\s+(as|like|in)\s+\w"
    # A reference token after "of" carries the value: the panel/frame
    # pixels are the style source, attached as a reference downstream.
    r"|style\s+of\s+@(?:frame|panel|gen|media):",
    re.IGNORECASE,
)
STYLE_CHANGE_RE = re.compile(
    r"\b(different|new|another|other|change|changing)\b", re.IGNORECASE
)


def _missing_orientation(prompt, width, height) -> str | None:
    """Orientation word with no usable size: the matched word or None."""
    if width is not None or height is not None:
        return None
    if _parse_ratio(prompt or "") is not None:
        return None
    m = ORIENTATION_RE.search(prompt or "")
    return m.group(0) if m else None


def _missing_duration(prompt, seconds, default_seconds: float = 4.0) -> str | None:
    """Duration word with no usable length: the matched word or None.

    Digits and spelled-out numbers ("four seconds") both count, so a
    stated length never asks.
    """
    m = DURATION_RE.search(prompt or "")
    if not m:
        return None
    if parse_seconds(prompt or "") is not None:
        return None
    if seconds != default_seconds:
        return None
    return m.group(0)


def _locked_style(session, user, project_id):
    """(style, genre) of the newest finished image generation, if recent."""
    row = _latest_done_image_row(session, user, project_id)
    if row is None:
        return None
    if not row.style or row.style == "none":
        return None
    return (row.style, row.genre if row.genre not in (None, "none") else "")


def _missing_style(prompt, style, genre, locked) -> str | None:
    """Style talk with no usable style: the matched word or None.

    A locked style resolves "same style" silently; an explicit change
    ("change the style") or no lock at all asks.
    """
    m = STYLE_TALK_RE.search(prompt or "")
    if not m:
        return None
    if (style and style != "none") or (genre and genre != "none"):
        return None
    if STYLE_VALUE_RE.search(prompt or ""):
        return None
    if locked and not STYLE_CHANGE_RE.search(prompt or ""):
        return None
    return m.group(0)


def _ask_before_generating(tool: str, missing: list[tuple[str, str, str]]) -> str:
    """Blocking question for subject-free generations (user-confirmed).

    Asked at most once per subject: one short question for the listed
    pieces only — never re-ask answered or delegated pieces, and call
    the tool on the next turn with defaults for anything still blank.
    """
    bits = "; ".join(f"{what} ({detail} — suggest {choices})" for what, detail, choices in missing)
    n = "one detail" if len(missing) == 1 else f"{len(missing)} details"
    return (
        f"Before generating I need {n}: {bits}. "
        "Ask ONE short question for these pieces only — nothing else, "
        "and never re-ask pieces the conversation already answered. "
        f"Call {tool} on the next turn with the answers, taking the "
        "stated defaults for anything still blank."
    )


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


def tool_list_skills():
    """Name + describe every installed studio craft skill."""
    found = list_skills()
    if not found:
        return "No craft skills installed."
    lines = ["- " + s["name"] + ": " + s["description"] for s in found]
    return "Studio craft skills: " + "; ".join(lines)


def tool_read_skill(name, section=None):
    """Read one studio craft skill (or a single ## section) into context."""
    try:
        return read_skill(name or "", section=section or None)
    except SkillError as exc:
        return str(exc)


def _parse_skill_name(text):
    """Skill name next to skill(s): X skill or skill X (found by its dash)."""
    strip_chars = '.,!?"' + chr(39)
    tokens = text.split()
    for i, tok in enumerate(tokens):
        if tok.strip(strip_chars) in ("skill", "skills"):
            before = tokens[i - 1] if i > 0 else ""
            after = tokens[i + 1] if i + 1 < len(tokens) else ""
            for cand in (before, after):
                cand = cand.strip(strip_chars)
                if cand and "-" in cand and all(
                    ch.islower() or ch.isdigit() or ch == "-" for ch in cand
                ):
                    return cand
    return None


def _html_to_text(html):
    """Strip tags and scripts from a page; collapse whitespace."""
    from html.parser import HTMLParser

    class _Text(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
            self.skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript"):
                self.skip += 1
            elif tag in ("p", "br", "li", "h1", "h2", "h3", "h4", "tr", "title"):
                self.parts.append(chr(10))

        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript") and self.skip:
                self.skip -= 1

        def handle_data(self, data):
            if not self.skip:
                self.parts.append(data)

    doc = _Text()
    doc.feed(html or "")
    doc.close()
    return " ".join("".join(doc.parts).split())


def tool_list_tools():
    """Name + describe every studio tool the agent can call."""
    lines = []
    for entry in _TOOLS_SCHEMA:
        fn = entry.get("function", {}) if isinstance(entry, dict) else {}
        lines.append("- " + str(fn.get("name", "?")) + ": " + str(fn.get("description", "")))
    return "Studio tools: " + "; ".join(lines)


def tool_web_fetch(url, max_chars=4000):
    """Fetch one http(s) page and return its text (docs, references)."""
    cleaned = (url or "").strip()
    cleaned = cleaned.strip(".,!?" + chr(34) + chr(39) + "<>")
    if not cleaned.startswith("http://") and not cleaned.startswith("https://"):
        return "web_fetch needs an http(s) URL."
    try:
        limit = min(max(int(max_chars or 4000), 200), 20000)
    except (TypeError, ValueError):
        limit = 4000
    try:
        resp = httpx.get(
            cleaned,
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": "KomaverseStudio/1.0"},
        )
    except Exception as exc:
        return "fetch failed: " + type(exc).__name__ + ": " + str(exc)[:160]
    if resp.status_code == 403:
        # Bot-walled pages (Fandom wikis): one retry behind a plain
        # browser UA before giving up.
        try:
            resp = httpx.get(
                cleaned,
                timeout=20.0,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"},
            )
        except Exception as exc:
            return "fetch failed: " + type(exc).__name__ + ": " + str(exc)[:160]
    if resp.status_code >= 400:
        return "fetch failed: HTTP " + str(resp.status_code)
    ctype = resp.headers.get("content-type", "")
    if not ctype.startswith("text/") and "html" not in ctype and "json" not in ctype and "xml" not in ctype:
        return "not a text page (" + ctype + "): " + cleaned
    body = resp.text
    if len(body) > 2000000:
        body = body[:2000000]
    text = body if "json" in ctype else _html_to_text(body)
    if len(text) > limit:
        text = text[:limit] + " ...[truncated]"
    out = text + " [from " + str(resp.url) + "]"
    main_image = _page_main_image(body) if "html" in ctype else ""
    if main_image:
        out += (
            "\n[image: " + main_image + "] — the page's main image; paste "
            "it into your reply as a bare URL to show it in chat."
        )
    return out


def _unwrap_ddg(href):
    """Unwrap DDG redirect links to the real page URL."""
    from urllib.parse import parse_qs, unquote, urlparse

    if not href:
        return ""
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        if qs.get("uddg"):
            return unquote(qs["uddg"][0])
    if href.startswith("//"):
        return "https:" + href
    return href


class _DDGResults(HTMLParser):
    """Collect DDG html result links + snippets. Stdlib only."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self.cur = None
        self.in_snippet = 0

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class", "")
        if tag == "a" and "result__a" in cls:
            self.cur = {"href": dict(attrs).get("href", ""), "title": ""}
        elif tag in ("td", "div") and "result-snippet" in cls:
            self.in_snippet += 1

    def handle_endtag(self, tag):
        if tag == "a" and self.cur is not None:
            self.results.append(self.cur)
            self.cur = None
        elif tag in ("td", "div") and self.in_snippet:
            self.in_snippet -= 1

    def handle_data(self, data):
        if self.cur is not None:
            self.cur["title"] += data
        elif self.in_snippet and self.results:
            prev = self.results[-1].get("snippet", "")
            self.results[-1]["snippet"] = prev + data


def _search_duckduckgo_web(query, limit=5):
    """Keyless DDG html results: title, url, snippet."""
    try:
        resp = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            timeout=20.0,
            headers={"User-Agent": "KomaverseStudio/1.0"},
        )
    except Exception:
        return []
    if resp.status_code >= 400:
        return []
    doc = _DDGResults()
    try:
        doc.feed(resp.text[:500000])
        doc.close()
    except Exception:
        return []
    out = []
    for r in doc.results:
        url = _unwrap_ddg(r.get("href", ""))
        if not url.startswith("http"):
            continue
        title = " ".join(str(r.get("title", "")).split())
        snippet = " ".join(str(r.get("snippet", "")).split())
        if title and url:
            out.append((title, url, snippet))
        if len(out) >= limit:
            break
    return out


def _search_wikipedia(query, limit=5):
    """Keyless Wikipedia opensearch: titles, urls, descriptions."""
    try:
        resp = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": query,
                "limit": limit,
                "format": "json",
            },
            timeout=20.0,
            headers={"User-Agent": "KomaverseStudio/1.0"},
        )
    except Exception:
        return []
    if resp.status_code >= 400:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    titles = data[1] if len(data) > 1 else []
    descs = data[2] if len(data) > 2 else []
    urls = data[3] if len(data) > 3 else []
    out = []
    for i, title in enumerate(titles):
        url = urls[i] if i < len(urls) else ""
        desc = descs[i] if i < len(descs) else ""
        out.append((str(title), str(url), str(desc)))
    return out


def _search_duckduckgo_instant(query, limit=5):
    """Keyless DDG instant answers: abstract + related topics + image."""
    try:
        resp = httpx.get(
            "https://api.duckduckgo.com/",
            params={
                "q": query,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1,
            },
            timeout=20.0,
            headers={"User-Agent": "KomaverseStudio/1.0"},
        )
    except Exception:
        return "", "", [], ""
    if resp.status_code >= 400:
        return "", "", [], ""
    try:
        data = resp.json()
    except ValueError:
        return "", "", [], ""
    if not isinstance(data, dict):
        return "", "", [], ""
    abstract = str(data.get("AbstractText") or "")
    abstract_url = str(data.get("AbstractURL") or "")
    image = str(data.get("Image") or "")
    if image.startswith("/"):
        image = "https://duckduckgo.com" + image
    topics = []
    for item in data.get("RelatedTopics") or []:
        if isinstance(item, dict) and item.get("Text") and item.get("FirstURL"):
            topics.append((str(item["Text"]), str(item["FirstURL"])))
        if len(topics) >= limit:
            break
    return abstract, abstract_url, topics, image


def _search_wikipedia_images(titles, limit=3):
    """Keyless Wikipedia thumbnails: (title, thumb url, page url)."""
    titles = [t for t in (titles or []) if t][:limit]
    if not titles:
        return []
    try:
        resp = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "prop": "pageimages",
                "format": "json",
                "pithumbsize": 500,
                "titles": "|".join(titles),
            },
            timeout=20.0,
            headers={"User-Agent": "KomaverseStudio/1.0"},
        )
    except Exception:
        return []
    if resp.status_code >= 400:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    out = []
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        thumb = (page.get("thumbnail") or {}).get("source") or ""
        if not thumb.startswith("http"):
            continue
        title = str(page.get("title") or "")
        out.append((
            title,
            thumb,
            "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
        ))
    return out


def tool_web_search(query, max_results=5):
    """Search the open web without keys: hits, answers, wiki fallback."""
    cleaned = (query or "").strip()
    if not cleaned:
        return "web_search needs a query, e.g. web_search cel shading tutorial."
    try:
        limit = min(max(int(max_results or 5), 1), 10)
    except (TypeError, ValueError):
        limit = 5
    lines = ["Web results for " + cleaned + ":"]
    for title, url, snippet in _search_duckduckgo_web(cleaned, limit):
        line = "- " + title[:120] + " (" + url + ")"
        if snippet:
            line += ": " + snippet[:200]
        lines.append(line)
    abstract, abstract_url, topics, _instant_image = _search_duckduckgo_instant(cleaned, limit)
    if abstract:
        line = "Answer: " + abstract[:300]
        if abstract_url:
            line += " [" + abstract_url + "]"
        lines.append(line)
    for ttext, turl in topics:
        lines.append("- " + ttext[:200] + " (" + turl + ")")
    if len(lines) == 1:
        for title, url, desc in _search_wikipedia(cleaned, limit):
            line = "- " + title
            if url:
                line += " (" + url + ")"
            if desc:
                line += ": " + desc[:200]
            lines.append(line)
    if len(lines) == 1:
        return "no results for " + cleaned + " (all sources empty)."
    lines.append("Use web_fetch to read any result in full.")
    return chr(10).join(lines)


_FANDOM_URL_RE = re.compile(
    r"https://([a-z0-9-]+)\.fandom\.com/wiki/([^?#]+)", re.IGNORECASE
)


def _search_fandom_images(wiki, title, limit=3):
    """Character-wiki thumbnails via a Fandom MediaWiki pageimages API.

    Wikipedia has no free lead image for most characters; the Fandom
    infobox thumbnail fills exactly that gap.
    """
    from urllib.parse import unquote

    title = unquote(title or "").replace("_", " ").strip()
    if not wiki or not title:
        return []
    try:
        resp = httpx.get(
            f"https://{wiki}.fandom.com/api.php",
            params={
                "action": "query",
                "prop": "pageimages",
                "format": "json",
                "pithumbsize": 500,
                "titles": title,
            },
            timeout=20.0,
            headers={"User-Agent": "KomaverseStudio/1.0"},
        )
    except Exception:
        return []
    if resp.status_code >= 400:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    out = []
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        thumb = (page.get("thumbnail") or {}).get("source") or ""
        if not thumb.startswith("http"):
            continue
        name = str(page.get("title") or title)
        out.append((
            name,
            thumb,
            f"https://{wiki}.fandom.com/wiki/" + name.replace(" ", "_"),
        ))
        if len(out) >= limit:
            break
    return out


def _search_anilist_character(query, limit=1):
    """Keyless anime/manga character art via the AniList GraphQL API.

    AniList search returns the single best match with direct CDN art —
    the most reliable likeness for anime characters, where Wikipedia
    has no free lead image. Non-anime queries return nothing.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return []
    try:
        resp = httpx.post(
            "https://graphql.anilist.co",
            json={
                "query": (
                    "query($s:String){Character(search:$s){"
                    "name{full} image{large} siteUrl}}"
                ),
                "variables": {"s": cleaned},
            },
            timeout=20.0,
            headers={"User-Agent": "KomaverseStudio/1.0"},
        )
    except Exception:
        return []
    if resp.status_code >= 400:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    char = ((data or {}).get("data") or {}).get("Character") or {}
    if not isinstance(char, dict):
        return []
    image = (char.get("image") or {}).get("large") or ""
    if not image.startswith("http"):
        return []
    name = ((char.get("name") or {}).get("full") or cleaned).strip()
    site = char.get("siteUrl") or "https://anilist.co"
    return [(name, image, str(site))][: max(limit, 1)]


def _search_commons_images(query, limit=3):
    """Keyless Wikimedia Commons file search: (title, thumb, page).

    Best for real people, places, and objects (freely licensed photos);
    anime characters mostly surface cosplay shots, so this runs last.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return []
    try:
        resp = httpx.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": cleaned,
                "gsrlimit": limit,
                "gsrnamespace": 6,
                "prop": "imageinfo",
                "iiprop": "url",
                "iiurlwidth": 500,
                "format": "json",
            },
            timeout=20.0,
            headers={"User-Agent": "KomaverseStudio/1.0"},
        )
    except Exception:
        return []
    if resp.status_code >= 400:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    out = []
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        info = (page.get("imageinfo") or [{}])[0] or {}
        thumb = info.get("thumburl") or info.get("url") or ""
        if not thumb.startswith("http"):
            continue
        title = str(page.get("title") or "")
        out.append((
            title,
            thumb,
            "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_"),
        ))
        if len(out) >= limit:
            break
    return out


def tool_web_images(query, max_results=3):
    """Keyless picture results: AniList art + Wikipedia + Fandom + Commons.

    Returns thumbnail URLs the chat renders inline — paste them into
    the reply as bare https URLs (one per line), name what each shows,
    and link each source page. Each picture carries a +Media button
    that saves it into the project as a @gen: reference — mention it
    when the user may want to reuse one.
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return "web_images needs a query, e.g. web_images Studio Ghibli background."
    try:
        limit = min(max(int(max_results or 3), 1), 5)
    except (TypeError, ValueError):
        limit = 3
    buckets: list[list[tuple[str, str, str]]] = []
    buckets.append(_search_anilist_character(cleaned, 1))
    _abstract, _abstract_url, _topics, instant_image = _search_duckduckgo_instant(cleaned, 1)
    buckets.append(
        [("Instant answer", instant_image, _abstract_url or "https://duckduckgo.com/")]
        if instant_image.startswith("http")
        else []
    )
    titles = [t for t, _u, _d in _search_wikipedia(cleaned, limit)]
    buckets.append(_search_wikipedia_images(titles, limit))
    fandom_found: list[tuple[str, str, str]] = []
    for _t, url, _s in _search_duckduckgo_web(cleaned, 8):
        m = _FANDOM_URL_RE.match(url or "")
        if m:
            fandom_found.extend(_search_fandom_images(m.group(1), m.group(2), limit))
    buckets.append(fandom_found)
    buckets.append(_search_commons_images(cleaned, limit))
    # Round-robin across sources so one strong source cannot crowd
    # out the rest of the diversity budget.
    found: list[tuple[str, str, str]] = []
    for i in range(max((len(b) for b in buckets), default=0)):
        for bucket in buckets:
            if i < len(bucket):
                found.append(bucket[i])
    seen = set()
    lines = ["Images for " + cleaned + ":"]
    for title, thumb, page in found:
        if thumb in seen:
            continue
        seen.add(thumb)
        lines.append("- " + title[:120] + ": " + thumb + " [img] (source: " + page + ")")
        if len(seen) >= limit:
            break
    if len(lines) == 1:
        return "no images for " + cleaned + " (all sources empty)."
    lines.append(
        "Paste the thumbnail URLs into your reply as bare https URLs "
        "(one per line) so the chat shows them; say what each one shows "
        "and link its source page."
    )
    return chr(10).join(lines)


def _parse_search_query(text):
    """Strip a leading search command, keep the natural question."""
    lowered = text.strip()
    for prefix in (
        "web search",
        "search the web for",
        "search for",
        "look up",
        "google",
    ):
        if lowered.lower().startswith(prefix):
            return lowered[len(prefix):].strip(" .,!?")
    return lowered


def _parse_image_query(text):
    """Strip image-request phrasing, keep the subject to picture."""
    cleaned = (text or "").strip()
    cleaned = re.sub(
        r"(?i)\b(show me |find |get |show )?(images?|pictures?|photos?)\s+of\b|\bwhat\s+does\b|\bwhat\s+do\b|\blooks?\s+like\b|\bshow\s+me\s+what\b",
        " ",
        cleaned,
    )
    return " ".join(cleaned.split(" .,!?")).strip(" .,!?")


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
    head = (
        f"Detected {total} panels across {len(p.pages)} pages "
        f"({used}, {direction}, free)."
        if used == detector
        else f"Detected {total} panels across {len(p.pages)} pages "
        f"({detector} unavailable, fell back to {used}, {direction}, free)."
    )
    # The panel roster rides the reply: later turns reference detected
    # panels by @panel: id from context alone — counts never suffice.
    session.expire_all()
    p = get_project(project_id, session, user)
    lines = [head]
    for page in sorted(p.pages, key=lambda pg: pg.index):
        ordered = sorted(page.panels, key=lambda pn: pn.reading_order)
        bits = [
            f'@panel:{pn.id} "{(pn.name or f"Panel {pn.reading_order + 1}")[:24]}"'
            for pn in ordered[:8]
        ]
        if len(ordered) > 8:
            bits.append(f"+{len(ordered) - 8} more (open Scan to see all)")
        lines.append(f"Page {page.index + 1} (@media:{page.id}): " + ", ".join(bits))
    return "\n".join(lines)


def auto_direct_core(
    session: Session,
    user: UserRow,
    project_id: str,
    seconds: float = 2.0,
    motions: list[str] | None = None,
    scene_id: str | None = None,
    new_scene: bool = False,
):
    """All panels → timeline clips in reading order. Returns the Scene.

    Destination control for the UI's current/new popup: scene_id appends
    to that timeline (404 when foreign), new_scene forces a fresh one,
    and neither keeps the legacy first-empty-else-new behavior.
    """
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
    if scene_id:
        target = next((s for s in scenes if s.id == scene_id), None)
        if target is None:
            raise HTTPException(404, "timeline not found")
    elif new_scene or not scenes or scenes[0].clips:
        target = create_scene(
            project_id, SceneCreate(name="Auto cut"), session, user
        )
    else:
        target = scenes[0]
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
        return f"{chained}Export started ({clips} clips) [job {job.id}] - reply now with the job id; the UI follows it live, do not poll check_job."
    run_export_job(job.id, job_factory)
    session.refresh(job)
    if job.status != "done":
        return f"Export failed: {job.error or 'unknown error'}"
    report = session.get(ExportReportRow, job.id)
    n = report.clips_rendered if report else clips
    secs = report.rendered_sec if report else 0.0
    return f"{chained}Rendered {n} clips ({secs:.1f}s): {job.result_url}"


REF_TOKEN_RE = re.compile(r"@(frame|panel|media|gen|sequence):([A-Za-z0-9_-]+)")

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

    Bare @-mentions carry no intent: the open frame rides the turn as a
    scope line (knowledge, never pixels), so only phrasal intent (or an
    explicitly picked reference) attaches one.
    """
    return bool(REFERENCE_INTENT_RE.search(text or ""))


FRAME_DIRECTED_RE = re.compile(
    r"((this|that|the|current|open)\s+frames?\b"
    r"|\btrace\b|\bredraw\b|\bpaint\s+over\b|\bdraw\s+(on|onto|over)\b"
    r"|\b(edit|change|fix|continue|paint|draw)\s+(this|it|that|the)\b"
    r"|\bon\s+(it|this|that|the\s+frames?)\b)",
    re.IGNORECASE,
)


def clean_prompt_text(prompt: str) -> str:
    return REF_TOKEN_RE.sub("", prompt or "").strip(" :")

#: How far back a finished generation still counts as "just made" for
#: deictic resolution ("use this character"). Older work needs an
#: explicit @gen: mention; only it bypasses the window.
RECENT_GEN_HOURS = 24


def _latest_done_image_job(session, user, project_id) -> str | None:
    """Id of the newest finished image generation in this project.

    Deictic follow-ups ("use this character", "make it a video") name no
    id; the open frame is already in scope, so this gives the turn the
    artifact the conversation just produced. Returns None when there is
    nothing finished to point at.
    """
    row = _latest_done_image_row(session, user, project_id)
    return row.id if row is not None else None


def _latest_done_image_row(session, user, project_id):
    """Newest finished image generation row in this project, if recent."""
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            hours=RECENT_GEN_HOURS
        )
        return (
            session.execute(
                select(JobRow)
                .where(
                    JobRow.user_id == user.id,
                    JobRow.project_id == project_id,
                    JobRow.kind == "image",
                    JobRow.status == "done",
                    JobRow.created_at.is_not(None),
                    JobRow.created_at >= cutoff,
                )
                .order_by(JobRow.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
    except Exception:
        return None


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
        elif kind == "sequence":
            seq = session.get(SequenceRow, rid)
            if seq is None or seq.project_id != project_id:
                notes.append(f"Unknown sequence {rid} - ignored.")
                continue
            frames = sorted(
                [f for f in _project_frames(session, user, project_id) if f.sequence_id == seq.id],
                key=lambda f: f.index,
            )
            layers = sum(len(f.layers) for f in frames)
            add(("sequence", rid), {"id": f"sequence:{rid}", "name": f"Sequence '{seq.name or 'Untitled'}'", "kind": "reference", "anchor": "sequence frames", "sequence_id": rid})
            notes.append(f"sequence '{seq.name or rid}' holds {len(frames)} frames, {layers} layers")
    for ref in references or []:
        if isinstance(ref, dict) and ref.get("kind") and ref.get("id"):
            extra, more = gather_reference_snapshots(session, user, project_id, f"@{ref['kind']}:{ref['id']}")
            for s in extra:
                add((s.get("id"), None), s)
            notes.extend(more)
    # Roles ride the refs into the snaps so the export can route start,
    # end, and guidance images to the right video slots.
    role_of = {}
    for ref in references or []:
        if isinstance(ref, dict) and ref.get("kind") and ref.get("id"):
            role_of[(ref["kind"], ref["id"])] = ref.get("role") or "reference"
    for s in snaps:
        kind, _, rid = (s.get("id") or "").partition(":")
        s["role"] = role_of.get((kind, rid), s.get("role", "reference"))
    return snaps, notes


_START_HINTS = (
    "start frame", "first frame", "opening frame", "start from",
    "starts from", "starting from", "start with", "starts with",
    "starting with", "open on", "open with", "opens on", "opens with",
    "begin with", "begins with", "beginning with",
)
_END_HINTS = (
    "end frame", "last frame", "final frame", "closing frame", "end on",
    "ends on", "ending on", "end at", "ends at", "ending at", "end with",
    "ends with", "ending with", "close on", "closes on", "finish on",
)
_FROM_TO_RE = re.compile(
    r"from\s+@(frame|panel|media|gen|sequence):([A-Za-z0-9_-]+)"
    r"\s+to\s+@(frame|panel|media|gen|sequence):([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def _tag_reference_roles(raw: str, refs):
    """Tag each reference with its video role: start, end, or reference.

    Start/end frame slots open and close the generated video; plain
    style/subject guidance never lands in a frame slot. The role comes
    from phrasing near each @-token ("X as the start frame", "end on
    Y", "from X to Y") — never from position — so an untagged ref is
    always guidance, even when it is the only ref. An explicit
    from-X-to-Y pair beats nearby window phrasing for both ends.
    """
    text = raw or ""
    lowered = text.lower()
    tokens = [
        (m.group(1).lower(), m.group(2), m.start(), m.end())
        for m in REF_TOKEN_RE.finditer(text)
    ]

    def nearest(s: int, e: int) -> str:
        best: tuple[int, str] | None = None
        lo, hi = max(0, s - 80), e + 80
        for hint in _START_HINTS:
            p = lowered.find(hint, lo, hi)
            while p != -1:
                d = 0 if p < e and p + len(hint) > s else min(abs(p - e), abs(s - (p + len(hint))))
                if best is None or d < best[0]:
                    best = (d, "start")
                p = lowered.find(hint, p + 1, hi)
        for hint in _END_HINTS:
            p = lowered.find(hint, lo, hi)
            while p != -1:
                d = 0 if p < e and p + len(hint) > s else min(abs(p - e), abs(s - (p + len(hint))))
                if best is None or d < best[0]:
                    best = (d, "end")
                p = lowered.find(hint, p + 1, hi)
        return best[1] if best else "reference"

    roles = {(kind, rid): nearest(s, e) for kind, rid, s, e in tokens}
    for m in _FROM_TO_RE.finditer(text):
        roles[(m.group(1).lower(), m.group(2))] = "start"
        roles[(m.group(3).lower(), m.group(4))] = "end"
    out = []
    for r in refs or []:
        if isinstance(r, dict):
            key = (r.get("kind"), r.get("id"))
            out.append({**r, "role": r.get("role") or roles.get(key, "reference")})
        else:
            key = (getattr(r, "kind", None), getattr(r, "id", None))
            out.append({"kind": key[0], "id": key[1], "role": roles.get(key, "reference")})
    return out


def _generation_references(raw: str, references=None, fallback_references=None):
    """Merge explicit references with @-tokens from raw text.

    No tokens are ever scope-injected: the open frame rides the turn
    as a scope line, so only phrasal reuse intent (the model may still
    cite a token it saw) attaches frame tokens from raw text.
    panel/media/gen tokens are never
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
    return _tag_reference_roles(raw, refs)


def tool_generate(
    session: Session,
    user: UserRow,
    project_id: str,
    prompt: str,
    job_factory=None,
    model: str | None = None,
    style: str = "none",
    genre: str = "none",
    negative_prompt: str = "",
    reference_frame_id: str | None = None,
    references=None,
    entity_ids=None,
    fallback_references=None,
    background: bool = False,
    width=None,
    height=None,
    attach_frame: bool = False,
    delegated: bool = False,
) -> str:
    raw = prompt or ""
    prompt = clean_prompt_text(raw)
    if not prompt:
        return "Tell me what to generate - e.g. \"generate a moonlit robot\"."
    locked = _locked_style(session, user, project_id)
    # Intent checks read the raw text: token scrubbing above removes the
    # @-references that can carry the value ("in the style of @panel:X").
    missing = []
    orient = _missing_orientation(raw, width, height)
    if orient:
        missing.append((
            "aspect ratio",
            f"the request says '{orient}' but no usable format",
            "16:9 landscape, 9:16 portrait, or 1:1 square",
        ))
    if _missing_style(raw, style, genre, locked):
        missing.append((
            "style",
            "the request names no style",
            "a style, e.g. shonen manga, photoreal, watercolor",
        ))
    if missing and not (delegated or _delegates_decisions(raw)):
        return _ask_before_generating("create_image", missing)
    refs = _generation_references(raw, references, fallback_references)
    snaps, notes = gather_reference_snapshots(session, user, project_id, prompt, refs, entity_ids)
    if locked and STYLE_TALK_RE.search(prompt or "") and style == "none" and genre == "none":
        notes.append(f"keeping locked style '{locked[0]}' — name another style to change it")
    try:
        genre_suffix(genre or "none")
    except ValueError as exc:
        return str(exc)
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
        genre=genre or "none",
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
        return f"Image job started [job {job.id}]{ref_bit} - reply now with the job id; the UI follows it live, do not poll check_job.{note_bit}"
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
    genre: str = "none",
    fallback_references=None,
    size: str | None = None,
    delegated: bool = False,
) -> str:
    """Start an async OpenRouter video job; watch it with check_job.

    Video renders silent by design (sound comes from voiceover takes
    or timeline audio); size is WxH or None for the provider default.
    """
    raw = prompt or ""
    prompt = clean_prompt_text(raw)
    if not prompt:
        return "Tell me what to render - e.g. \"generate a video of a robot walking\"."
    locked = _locked_style(session, user, project_id)
    # Intent checks read the raw text (see create_image): scrubbing
    # removes the @-references that can carry the value.
    missing = []
    dur = _missing_duration(raw, seconds, 4.0)
    if dur:
        missing.append((
            "duration",
            f"the request says '{dur}' but no length",
            "a number of seconds, e.g. 2, 4, or 8",
        ))
    elif seconds == 4.0 and parse_seconds(raw) is None:
        missing.append((
            "duration",
            "the request names no length (default 4 seconds)",
            "a number of seconds, e.g. 2, 4, or 8",
        ))
    if size is None and _parse_ratio(raw) is None:
        orient = _missing_orientation(raw, None, None)
        detail = "the request names no framing"
        if orient:
            detail = f"the request says '{orient}' but no usable format"
        missing.append((
            "ratio",
            detail,
            "16:9 landscape, 9:16 portrait, or 1:1 square",
        ))
    if size is None and _parse_video_size(raw) is None and not VIDEO_RES_RE.search(raw):
        missing.append((
            "resolution",
            "the request names no resolution",
            "provider default (cheapest), 720p, or 1080p",
        ))
    if SOUND_RE.search(raw):
        missing.append((
            "sound",
            "the request mentions sound, but the video model renders silent",
            "no model sound: a voiceover take (synthesize_voice) or timeline audio instead",
        ))
    if _missing_style(raw, "none", genre, locked):
        missing.append((
            "style",
            "the request names no style",
            "a style, e.g. anime, cinematic, photorealistic",
        ))
    if missing and not (delegated or _delegates_decisions(raw)):
        return _ask_before_generating("create_video", missing)
    refs = _generation_references(raw, references, fallback_references)
    snaps, notes = gather_reference_snapshots(session, user, project_id, prompt, refs, entity_ids)
    if locked and STYLE_TALK_RE.search(prompt or "") and genre == "none":
        notes.append(f"keeping locked style '{locked[0]}' — name another style to change it")
    try:
        genre_suffix(genre or "none")
    except ValueError as exc:
        return str(exc)
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
    dims = _parse_video_size(size or "") or _parse_video_size(raw) or (0, 0)
    job = JobRow(
        user_id=user.id,
        project_id=project_id,
        kind="video",
        prompt=prompt,
        provider=provider,
        model=model,
        seconds=seconds,
        width=dims[0],
        height=dims[1],
        attach_frame=False,
        genre=genre or "none",
        entities_json=json.dumps(snaps),
    )
    session.add(job)
    session.commit()
    threading.Thread(target=run_ai_job, args=(job.id, job_factory), daemon=True).start()
    ref_bit = f" with {len(snaps)} reference(s)" if snaps else ""
    size_bit = f" at {_size_str(dims)}" if dims != (0, 0) else " at provider-default resolution"
    note_bit = (" " + " ".join(notes)) if notes else ""
    return (
        f"Video job started [job {job.id}]{ref_bit}{size_bit}, silent by design - "
        f"reply now with the job id; the UI follows it live, do not poll check_job.{note_bit} Add sound with a voiceover take "
        f"(synthesize_voice) or timeline audio."
    )


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


def _parse_speech_line(text: str) -> str | None:
    """The line to voice: quoted text first, else text after a speech verb."""
    spans = [
        (m.start(), ((m.group(1) or m.group(2)) or "").strip())
        for m in re.finditer(r'"([^"]{1,2000})"|\'([^\']{1,2000})\'', text)
    ]
    spans = [(pos, line) for pos, line in spans if line]
    if spans:
        verb = re.search(
            r"\b(?:say|speak|narrate|voice|utter|deliver)\b", text, re.IGNORECASE
        )
        if verb:
            after = [line for pos, line in spans if pos > verb.start()]
            if after:
                return after[0]
        return spans[0][1]
    m = re.search(
        r"(?:say|speak|narrate|voice|utter|deliver)\s*:\s*(.{1,2000})$",
        text.strip(),
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip() or None
    return None


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
    lane=0,
    start_sec=0.0,
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
    try:
        lane_i = min(max(int(lane), 0), 3)
    except (TypeError, ValueError):
        lane_i = 0
    try:
        start_f = max(float(start_sec), 0.0)
    except (TypeError, ValueError):
        start_f = 0.0
    clip = add_clip(
        target.id,
        ClipCreate(kind=kind, source_id=source_id, duration=duration, motion=motion, transition=transition, transition_duration=tdur, fade_in=fi, fade_out=fo, lane=lane_i, start_sec=start_f),
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


def _vision_png(png: bytes, max_side: int = 1024) -> str:
    """Downscale raster bytes to a data URL a vision chat model accepts."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _vision_answer(question: str, images: list[str]) -> str:
    """Ask the configured vision chat model about attached images."""
    try:
        base_url, api_key, model = get_chat_config(vision=True)
    except ProviderError as exc:
        return f"Vision is not configured: {exc}"
    content: list = [{"type": "text", "text": question or "Describe this image in detail."}]
    content += [{"type": "image_url", "image_url": {"url": u}} for u in images]
    try:
        message, _usage = chat_complete_with_usage(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=[{"role": "user", "content": content}],
        )
    except ProviderError as exc:
        return f"Vision call failed: {exc}"
    except Exception as exc:
        return f"Vision call failed: {exc}"
    text = message.get("content", "") if isinstance(message, dict) else ""
    if isinstance(text, list):
        text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
    return str(text or "").strip() or "The vision model returned no text."


def _contact_sheet(pngs: list[bytes], labels: list[str], thumb: int = 320) -> bytes:
    """Tile frame thumbnails into one labeled contact sheet for one
    vision call (the harness pattern: many frames, one round trip)."""
    cells = []
    for png in pngs:
        img = Image.open(io.BytesIO(png)).convert("RGB")
        img.thumbnail((thumb, thumb))
        cells.append(img)
    cols = min(4, max(len(cells), 1))
    rows = max((len(cells) + cols - 1) // cols, 1)
    sheet = Image.new("RGB", (cols * thumb, rows * thumb), (16, 16, 20))
    drawer = ImageDraw.Draw(sheet)
    for i, img in enumerate(cells):
        x, y = (i % cols) * thumb, (i // cols) * thumb
        sheet.paste(img, (x + (thumb - img.width) // 2, y + (thumb - img.height) // 2))
        drawer.text((x + 6, y + 4), labels[i] if i < len(labels) else f"#{i + 1}")
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


def _sequence_frames(session, user, project_id, target) -> tuple:
    """SequenceRow + its frames in flipbook order, by @sequence:, id, or name."""
    from komaverse.api.animate import _resolve_sequence

    seq = None
    if target:
        m = re.search(r"@sequence:([A-Za-z0-9_-]+)", str(target))
        ident = m.group(1) if m else str(target)
        try:
            seq = _resolve_sequence(session, user, project_id, ident)
        except HTTPException:
            seq = None
    if seq is None:
        return None, f"Unknown sequence {target or ''} - use @sequence:, an id, or a name."
    frames = sorted(
        [f for f in _project_frames(session, user, project_id) if f.sequence_id == seq.id],
        key=lambda f: f.index,
    )
    return (seq, frames), ""


def _layer_lines(frame) -> list[str]:
    lines = []
    for i, layer in enumerate(getattr(frame, "layers", None) or []):
        paint = "blank" if not getattr(layer, "data_url", None) else "has paint"
        lines.append(
            f"{i + 1}. '{layer.name or 'Layer'}' "
            f"({'visible' if layer.visible else 'hidden'}, "
            f"opacity {layer.opacity}, blend {layer.blend_mode}, "
            f"{'locked' if layer.locked else 'unlocked'}, {paint})"
        )
    return lines


_DEFAULT_LOOK_QUESTION = (
    "Describe this image in detail: subjects, composition, art style, "
    "colors, and any text you can read."
)


def tool_look_at(session, user, project_id, target, question="") -> str:
    """Actually SEE a frame, panel, scan page, generation, or sequence
    with the vision model, then answer a question about it.

    target is @frame:/@panel:/@media:/@gen:/@sequence:, a bare id or
    name, or 'latest' (the newest finished image generation). Frames
    come back with their layer inventory so the model understands the
    layer stack, not just the flattened pixels; sequences come back
    as a labeled contact sheet plus per-frame layer counts.
    """
    from komaverse.api.ai import _frame_png, _job_png, _page_png, _panel_png

    q = (question or "").strip() or _DEFAULT_LOOK_QUESTION
    text = str(target or "").strip()
    m = REF_TOKEN_RE.search(text)
    kind = rid = None
    if m:
        kind, rid = m.group(1).lower(), m.group(2)
    png = None
    extra = ""
    if kind == "sequence" or (not kind and text):
        seq_target = rid if kind == "sequence" else (text or None)
        if kind == "sequence" or "/" not in (seq_target or ""):
            found, err = _sequence_frames(session, user, project_id, seq_target or "latest")
            if found is not None:
                seq, frames = found
                if not frames:
                    return f"Sequence '{seq.name or seq.id}' has no frames yet."
                use = frames[:12]
                labels = [f"{i + 1}. {f.name or 'Untitled'}" for i, f in enumerate(use)]
                sheet = _contact_sheet(
                    [p for p in (
                        _frame_png(session, project_id, f.id) for f in use
                    ) if p is not None],
                    labels,
                )
                if sheet is None:
                    return f"Could not render sequence '{seq.name or seq.id}'."
                counts = "; ".join(
                    f"{i + 1}. {f.name or 'Untitled'} ({len(f.layers)} layers)"
                    for i, f in enumerate(use)
                )
                extra = (
                    f"Sequence '{seq.name or seq.id}': {len(frames)} frames "
                    f"(showing {len(use)}): {counts}."
                    + (f" Plus {len(frames) - len(use)} more not shown." if len(frames) > len(use) else "")
                    + " Layer order inside each frame is bottom-first."
                )
                png = sheet
    if png is None and (kind == "frame" or (not kind and text)):
        f = None
        if kind == "frame":
            f = session.get(FrameRow, rid)
            if f is None or f.project_id != project_id:
                return f"Unknown frame {rid} in this project."
        elif text and text != "latest":
            resolved = _resolve_frame(session, user, project_id, text)
            if isinstance(resolved, str):
                return resolved
            f = resolved
        if f is not None:
            png = _frame_png(session, project_id, f.id)
            if png is None:
                return f"Could not render frame '{f.name or f.id}'."
            layers = _layer_lines(f)
            extra = (
                f"Frame '{f.name or f.id}' layers bottom-first: "
                + ("; ".join(layers) if layers else "no layers")
            )
    if png is None and (kind == "panel" or (not kind and text)):
        pid = rid if kind == "panel" else text
        png = _panel_png(session, project_id, pid) if pid and pid != "latest" else None
        if png is not None:
            extra = f"Panel {pid}."
    if png is None and (kind == "media" or (not kind and text)):
        pid = rid if kind == "media" else text
        png = _page_png(session, project_id, pid) if pid and pid != "latest" else None
        if png is not None:
            extra = f"Scan page {pid}."
    if png is None and (kind == "gen" or (not kind and (text in ("latest", "") or text))):
        jid = rid if kind == "gen" else None
        if jid is None:
            if text and text not in ("latest", ""):
                job = session.get(JobRow, text)
                if job is not None and job.user_id == user.id and job.project_id == project_id:
                    jid = job.id
            if jid is None:
                jid = _latest_done_image_job(session, user, project_id)
        if jid is None:
            return "No finished image generation to look at yet."
        job = session.get(JobRow, jid)
        if job is not None and job.kind == "video":
            return (
                f"@{jid} is a video - use watch_video to sample its frames "
                f"and hear what happens in it."
            )
        png = _job_png(session, user.id, project_id, jid) if jid else None
        if png is None:
            return f"Generation {jid} is not a finished image."
        extra = f"Generation '{(job.prompt or '')[:80]}'." if job else ""
    if png is None:
        return (
            "Nothing to look at - pass @frame:, @panel:, @media:, @gen:, "
            "@sequence:, an id or name, or 'latest'."
        )
    answer = _vision_answer(f"{extra} {q}".strip(), [_vision_png(png)])
    return answer


def _sample_video_frames(path: str, count: int) -> tuple[list, list, float]:
    """Evenly sampled (timestamp, JPEG) pairs plus the clip duration."""
    import cv2

    cap = cv2.VideoCapture(path)
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        dur = total / fps if fps > 0 and total > 0 else 0.0
        if dur <= 0:
            return [], [], 0.0
        times = [dur * (i + 0.5) / count for i in range(count)]
        out: list = []
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if ok2:
                out.append((t, buf.tobytes()))
    finally:
        cap.release()
    if not out:
        return [], [], 0.0
    return out, [t for t, _ in out], dur


def _video_audio_wav(path: str, max_mb: int = 24) -> bytes | None:
    """Mono 16 kHz WAV of a video file for transcription (None when the
    track is missing or too large for the one-shot cap)."""
    import subprocess

    import imageio_ffmpeg

    try:
        proc = subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-v", "error",
                "-i", path, "-vn", "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1",
            ],
            capture_output=True,
            timeout=180,
        )
    except Exception:
        return None
    data = proc.stdout or b""
    if not data or len(data) > max_mb * 1024 * 1024:
        return None
    return data


def tool_watch_video(session, user, project_id, target, question="", frames=6) -> str:
    """Watch a finished video generation: N frames sampled across its
    length go to the vision model together with a speech transcript,
    so the model apprehends the reference before generating from it.

    target is @gen:, a job id, or 'latest'. Answers timestamped, in
    order — the evidence-ledger shape video harnesses use: sample
    frames first, transcript alongside, lazy vision over both.
    """
    text = str(target or "").strip()
    m = REF_TOKEN_RE.search(text)
    jid = None
    if m and m.group(1).lower() == "gen":
        jid = m.group(2)
    elif text and text != "latest":
        job = session.get(JobRow, text)
        if (
            job is not None
            and job.user_id == user.id
            and job.project_id == project_id
            and job.kind == "video"
        ):
            jid = job.id
    if jid is None:
        rows = (
            session.execute(
                select(JobRow)
                .where(
                    JobRow.user_id == user.id,
                    JobRow.project_id == project_id,
                    JobRow.kind == "video",
                    JobRow.status == "done",
                )
                .order_by(JobRow.created_at.desc())
            )
            .scalars()
            .all()
        )
        jid = rows[0].id if rows else None
    if jid is None:
        return "No finished video to watch yet - generate one first."
    job = session.get(JobRow, jid)
    if job is None or job.status != "done" or not job.result_url:
        return f"Video {jid} is not finished yet - check it with check_job."
    try:
        rel = job.result_url.split("/media/", 1)[-1]
        vpath = str(storage.resolve(rel))
    except OSError:
        return f"Could not read video {jid}."
    try:
        n = min(max(int(frames or 6), 2), 8)
    except (TypeError, ValueError):
        n = 6
    samples, times, dur = _sample_video_frames(vpath, n)
    if not samples:
        return f"Could not sample frames from video {jid}."
    transcript = ""
    wav = _video_audio_wav(vpath)
    if wav:
        try:
            res = MetaProvider().transcribe_audio(wav, "video.wav")
            turns = [
                t for t in (res.get("turns") or [])
                if str(t.get("transcript") or "").strip()
            ]
            if turns:
                transcript = "\n".join(
                    f"[{t.get('startMs', 0) / 1000:.1f}s] {str(t.get('transcript')).strip()}"
                    for t in turns
                )
            elif str(res.get("transcript") or "").strip():
                transcript = str(res.get("transcript")).strip()
        except ProviderError as exc:
            transcript = f"(transcription unavailable: {exc})"
    else:
        transcript = "(no audio track, or audio too large to transcribe)"
    q = (question or "").strip() or "What happens in this video, in order?"
    prompt = (
        f"You are watching a {dur:.1f}s video. {len(samples)} frames sampled at "
        f"{', '.join(f'{t:.1f}s' for t in times)}. "
        f"Speech transcript with timestamps:\n{transcript or '(none)'}\n"
        f"Question: {q} Answer with what happens in order, citing timestamps like [3.2s]."
    )
    answer = _vision_answer(prompt, [_vision_png(jpg, max_side=768) for _, jpg in samples])
    label = (job.prompt or jid)[:60]
    return f"Watched '{label}' ({dur:.1f}s): {answer}"


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
        session, user.id, "transcribe", settings.meta_transcribe_model, len(res.get("transcript") or "") // 4,
        project_id=project_id or "",
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


def tool_synthesize_voice(session, user, project_id, text, voice=None, lang="en", scene=None, start_sec=0.0):
    """Generate a voiceover take from text (TTS) into the take library.

    text is the line to speak (<=2000 chars). voice is provider-shaped
    and optional (blank = Brain default): an ElevenLabs voice id, an
    `xtts:<clone-id>` local clone, or a free-tts `engine:voice` value —
    list voices in the Render tab voice settings. With scene (+ optional
    start_sec) the new take is cued on the scene timeline in one trip.
    """
    line = (text or "").strip()
    if not line:
        return "No text to speak — give me the line first."
    try:
        start = max(float(start_sec or 0.0), 0.0)
    except (TypeError, ValueError):
        start = 0.0
    cue_scene_id = None
    if scene:
        try:
            cue_scene_id = _resolve_scene(session, user, project_id, scene).id
        except HTTPException as exc:
            return str(exc.detail)
    try:
        take = synthesize_take(
            project_id,
            SynthCreate(
                text=line[:2000],
                voice=(voice or None),
                lang=(lang or "en"),
                cue_scene_id=cue_scene_id,
                cue_start_sec=start,
            ),
            session,
            user,
        )
    except HTTPException as exc:
        return str(exc.detail)
    where = f", cued at {start:g}s" if cue_scene_id else ""
    return f"Voiced {len(line)} chars as '{take.name or 'take'}' [take {take.id}]{where}."


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
    """A render request ("generate a video of ...") vs an export ("export the video").

    Mirrors _generation_kind: "make a video of X" generates, render-tab
    work ("make a video in the render tab") does not.
    """
    if _RENDER_TAB_RE.search(text or "") and not any(
        k in (text or "") for k in ("generate", "create")
    ):
        return False
    return "video" in (text or "") and (
        any(k in text for k in ("generate", "create", "make me", "draw me"))
        or bool(_MAKE_VIDEO_GEN_RE.search(text or ""))
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
    elif any(
        p in text for p in (
            "show me the history", "show history", "chat history",
            "what did we do", "what have you done", "recent actions",
            "our chat so far",
        )
    ):
        reply = run("history", tool_history, session, user, project_id, 10)
    elif any(
        p in text for p in (
            "recent jobs", "job history", "show jobs", "show me the jobs",
            "what did you generate", "past generations", "past jobs",
        )
    ):
        reply = run("recent_jobs", tool_recent_jobs, session, user, project_id, 10)
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
            size=_size_str(_parse_video_size(message)),
            delegated=_delegates_decisions(message),
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
            reply = run("create_image", tool_generate, session, user, project_id, prompt, job_factory, fallback_references=fallback_references, attach_frame=True, delegated=_delegates_decisions(message))
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
    
    elif any(k in text for k in ("look at", "take a look", "describe", "what do you see", "what is in", "what's in")) and (
        re.search(r"@(frame|panel|media|gen|sequence):", text) or any(k in text for k in ("frame", "panel", "sequence", "generation", "image", "picture"))
    ):
        reply = run("look_at", tool_look_at, session, user, project_id, message, "")
    elif any(k in text for k in ("watch", "walk me through")) and ("video" in text or "@gen:" in text) and "timeline" not in text:
        reply = run("watch_video", tool_watch_video, session, user, project_id, message, "", 6)
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
    elif any(k in text for k in ("say ", "speak", "narrate", "text to speech", "text-to-speech", "tts", "voice this", "voice the", "voiceover for", "voiceover:", 'voice "', "voice '", "voice:")) and "transcribe" not in text:
        line = _parse_speech_line(message)
        if not line:
            reply = "Which line should I voice? Quote it (e.g. voice \"we ride at dawn\") — and name a scene and I will cue it on the timeline in the same step."
        else:
            reply = run("synthesize_voice", tool_synthesize_voice, session, user, project_id, line, None, "en", _parse_scene_ref(message), parse_seconds(message) or 0.0)
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
    elif "http" in text:
        urls = [t for t in text.split() if t.startswith("http")]
        if urls:
            reply = run("web_fetch", tool_web_fetch, urls[0])
        else:
            reply = "Send a full http(s) link to fetch."
    elif any(
        k in text for k in ("images of", "pictures of", "picture of", "photos of", "photo of")
    ) or ("look like" in text and not re.search(r"@(frame|panel|media|gen|sequence):", text)):
        reply = run("web_images", tool_web_images, _parse_image_query(message))
    elif "search" in text and any(
        k in text for k in ("web", "online", "internet", "look up", "google")
    ):
        reply = run("web_search", tool_web_search, _parse_search_query(message))
    elif "tool" in text and any(k in text for k in ("list", "show", "what", "which")):
        reply = run("list_tools", tool_list_tools)
    elif "skill" in text:
        skill_name = _parse_skill_name(text)
        if skill_name:
            reply = run("read_skill", tool_read_skill, skill_name)
        else:
            reply = run("list_skills", tool_list_skills)
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
    "project, then summarize briefly. "
    "When the user names an image or video subject, call "
    "create_image/create_video ON THIS TURN — pass what the conversation "
    "states, omit the rest. Blanks take defaults; 'decide', 'go ahead' "
    "and 'whatever' mean every blank defaults. Ask a question only when "
    "no subject exists at all. "
    "Never invent IDs, counts, or URLs — "
    "only report what tools return. Earlier turns in this conversation are "
    "included before the latest message - use them as context; turns carry "
    "[date time UTC] stamps and 'Now:' states the current UTC time, so use "
    "them when asked what happened when. "
    "Greetings, thanks, and other smalltalk get a words-only reply — "
    "call no tools unless the user asks for an action, a change, or "
    "information not already in context. Async jobs (create_video, "
    "background create_image, export_video) answer immediately with the "
    "[job id] — the UI follows the job live and shows it when done. "
    "Never call check_job to wait; call it only when the user explicitly "
    "asks for a job's status. Reference images attach only "
    "when the user asked to reuse a frame, panel, scan, generation, sequence, "
    "or entity — never assume one. References are style/subject guidance "
    "unless the user names a frame role: 'as the start/first frame' opens "
    "the video, 'as the end/last frame' or 'from X to Y' closes it. Never "
    "invent what a reference shows: call look_at to see an image, frame, "
    "panel, generation, or sequence first (frames report their layer stack, "
    "sequences a contact sheet with layer counts), and call watch_video "
    "before summarizing or generating from a video - it returns timestamped "
    "frames plus speech. "
    "For anime-styled generation or direction, read the "
    "anime-animation-production skill (list_skills/read_skill) for genre "
    "tags, shot grammar, and QC: pass a genre to create_image/"
    "create_video so prompts carry the craft. For drawing advice, line "
    "art, or anime/cartoon style questions, read the "
    "drawing-fundamentals skill (construction, pen and pencil line "
    "work, anime proportions, screentone, layer discipline). "
    "For story structure, episode/chapter beats, pacing, fps and "
    "export cadence, or voice casting and line reads, read the "
    "story-pacing skill (kishotenketsu, page-turn beats, ma, "
    "animatic timing, voice direction); synthesize voiceover lines "
    "with synthesize_voice, which cues the take on the timeline. "
    "For shot and panel staging, perspective, and framing that reads "
    "instantly, read the visual-composition skill (focal hierarchy, "
    "leading lines, depth, anime-cut staging); for panel flow, "
    "reading order, and lettering/SFX placement, read manga-literacy; "
    "for studied technique from the acknowledged masters, read the "
    "masters-craft skill (manga, comics, and animation lineage, one "
    "lens per job, prompt vocabulary). "
    "Skills and refs read once per conversation: reuse what earlier "
    "turns already read or saw — never re-read a skill or re-look at "
    "a ref already in context. "
    "Research online with web_search, then read hits with web_fetch; "
    "list_tools shows every tool, list_skills shows every skill."
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
            "name": "history",
            "description": (
                "Recall recent chat turns with dates and the tool actions "
                "each ran. Use when the user asks what was done, said, or "
                "decided ('show the history', 'what did we do')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "number", "description": "Max turns back (default 10, max 20)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_jobs",
            "description": (
                "List recent operations (image/video generations, "
                "exports, transcriptions) with kind, status, date, and "
                "[job id]. Use for 'show recent jobs', 'what did you "
                "generate', past-operation questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "number", "description": "Max jobs back (default 10, max 20)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": (
                "List the studio craft skills (anime direction, manga, "
                "motion design) with one-line descriptions. Read one with "
                "read_skill before anime-styled generation or direction."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_skill",
            "description": (
                "Read one studio craft skill into context. Prefer a single "
                "## section (e.g. anime-animation-production + Prompting "
                "vocabulary) over the whole file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name from list_skills."},
                    "section": {"type": "string", "description": "Optional ## heading prefix."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tools",
            "description": (
                "List every studio tool the agent can call with one-line "
                "descriptions (generation, timeline, layers, takes, skills, "
                "web research)."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetch one http(s) page and return its text. Use for online "
                "research: official docs, references, model catalogs. "
                "The output names the page's main image ([image: url]) — "
                "paste it into your reply as a bare URL to show it. "
                "Never invent a URL; use user-supplied or fetched links."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full http(s) URL to read."},
                    "max_chars": {"type": "number", "description": "Max text chars back (default 4000)."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the open web without keys (DDG hits, instant "
                "answers, Wikipedia fallback). Use for online research, "
                "then read hits with web_fetch. Pass the natural question. "
                "When the request is about a person, character, place, or "
                "other visual subject, also call web_images so the chat "
                "shows pictures. For fictional characters, fetch the "
                "fandom.com result — its page art is the character "
                "(Wikipedia character pages carry no usable lead image)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language question or terms."},
                    "max_results": {"type": "number", "description": "Max hits per source (default 5)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_images",
            "description": (
                "Find pictures of anything (people, places, characters, "
                "art styles, objects): AniList character art, Wikipedia "
                "thumbnails, Fandom wiki art, Commons photos, plus "
                "the instant-answer image. Use when the user asks what "
                "something looks like or wants visual references. Paste "
                "the thumbnail URLs into your reply as bare https URLs, "
                "one per line, so the chat shows them; say what each "
                "shows and link its source page. Each picture has a "
                "+Media button that saves it as a @gen: reference — "
                "mention it when the user may want to reuse one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language question or terms."},
                    "max_results": {"type": "number", "description": "Max hits per source (default 5)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "frame_from_media",
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
            "description": (
                "Detect panels on every page of the project. Replies "
                "with a @panel: roster — reference those ids directly in "
                "later turns."
            ),
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
                "Generate an image from the named subject ON THIS TURN - "
                "call now, do not interview. Pass what the conversation "
                "states; omit the rest (square, standard quality). "
                "'Decide', 'go ahead' and 'whatever' mean every blank "
                "takes its default. "
                "Attach a reference only when the user asked to reuse one: "
                "pass their @frame:/@panel:/@media:/@gen:/@sequence: mentions as "
                "references, named entities as entity_ids - never paste "
                "@-tokens into the prompt, never invent references. "
                "References are style/subject guidance only: look_at a ref "
                "first when you must describe it, unless already seen in "
                "this conversation; never claim pixels you have not seen. "
                "Named third-party characters or published art are often "
                "refused on copyright grounds — after such a refusal, steer "
                "toward original characters (docs/MODEL_ERRORS.md)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Clean scene text, no @-mentions."},
                    "model": {"type": "string"},
                    "style": {"type": "string"},
                    "genre": {"type": "string"},
                    "negative_prompt": {"type": "string"},
                    "reference_frame_id": {"type": "string"},
                    "references": {"type": "array", "description": "[{kind, id}] for user-mentioned refs to reuse (frame|panel|media|gen|sequence). Style/subject guidance unless the user names a start/end frame role. Omit otherwise."},
                    "entity_ids": {"type": "array", "description": "Named entity ids to keep on-model. Omit otherwise."},
                    "width": {"type": "number", "description": "Pixels (16:9→1280x720, 9:16→720x1280, 1:1→1024x1024). Pass the conversation's format when stated; otherwise omit."},
                    "height": {"type": "number", "description": "Pixels (draft→512, standard→768, high→1024 longest side). Pass the conversation's quality when stated; otherwise omit."},
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
                "Render a video from the named subject ON THIS TURN - call "
                "now, do not interview. Pass what the conversation states "
                "(subject words, seconds, size, references); omit the "
                "rest: blanks take defaults (4s, provider-default size, "
                "silent) and 'decide', 'go ahead' and 'whatever' mean "
                "every blank defaults. The job runs in the background and "
                "the UI follows it live — answer immediately with the "
                "[job id]; never call check_job afterwards. "
                "Attach a reference only when the user asked to reuse one, "
                "via references - never paste @-tokens into the prompt. "
                "Frame roles come from the user's words, never position: "
                "'as the start/first frame' opens the video, 'as the "
                "end/last frame' or 'from X to Y' closes it, everything "
                "else is style/subject guidance and never becomes a frame. "
                "look_at an image ref or watch_video a video ref before "
                "generating from it, unless already seen in this "
                "conversation. Named third-party characters or published "
                "art are often refused on copyright grounds — after such "
                "a refusal, steer toward original characters "
                "(docs/MODEL_ERRORS.md)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Clean scene text, no @-mentions. Fold any framing words from the conversation into the scene ('wide 16:9 framing' or 'vertical 9:16 framing'); with none stated, describe the subject plainly."},
                    "seconds": {"type": "number", "description": "Seconds of video (Seedance bills per second; default 4; Hailuo 3 Max needs 5+). Pass the conversation's number when stated - digits or words ('four seconds'); otherwise omit."},
                    "size": {"type": "string", "description": "WxH (1280x720, 720x1280, 1920x1080...). Pass the conversation's value when stated; otherwise omit for the provider default (cheapest)."},
                    "generate_audio": {"type": "boolean", "description": "Always false or omitted: the video model renders silent. Dialogue and music come from synthesize_voice takes and timeline audio, never the video model."},
                    "model": {"type": "string", "description": "Video model id. Durations, sizes, and image inputs differ per model and are validated automatically (errors name the supported values): Hailuo 3 Max takes 5-15s and frames (first/last) but no style references; Wan 2.6 takes 5s/10s; Veo takes 4/6/8s."},
                    "genre": {"type": "string"},
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
            "name": "synthesize_voice",
            "description": (
                "Generate a voiceover take from text (TTS) into the take library, "
                "then optionally cue it on a scene timeline in the same call. "
                "voice is optional (blank = Brain default): an ElevenLabs voice id, "
                "an xtts:<clone-id> local clone, or a free-tts engine:voice value. "
                "Ask which voice to use when the line matters; scene cues the take at start_sec."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "voice": {"type": "string"},
                    "lang": {"type": "string"},
                    "scene": {"type": "string"},
                    "start_sec": {"type": "number"},
                },
                "required": ["text"],
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
            "name": "look_at",
            "description": (
                "Actually SEE a frame, panel, scan page, generation, or "
                "sequence with the vision model, then answer a question "
                "about it. Call this before describing or generating from "
                "a reference: never invent what an image shows. Frames "
                "arrive with their layer inventory; sequences arrive as a "
                "labeled contact sheet with per-frame layer counts. Skip "
                "refs this conversation already saw — an earlier look_at "
                "result or locked entity in context counts as seen."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "question": {"type": "string"},
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "watch_video",
            "description": (
                "Watch a finished video generation: frames sampled across "
                "its length go to the vision model together with a speech "
                "transcript, timestamped. Call this to apprehend a video "
                "reference before generating from it - never summarize a "
                "video from its prompt text alone. Skip videos this "
                "conversation already watched."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "question": {"type": "string"},
                    "frames": {"type": "integer"},
                },
                "required": ["target"],
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
                "Report one job's status and result URL. Only for explicit "
                "user status questions ('is it done yet?') — never call "
                "this after create_video: answer with the [job id] and the "
                "UI follows the job live."
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
                "Stop a queued or running image/video/export job by its id. "
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


def _stub_skill_bodies(messages, skill_bodies, current_id=None):
    """Collapse older read_skill payloads to pointers, in place.

    A 36KB skill re-sent on every loop step is what makes skill-heavy
    turns crawl; the model keeps the latest full body and pointers to
    the rest. Returns the stubbed count.
    """
    stubbed = 0
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "tool":
            continue
        cid = m.get("tool_call_id")
        if cid in skill_bodies and cid != current_id and len(str(m.get("content") or "")) > 200:
            m["content"] = (
                f"[Skill '{skill_bodies[cid]}' read above — use that "
                "copy, do not re-read it.]"
            )
            stubbed += 1
    return stubbed


def _exec_cloud_tool(
    name, args, *, message, session, user, project_id, job_factory,
    turn_references=None, skills_read=None,
) -> str:
    """Run one model-requested tool; shared by sync turns and streams.

    skills_read is the turn's set of (skill, section) pairs: a repeat
    read answers with a pointer instead of re-sending the whole file.
    """
    try:
        if name == "project_status":
            result = tool_status(session, user, project_id)
        elif name == "history":
            result = tool_history(
                session, user, args.get("project_id") or project_id, args.get("limit")
            )
        elif name == "recent_jobs":
            result = tool_recent_jobs(
                session, user, args.get("project_id") or project_id, args.get("limit")
            )
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
                genre=str(args.get("genre") or "none"),
                negative_prompt=str(args.get("negative_prompt") or ""),
                reference_frame_id=args.get("reference_frame_id"),
                references=args.get("references"),
                entity_ids=args.get("entity_ids"),
                fallback_references=turn_references,
                width=args.get("width"), height=args.get("height"),
                attach_frame=bool(args.get("attach_frame")),
                delegated=_delegates_decisions(message),
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
                str(args.get("genre") or "none"),
                fallback_references=turn_references,
                size=args.get("size") if isinstance(args.get("size"), str) else None,
                delegated=_delegates_decisions(message),
            )
            if args.get("generate_audio"):
                result += " (Model sound stays off even when asked: the video renders silent.)"
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
        elif name == "synthesize_voice":
            result = tool_synthesize_voice(
                session, user, project_id,
                str(args.get("text") or ""),
                args.get("voice"), args.get("lang"),
                args.get("scene"), args.get("start_sec"),
            )
        elif name == "frame_from_media":
            result = tool_frame_from_media(
                session, user, project_id,
                str(args.get("source") or message),
                args.get("name"), args.get("sequence"),
            )
        elif name == "look_at":
            result = tool_look_at(
                session, user, project_id,
                str(args.get("target") or message),
                str(args.get("question") or ""),
            )
        elif name == "watch_video":
            result = tool_watch_video(
                session, user, project_id,
                str(args.get("target") or message),
                str(args.get("question") or ""),
                args.get("frames", 6),
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
        elif name == "list_skills":
            result = tool_list_skills()
        elif name == "read_skill":
            skill_key = (str(args.get("name") or ""), str(args.get("section") or ""))
            if skills_read is not None and skill_key in skills_read:
                result = (
                    f"Skill '{skill_key[0]}' is already in context above — "
                    "use that copy, do not re-read it."
                )
            else:
                if skills_read is not None:
                    skills_read.add(skill_key)
                result = tool_read_skill(
                    str(args.get("name") or ""), args.get("section")
                )
        elif name == "list_tools":
            result = tool_list_tools()
        elif name == "web_fetch":
            result = tool_web_fetch(
                str(args.get("url") or ""), args.get("max_chars")
            )
        elif name == "web_search":
            result = tool_web_search(
                str(args.get("query") or ""), args.get("max_results")
            )
        elif name == "web_images":
            result = tool_web_images(
                str(args.get("query") or message), args.get("max_results")
            )
        else:
            result = f"unknown tool: {name}"
    except HTTPException as exc:
        result = f"error {exc.status_code}: {exc.detail}"
    return result


def _job_visible(session, user, job_id) -> bool:
    """True when the id names a real job of this user.

    Guards the follow-up pollers (and the dock) against placeholder
    tokens like [job id] that the model sometimes echoes from tool
    descriptions instead of a real id: only existing, owned jobs are
    ever polled.
    """
    try:
        row = session.get(JobRow, job_id)
    except Exception:
        return False
    return row is not None and row.user_id == user.id


CLOUD_MAX_STEPS = 12
"""Model rounds per cloud turn: async jobs answer immediately (the UI
follows [job id] live), so polling check_job must never be needed."""


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
    now_line = f"\nNow: {_fmt_when(datetime.now(timezone.utc))}"
    user_content = f"Project: {context}{frame_line}{now_line}\n\n{message}"
    if ref_context:
        user_content = (
            f"Project: {context}{frame_line}{now_line}\n\nReferenced:\n{ref_context}\n\n{message}"
        )
    past = []
    for h in (history or [])[-8:]:
        stamp = str(h.get("at") or "").replace("T", " ")[:16]
        prefix = f"[{stamp}] " if stamp else ""
        past.append(
            {
                "role": "user" if h.get("role") == "user" else "assistant",
                "content": prefix + str(h.get("text") or "")[:600],
            }
        )
    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM},
        *past,
        {"role": "user", "content": user_content},
    ]
    actions: list[dict] = []
    base_url, api_key, model = get_chat_config()
    total_tokens = 0
    skills_read: set[tuple[str, str]] = set()
    skill_bodies: dict[str, str] = {}
    for _ in range(CLOUD_MAX_STEPS):
        # Older skill bodies collapse to pointers; the newest full body
        # stays for the step about to read it.
        _stub_skill_bodies(messages, skill_bodies, current_id=list(skill_bodies)[-1] if skill_bodies else None)
        msg, usage, tokens = step(messages, base_url, api_key, model)
        for text in tokens:
            yield {"type": "token", "text": text}
        total_tokens += usage.get("total", 0)
        calls = msg.get("tool_calls") or []
        # finish_reason rides the message dict from the providers (stream
        # and batch); strip it before the message re-enters history.
        finish_reason = msg.pop("finish_reason", None) if isinstance(msg, dict) else None
        if not calls:
            reply_text = msg.get("content") or "Done."
            if finish_reason == "length":
                reply_text += (
                    "\n\n[The model hit its response limit \u2014 say 'continue' "
                    "and I'll pick up where I left off.]"
                )
            record_usage(session, user.id, "chat", model, total_tokens or (len(message) + len(reply_text)) // 4, project_id=project_id or "")
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
                turn_references=turn_references, skills_read=skills_read,
            )
            summary = result[:200]
            actions.append({"tool": name, "summary": summary})
            found_ids = re.findall(r"\[job ([A-Za-z0-9_-]+)\]", result)
            yield {
                "type": "tool_result",
                "tool": name,
                "summary": summary,
                "job_ids": [jid for jid in found_ids if _job_visible(session, user, jid)],
            }
            if name == "read_skill":
                skill_bodies[call["id"]] = str(args.get("name") or "skill")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    # Skill bodies ride whole (once — repeats and older
                    # ones are stubbed); everything else stays capped.
                    "content": result if name == "read_skill" else result[:2000],
                }
            )
    record_usage(session, user.id, "chat", model, total_tokens or (len(message) + 40) // 4, project_id=project_id or "")
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
                record_usage(session, user.id, "chat", "demo", (len(message) + len(summary)) // 4, project_id=created or "")
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
    current = None
    frame_named_id: str | None = None
    # Tokens/refs the user actually typed or picked. The open frame is
    # knowledge (the scope line below), never an implicit token: deictic
    # "this/that" resolves against these, never against injected scope.
    typed_tokens = REF_TOKEN_RE.findall(message or "")
    picked_refs = list(refs or [])
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
            # A frame the user names outright resolves to its token. The
            # open frame itself is never injected: it stays knowledge via
            # the scope line, so the model cannot echo a phantom frame
            # into generation prompts.
            named = [
                f for f in frames
                if f.name and len(f.name) > 2 and f.name.lower() in message.lower()
            ]
            if len(named) == 1:
                frame_named_id = named[0].id
                message = f"{message} @frame:{frame_named_id}"
    typed_has_frame = any(kind == "frame" for kind, _ in typed_tokens)
    picked_has_frame = any(
        (r.get("kind") if isinstance(r, dict) else r.kind) == "frame"
        for r in picked_refs
    )
    frame_ok = typed_has_frame or picked_has_frame or frame_named_id is not None
    if not frame_ok and (
        current_frame_id
        and current is not None
        and FRAME_DIRECTED_RE.search(message or "")
    ):
        # The open frame becomes reference pixels only when the request
        # names it (typed, picked, or named outright) or operates on it
        # (trace, redraw, paint over, "on the current frame"...). Mere
        # knowledge of the frame never attaches it, so generations about
        # anything else stay clean.
        refs = [*(refs or []), {"kind": "frame", "id": current_frame_id}]
        frame_ok = True
    if frame_named_id is not None and not any(
        (r.get("kind") if isinstance(r, dict) else r.kind) == "frame"
        and (r.get("id") if isinstance(r, dict) else r.id) == frame_named_id
        for r in refs or []
    ):
        refs = [*(refs or []), {"kind": "frame", "id": frame_named_id}]
        frame_ok = True
    gen_ok = (
        any(kind == "gen" for kind, _ in typed_tokens)
        or any(
            (r.get("kind") if isinstance(r, dict) else r.kind) == "gen"
            for r in picked_refs
        )
    )
    for kind, rid in typed_tokens:
        if not any(
            (r.get("kind") if isinstance(r, dict) else r.kind) == kind
            and (r.get("id") if isinstance(r, dict) else r.id) == rid
            for r in refs or []
        ):
            refs = [*(refs or []), {"kind": kind, "id": rid}]
    if (
        target
        and wants_reference(message)
        and not typed_tokens
        and not picked_refs
    ):
        # "use this character" after generating one: with no id in play
        # the turn would hand the model nothing it can cite, so it falls
        # back to the frame. Attach the latest finished image generation
        # in this project; the snapshot lookup re-checks ownership and
        # scope before any pixel is used.
        last_gen = _latest_done_image_job(session, user, target)
        if last_gen is not None:
            message = f"{message} @gen:{last_gen}"
            refs = [{"kind": "gen", "id": last_gen}, *(refs or [])]
            gen_ok = True
    if (
        target
        and wants_reference(message)
        and not typed_tokens
        and not picked_refs
        and not frame_ok
        and not gen_ok
        and re.search(r"\b(this|that|it|them)\b", message or "", re.IGNORECASE)
    ):
        # Reference intent with no resolvable referent: asking one short
        # question beats burning a generation on the wrong pixels.
        frame_bit = (
            f"the open frame '{current.name or 'Untitled'}'"
            if current is not None
            else "the open frame"
        )
        return {"early": {
            "reply": (
                f"Which reference should I use — {frame_bit}, or a specific "
                "one? Paste it inline like @gen:<id> (type @ to list studio "
                "references), or name the frame, and I'll generate from that."
            ),
            "project_id": target,
            "project_name": schema.name,
            "actions": [],
            "brain": "demo",
        }}
    if target:
        gen_kind = _generation_kind(message)
        # Canvas paint ops ("draw a red circle") route to manage_layers,
        # not to media generation — never interrogate those.
        if gen_kind and not _wants_draw_shape((message or "").lower()):
            # Cost-relevant dimensions are always confirmed when blank:
            # format + quality for images, duration + framing + resolution
            # + sound for video (sound defaults off: voiceover/timeline).
            # A usable value in the text skips its question; "go" takes
            # the stated defaults (today's silent behavior), so the
            # answer turn needs no new machinery — the model reads the
            # thread and passes the resolved width/height/seconds.
            missing = []
            go: list[str] = []
            if gen_kind == "image":
                if provider_for("image") != "meta":
                    if _parse_ratio(message or "") is None:
                        orient = _missing_orientation(message, None, None)
                        bit = (
                            "aspect ratio — 16:9 landscape, "
                            "9:16 portrait, or 1:1 square"
                        )
                        if orient:
                            bit += f" (you said '{orient}')"
                        missing.append(bit)
                        if orient and _WIDE_HINT_RE.search(orient):
                            go.append("16:9 landscape")
                        elif orient and _TALL_HINT_RE.search(orient):
                            go.append("9:16 portrait")
                        elif _WIDE_HINT_RE.search(message or ""):
                            go.append("16:9 landscape")
                        elif _TALL_HINT_RE.search(message or ""):
                            go.append("9:16 portrait")
                        else:
                            go.append("1:1 square")
                    if not QUALITY_RE.search(message or ""):
                        missing.append(
                            "quality — Draft (512px, fastest), "
                            "Standard (768px), or High (1024px)"
                        )
                        go.append("High quality")
            else:
                if parse_seconds(message or "") is None:
                    dur = _missing_duration(message, 4.0)
                    bit = "duration in seconds (e.g. 2, 4, or 8)"
                    if dur:
                        bit += f" (you said '{dur}')"
                    missing.append(bit)
                    go.append("4 seconds")
                if not _composition_hint(message or ""):
                    missing.append("framing — landscape 16:9 or portrait 9:16")
                    go.append("default framing")
                if _parse_video_size(message or "") is None and not VIDEO_RES_RE.search(message or ""):
                    missing.append("resolution — provider default (cheapest), 720p, or 1080p")
                    go.append("provider default")
                if SOUND_RE.search(message or ""):
                    missing.append("sound — the video model renders silent, so say 'no model sound' and use a voiceover take or timeline audio instead")
                    go.append("no model sound")
            locked = _locked_style(session, user, target)
            if _missing_style(message, "none", "none", locked):
                missing.append(
                    "art style (e.g. shonen manga, photoreal, watercolor)"
                )
            if missing and not _delegates_decisions(message):
                # References resolved above ride the question: the early
                # return drops message/refs, so without their names in the
                # reply the next turn could not cite them back.
                using: list[str] = []
                for r in refs or []:
                    kind = r.get("kind") if isinstance(r, dict) else getattr(r, "kind", None)
                    rid = r.get("id") if isinstance(r, dict) else getattr(r, "id", None)
                    if kind == "gen" and rid:
                        using.append(f"@gen:{rid}")
                    elif kind == "frame" and rid:
                        using.append("the open frame")
                tiny = len(message.split()) <= 4
                lead = "What should I generate, and which " if tiny else "Which "
                if using:
                    lead = f"Using {', '.join(using)} — {lead[0].lower()}{lead[1:]}"
                if go:
                    tail = (
                        "Answer with the specifics, or say 'go' for "
                        f"{', '.join(go)} and I'll generate it."
                    )
                else:
                    tail = "Answer with the specifics and I'll generate it."
                gen_ids = [u for u in using if u.startswith("@gen:")]
                if gen_ids:
                    tail += (
                        f" Keep {', '.join(gen_ids)} in your reply "
                        "so I reuse it."
                    )
                return {"early": {
                    "reply": f"{lead}{' and which '.join(missing)}? {tail}",
                    "project_id": target,
                    "project_name": schema.name,
                    "actions": [],
                    "brain": "demo",
                }}
    ref_context = resolve_refs(refs, session, user) if refs else ""
    return {"message": message, "target": target, "refs": refs, "open_frame": open_frame, "ref_context": ref_context}


#: Render-tab work: cutting, arranging, previewing or exporting the
#: timeline — never new media, even when the words "make a video" appear.
_RENDER_TAB_RE = re.compile(
    r"\b(render(\s+tab)?|timeline|export|preview|trim(?:ming)?|cut(?:ting)?|arrang\w+)\b",
    re.IGNORECASE,
)
#: "make a video of X" generates media; "make the video longer" edits it.
_MAKE_VIDEO_GEN_RE = re.compile(
    r"\bmake\s+(?:me\s+)?a\s+video\s+(of|about|featuring|with|showing|starring|where|in\s+which)\b",
    re.IGNORECASE,
)
_EDIT_VIDEO_RE = re.compile(r"\bmake\s+(the|this|that|my)\s+video\b", re.IGNORECASE)


def _generation_kind(message: str | None) -> str | None:
    """Image/video generation intent, or None.

    Conservative on purpose: draw/project/timeline ops are not
    generations, and the parameter-dimension check is the real guard —
    this only picks which dimensions to test. Making a video in the
    render tab (cutting, arranging, exporting) is timeline work, while
    "make a video of X" is media generation.
    """
    lowered = (message or "").lower()
    if "video" in lowered and (
        any(k in lowered for k in ("generate", "create", "make me", "draw me"))
        or _MAKE_VIDEO_GEN_RE.search(message or "")
    ):
        # Explicit generate/create verbs mean new media even when the
        # render tab is mentioned; otherwise render-tab words win.
        if _RENDER_TAB_RE.search(message or "") and not any(
            k in lowered for k in ("generate", "create")
        ):
            return None
        return "video"
    if _EDIT_VIDEO_RE.search(message or ""):
        return None
    if re.search(r"\b(generate|generating|create|creating|make|making|draw|paint)\b", lowered) and not re.search(
        r"\b(project|clip|subtitle|cue|frame|layer|sequence|timeline|render|scene|export|preview)\b", lowered
    ):
        return "image"
    return None

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
    record_usage(session, user.id, "chat", "demo", (len(message) + len(reply)) // 4, project_id=target or "")
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
    record_usage(session, user.id, "chat", "demo", (len(message) + len(reply)) // 4, project_id=target or "")
    return _finish_turn(target, session, user, reply, actions, "demo", cloud_error)

