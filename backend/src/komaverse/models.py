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
    crop_url: str | None = None


class Page(BaseModel):
    id: str = Field(default_factory=uuid_short)
    project_id: str
    index: int = 0
    image_url: str
    width: int = 0
    height: int = 0
    panels: list[Panel] = Field(default_factory=list)


class DrawLayer(BaseModel):
    id: str = Field(default_factory=uuid_short)
    name: str = "Layer"
    opacity: int = 100  # 0-100
    visible: bool = True
    locked: bool = False
    blend_mode: str = "normal"
    layer_order: int = 0
    data_url: str | None = None


class DrawFrame(BaseModel):
    id: str = Field(default_factory=uuid_short)
    name: str = ""
    width: int = 1920
    height: int = 1080
    layers: list[DrawLayer] = Field(default_factory=list)
    thumbnail: str | None = None
    sequence_id: str | None = None


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
    kind: Literal["panel", "frame", "media", "video", "ai"] = "panel"
    source_id: str  # panel id, frame id, or media/asset id
    start: float = 0.0  # seconds on timeline
    duration: float = 2.0
    motion: str = "none"  # none | kenburns-* | ai
    order: int = 0


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
    style: str = "none"
    camera_move: str = "none"
    enhance: bool = False
    reference_frame_id: str | None = None
    final_prompt: str = ""  # exact text sent after entities/style/camera/negative
    entities_json: str = "[]"  # snapshot [{name, kind}] for the UI
    status: Literal["queued", "running", "done", "failed"] = "queued"
    progress: float = 0.0
    result_url: str | None = None
    error: str | None = None


class Entity(BaseModel):
    """A named consistency anchor: character, field (setting), or prop."""

    id: str = Field(default_factory=uuid_short)
    project_id: str
    kind: str = "character"  # character | field | prop
    name: str = ""
    anchor: str = ""  # stable description reused in every prompt
    frame_id: str | None = None  # optional visual snapshot (draw frame)


class CreditEntry(BaseModel):
    id: str = Field(default_factory=uuid_short)
    user_id: str
    delta: int  # +grant, -spend
    reason: str
    job_id: str | None = None
