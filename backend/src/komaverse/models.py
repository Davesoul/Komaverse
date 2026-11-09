"""Komaverse unified domain models (Pydantic = source of truth for shapes).

Project ─┬─ Page[] ─── Panel[]   (scan side)
         ├─ Scene[] ─── Clip[]    (timeline side; clips reference panels/frames/media)
         └─ Frame[] ─── Layer[]   (draw side)

M2 adds SQLAlchemy tables mirroring these + Alembic; the API never
exposes ORM objects.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


def uuid_short() -> str:
    return uuid.uuid4().hex[:12]


class Panel(BaseModel):
    id: str = Field(default_factory=uuid_short)
    page_id: str
    x: float = 0
    y: float = 0
    w: float = 0
    h: float = 0
    reading_order: int = 0
    name: str = ""
    dialogue: str = ""
    sfx: str = ""
    cues: str = "[]"
    crop_url: str | None = None


class Page(BaseModel):
    id: str = Field(default_factory=uuid_short)
    project_id: str
    index: int = 0
    image_url: str
    width: int = 0
    height: int = 0
    name: str = ""
    panels: list[Panel] = Field(default_factory=list)


class StoryboardShot(BaseModel):
    """One auto-direct shot previewed before anything is stored: the
    panel in send order plus the dialogue already on it and the motion
    the chosen camera preset assigns it."""

    panel_id: str
    page_id: str
    page_index: int = 0
    reading_order: int = 0
    dialogue: str = ""
    sfx: str = ""
    motion: str = "none"
    duration: float = 2.0


class DrawLayer(BaseModel):
    id: str = Field(default_factory=uuid_short)
    name: str = "Layer"
    opacity: int = 100  # 0-100
    visible: bool = True
    locked: bool = False
    blend_mode: str = "normal"
    layer_order: int = 0
    data_url: str | None = None
    # M3 vector layers: "raster" paints pixels into data_url; "vector"
    # stores editable SVG path data in vector_data (JSON list of
    # {d, stroke, stroke_width, fill}). Vector layers also carry a baked
    # data_url so thumbnails/exports keep compositing unchanged.
    kind: Literal["raster", "vector"] = "raster"
    vector_data: str | None = None


class Guide(BaseModel):
    """One drawing guide overlay (rule-of-thirds, center, diagonal...)."""

    id: str = Field(default_factory=uuid_short)
    kind: str = "thirds"  # thirds | center | diagonal | perspective
    visible: bool = True
    color: str = "#3b82f6"
    opacity: int = 50  # 0-100


class DrawFrame(BaseModel):
    id: str = Field(default_factory=uuid_short)
    name: str = ""
    width: int = 1920
    height: int = 1080
    layers: list[DrawLayer] = Field(default_factory=list)
    thumbnail: str | None = None
    sequence_id: str | None = None
    guides_visible: bool = False
    guides: list[Guide] = Field(default_factory=list)


class SequenceFrame(BaseModel):
    """Frame metadata inside a sequence (no pixels — Media lists stay light)."""

    id: str = Field(default_factory=uuid_short)
    name: str = ""
    width: int = 1920
    height: int = 1080
    index: int = 0
    layer_count: int = 0


class Sequence(BaseModel):
    id: str = Field(default_factory=uuid_short)
    project_id: str
    name: str = "Sequence"
    index: int = 0
    frames: list[SequenceFrame] = Field(default_factory=list)


class Clip(BaseModel):
    id: str = Field(default_factory=uuid_short)
    scene_id: str
    kind: Literal["panel", "frame", "media", "video", "ai", "text"] = "panel"
    source_id: str  # panel id, frame id, or media/asset id ("" for text)
    start: float = 0.0  # seconds on timeline
    duration: float = 2.0
    # Seconds skipped at the source head (video only): head-trimming a
    # video clip grows this and shrinks duration instead of moving it.
    offset: float = 0.0
    motion: str = "none"  # none | zoom-* | pan-* | drift
    transition: str = "none"  # into this clip: none | crossfade | fade-black | fade-white | wipe-left | wipe-right | push-left | impact
    transition_duration: float = 0.5
    fade_in: float = 0.0
    fade_out: float = 0.0
    order: int = 0
    lane: int = 0  # 0 = V1 sequential base; >=1 = fullscreen overlay V{lane+1} placed at start
    # Text title cards (kind == "text" only): full-frame card rendered by
    # the backend, CapCut-style.
    text: str = ""
    text_size: int = 64
    text_color: str = "#ffffff"
    text_position: str = "center"  # top | center | bottom
    text_anim: str = "none"  # none | fade | rise
    # Color grade burned into the export (and previewed via CSS).
    filter: str = "none"  # none | grayscale | sepia | vintage | vivid


class ClonedVoice(BaseModel):
    """A voice clone owned by one user (ElevenLabs cloud or local XTTS)."""

    id: str = Field(default_factory=uuid_short)
    name: str = ""
    voice_id: str = ""  # ElevenLabs voice_id (usable as a TTS voice)
    engine: str = "elevenlabs"  # elevenlabs | xtts
    sample_count: int = 0
    created_at: str | None = None


class VisemeFrame(BaseModel):
    """One coarse mouth-shape frame (M2 lip-sync, offline envelope)."""

    start_sec: float = 0.0
    end_sec: float = 0.0
    viseme: str = "closed"  # closed | narrow | open | wide
    openness: float = 0.0  # 0..1 mouth openness


class AudioTake(BaseModel):
    """One library recording: mic take or uploaded file (metadata only)."""

    id: str = Field(default_factory=uuid_short)
    project_id: str
    name: str = ""
    url: str
    mime: str = ""
    duration_sec: float = 0.0
    created_at: str | None = None  # ISO timestamp; serialized so the
    # library can sort takes newest-first (column already on AudioTakeRow)
    lipsync: list[VisemeFrame] = Field(default_factory=list)


class AudioCue(BaseModel):
    """One timeline placement of a take inside a scene."""

    id: str = Field(default_factory=uuid_short)
    scene_id: str
    audio_id: str
    start_sec: float = 0.0  # scene-timeline position
    offset_sec: float = 0.0  # skip into the take
    end_sec: float | None = None  # stop early on the timeline; null = take end
    lane: int = 0  # audio lane (A1, A2, ...) on the timeline
    volume: float = 1.0  # 0..2 gain
    fade_in: float = 0.0
    fade_out: float = 0.0
    rate: float = 1.0  # playback speed; 2.0 = twice as fast
    speaker: str = ""  # cast member or narrator name; "" = no speaker
    text: str = ""  # dialogue line for speak + captions; "" = none
    lipsync: list[VisemeFrame] = Field(default_factory=list)


class SubtitleSegment(BaseModel):
    """One parsed caption (seconds on the scene timeline)."""

    start_sec: float = 0.0
    end_sec: float = 0.0
    text: str = ""


class SubtitleTrack(BaseModel):
    """One SRT track per scene, with parsed segments for the timeline."""

    id: str = Field(default_factory=uuid_short)
    scene_id: str
    lang: str = "en"
    lane: int = 0  # 0 = S1 bottom captions; 1 = S2 top captions
    segments: list[SubtitleSegment] = Field(default_factory=list)


class Scene(BaseModel):
    id: str = Field(default_factory=uuid_short)
    project_id: str
    name: str = "Scene"
    order: int = 0
    clips: list[Clip] = Field(default_factory=list)
    cues: list[AudioCue] = Field(default_factory=list)


class Project(BaseModel):
    id: str = Field(default_factory=uuid_short)
    user_id: str
    name: str = "Untitled"
    fps: int = 24
    canvas_width: int = 1920
    canvas_height: int = 1080
    pages: list[Page] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    frames: list[DrawFrame] = Field(default_factory=list)
    sequences: list[Sequence] = Field(default_factory=list)
    audios: list[AudioTake] = Field(default_factory=list)


class ProjectSummary(BaseModel):
    """Lightweight project header for lists and tab bootstraps.

    Counts let tabs render instantly; each tab then fetches only its own
    slice (pages, timeline, …) instead of the full tree with layer pixels.
    """

    id: str = Field(default_factory=uuid_short)
    user_id: str
    name: str = "Untitled"
    fps: int = 24
    canvas_width: int = 1920
    canvas_height: int = 1080
    page_count: int = 0
    scene_count: int = 0
    frame_count: int = 0
    sequence_count: int = 0
    take_count: int = 0


class ProjectView(ProjectSummary):
    """One-round-trip tab bootstrap: header counts plus the pages slice."""

    pages: list[Page] = Field(default_factory=list)


class ExportReport(BaseModel):
    """What an export job actually produced (SPEC §Export)."""

    job_id: str
    url: str
    warnings: list[str] = Field(default_factory=list)
    rendered_sec: float = 0.0
    clips_rendered: int = 0
    fps: int = 24
    width: int = 1280
    height: int = 720
    audio_cues: int = 0
    subtitles_vtt_url: str | None = None
    lipsync_url: str | None = None


class Job(BaseModel):
    id: str = Field(default_factory=uuid_short)
    user_id: str
    project_id: str | None = None
    kind: str  # image | video (openrouter only) | export
    model: str | None = None  # vendor model id (openrouter jobs); None = server default
    prompt: str = ""  # generation prompt (image jobs); serialized so the
    # library can caption and search generations without a schema migration
    # (the column already exists on JobRow)
    seed: int | None = None  # seed sent to the provider (random when unset)
    negative_prompt: str = ""
    cost_credits: int = 0  # debited at creation (0 when free)
    used_own_key: bool = False  # ran on the user's own provider key: studio paid nothing
    style: str = "none"
    genre: str = "none"  # GENRE_TAGS key (anime direction grammar)
    camera_move: str = "none"
    enhance: bool = False
    reference_frame_id: str | None = None
    layer_frame_id: str | None = None  # paint target: finished image lands as a layer on this frame
    final_prompt: str = ""  # exact text sent after entities/genre/style/camera/negative
    entities_json: str = "[]"  # snapshot [{name, kind}] for the UI
    status: Literal["queued", "running", "done", "failed", "cancelled"] = "queued"
    progress: float = 0.0
    result_url: str | None = None
    error: str | None = None
    seconds: float = 0.0


class Entity(BaseModel):
    """A named consistency anchor: character, field (setting), or prop."""

    id: str = Field(default_factory=uuid_short)
    project_id: str
    kind: str = "character"  # character | field | prop
    name: str = ""
    anchor: str = ""  # stable description reused in every prompt
    frame_id: str | None = None  # optional visual snapshot (draw frame)
    voice: str = ""  # TTS voice id for this cast member; "" = Brain default


class CreditEntry(BaseModel):
    id: str = Field(default_factory=uuid_short)
    user_id: str
    delta: int  # +grant, -spend
    reason: str
    job_id: str | None = None


class Purchase(BaseModel):
    id: str = Field(default_factory=uuid_short)
    amount_usd_cents: int
    credits: int
    status: str  # pending | done | expired
    created_at: str = ""


class PortalPack(BaseModel):
    id: str  # topup-5 | topup-10 | topup-25
    usd_cents: int
    credits: int


class PortalPlan(BaseModel):
    id: str  # hobby | studio | atelier
    name: str
    usd_cents: int
    credits: int  # granted every billing month while active
    blurb: str


class Subscription(BaseModel):
    plan_id: str
    status: str  # active | canceled
    renews: str = ""  # ISO current_period_end, "" when unknown


class PortalOut(BaseModel):
    balance: int
    free_monthly_credits: int
    free_remaining: int  # grant not yet taken this month (0 when taken/disabled)
    paywall_enabled: bool
    publishable_key: str | None  # None = top-ups unavailable, freemium only
    packs: list[PortalPack]
    costs: dict[str, int]  # credits per action (video = per second)
    purchases: list[Purchase]
    plans: list[PortalPlan] = []
    subscription: Subscription | None = None
