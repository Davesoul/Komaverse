"""SQLAlchemy tables mirroring the Pydantic models.

API responses always use the Pydantic schemas, never these rows.
Foreign keys and lookup columns are indexed (perf); emails/token hashes unique.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from komaverse import models as schemas
from komaverse.models import uuid_short


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    sessions: Mapped[list[SessionRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    user: Mapped[UserRow] = relationship(back_populates="sessions")


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="local")
    name: Mapped[str] = mapped_column(String(200), default="Untitled")
    fps: Mapped[int] = mapped_column(default=24)
    canvas_width: Mapped[int] = mapped_column(default=1920)
    canvas_height: Mapped[int] = mapped_column(default=1080)

    pages: Mapped[list[PageRow]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="PageRow.index",
        lazy="selectin",
    )
    scenes: Mapped[list[SceneRow]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="SceneRow.order",
        lazy="selectin",
    )
    frames: Mapped[list[FrameRow]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="FrameRow.index",
        lazy="selectin",
    )
    audios: Mapped[list[AudioTakeRow]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="AudioTakeRow.created_at",
        lazy="selectin",
    )

    def to_schema(self) -> schemas.Project:
        return schemas.Project(
            id=self.id,
            user_id=self.user_id,
            name=self.name,
            fps=self.fps,
            canvas_width=self.canvas_width,
            canvas_height=self.canvas_height,
            pages=[p.to_schema() for p in self.pages],
            scenes=[s.to_schema() for s in self.scenes],
            frames=[f.to_schema() for f in self.frames],
            audios=[a.to_schema() for a in self.audios],
        )


class PageRow(Base):
    __tablename__ = "pages"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    index: Mapped[int] = mapped_column(default=0)
    image_path: Mapped[str] = mapped_column(String(500))  # relative under media_dir
    image_url: Mapped[str] = mapped_column(String(500))
    width: Mapped[int] = mapped_column(default=0)
    height: Mapped[int] = mapped_column(default=0)

    project: Mapped[ProjectRow] = relationship(back_populates="pages")
    panels: Mapped[list[PanelRow]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        order_by="PanelRow.reading_order",
        lazy="selectin",
    )

    def to_schema(self) -> schemas.Page:
        return schemas.Page(
            id=self.id,
            project_id=self.project_id,
            index=self.index,
            image_url=self.image_url,
            width=self.width,
            height=self.height,
            panels=[p.to_schema() for p in self.panels],
        )


class PanelRow(Base):
    __tablename__ = "panels"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    page_id: Mapped[str] = mapped_column(ForeignKey("pages.id"), index=True)
    x: Mapped[float] = mapped_column(default=0)
    y: Mapped[float] = mapped_column(default=0)
    w: Mapped[float] = mapped_column(default=0)
    h: Mapped[float] = mapped_column(default=0)
    reading_order: Mapped[int] = mapped_column(default=0)

    page: Mapped[PageRow] = relationship(back_populates="panels")

    def to_schema(self) -> schemas.Panel:
        return schemas.Panel(
            id=self.id,
            page_id=self.page_id,
            x=self.x,
            y=self.y,
            w=self.w,
            h=self.h,
            reading_order=self.reading_order,
            crop_url=f"/api/v1/panels/{self.id}/preview",
        )


class SceneRow(Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="Scene")
    order: Mapped[int] = mapped_column(default=0)

    project: Mapped[ProjectRow] = relationship(back_populates="scenes")
    clips: Mapped[list[ClipRow]] = relationship(
        back_populates="scene",
        cascade="all, delete-orphan",
        order_by="ClipRow.order",
        lazy="selectin",
    )
    cues: Mapped[list[AudioCueRow]] = relationship(
        back_populates="scene",
        cascade="all, delete-orphan",
        order_by="AudioCueRow.start_sec",
        lazy="selectin",
    )
    subtitle_track: Mapped["SubtitleTrackRow | None"] = relationship(
        back_populates="scene",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=False,
    )

    def to_schema(self) -> schemas.Scene:
        return schemas.Scene(
            id=self.id,
            project_id=self.project_id,
            name=self.name,
            order=self.order,
            clips=[c.to_schema() for c in self.clips],
            cues=[c.to_schema() for c in self.cues],
        )


class ClipRow(Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="panel")
    source_id: Mapped[str] = mapped_column(String(500), default="")
    start: Mapped[float] = mapped_column(default=0)
    duration: Mapped[float] = mapped_column(default=2.0)
    motion: Mapped[str] = mapped_column(String(32), default="none")
    order: Mapped[int] = mapped_column(default=0)

    scene: Mapped[SceneRow] = relationship(back_populates="clips")

    def to_schema(self) -> schemas.Clip:
        return schemas.Clip(
            id=self.id,
            scene_id=self.scene_id,
            kind=self.kind,  # type: ignore[arg-type]
            source_id=self.source_id,
            start=self.start,
            duration=self.duration,
            motion=self.motion,
            order=self.order,
        )


class AudioTakeRow(Base):
    __tablename__ = "audio_takes"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    file_path: Mapped[str] = mapped_column(String(500))  # relative under media_dir
    url: Mapped[str] = mapped_column(String(500))
    mime: Mapped[str] = mapped_column(String(100), default="")
    duration_sec: Mapped[float] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    project: Mapped[ProjectRow] = relationship(back_populates="audios")
    cues: Mapped[list[AudioCueRow]] = relationship(back_populates="audio")

    def to_schema(self) -> schemas.AudioTake:
        return schemas.AudioTake(
            id=self.id,
            project_id=self.project_id,
            name=self.name,
            url=self.url,
            mime=self.mime,
            duration_sec=self.duration_sec,
            created_at=self.created_at.isoformat() if self.created_at else None,
        )


class AudioCueRow(Base):
    __tablename__ = "audio_cues"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id"), index=True)
    audio_id: Mapped[str] = mapped_column(ForeignKey("audio_takes.id"), index=True)
    start_sec: Mapped[float] = mapped_column(default=0)
    offset_sec: Mapped[float] = mapped_column(default=0)
    end_sec: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    lane: Mapped[int] = mapped_column(default=0)
    volume: Mapped[float] = mapped_column(default=1.0)
    fade_in: Mapped[float] = mapped_column(default=0)
    fade_out: Mapped[float] = mapped_column(default=0)

    scene: Mapped[SceneRow] = relationship(back_populates="cues")
    audio: Mapped[AudioTakeRow] = relationship(back_populates="cues")

    def to_schema(self) -> schemas.AudioCue:
        return schemas.AudioCue(
            id=self.id,
            scene_id=self.scene_id,
            audio_id=self.audio_id,
            start_sec=self.start_sec,
            offset_sec=self.offset_sec,
            end_sec=self.end_sec,
            lane=self.lane or 0,
            volume=self.volume,
            fade_in=self.fade_in,
            fade_out=self.fade_out,
        )


class SubtitleTrackRow(Base):
    __tablename__ = "subtitle_tracks"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.id"), index=True, unique=True
    )
    lang: Mapped[str] = mapped_column(String(16), default="en")
    file_path: Mapped[str] = mapped_column(String(500), default="")

    scene: Mapped[SceneRow] = relationship(back_populates="subtitle_track")

    def to_schema(self, segments: list | None = None) -> schemas.SubtitleTrack:
        return schemas.SubtitleTrack(
            id=self.id,
            scene_id=self.scene_id,
            lang=self.lang,
            segments=[
                schemas.SubtitleSegment(start_sec=s.start, end_sec=s.end, text=s.text)
                for s in (segments or [])
            ],
        )


class FrameRow(Base):
    __tablename__ = "frames"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    index: Mapped[int] = mapped_column(default=0)
    width: Mapped[int] = mapped_column(default=1920)
    height: Mapped[int] = mapped_column(default=1080)

    project: Mapped[ProjectRow] = relationship(back_populates="frames")
    layers: Mapped[list[LayerRow]] = relationship(
        back_populates="frame",
        cascade="all, delete-orphan",
        order_by="LayerRow.layer_order",
        lazy="selectin",
    )

    def to_schema(self) -> schemas.DrawFrame:
        return schemas.DrawFrame(
            id=self.id,
            name=self.name,
            width=self.width,
            height=self.height,
            layers=[layer.to_schema() for layer in self.layers],
        )


class LayerRow(Base):
    __tablename__ = "layers"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    frame_id: Mapped[str] = mapped_column(ForeignKey("frames.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="Layer")
    opacity: Mapped[int] = mapped_column(default=100)
    visible: Mapped[bool] = mapped_column(default=True)
    locked: Mapped[bool] = mapped_column(default=False)
    blend_mode: Mapped[str] = mapped_column(String(32), default="normal")
    layer_order: Mapped[int] = mapped_column(default=0)
    data_url: Mapped[str | None] = mapped_column(Text, default=None)

    frame: Mapped[FrameRow] = relationship(back_populates="layers")

    def to_schema(self) -> schemas.DrawLayer:
        return schemas.DrawLayer(
            id=self.id,
            name=self.name,
            opacity=self.opacity,
            visible=self.visible,
            locked=self.locked,
            blend_mode=self.blend_mode,
            data_url=self.data_url,
            layer_order=self.layer_order,
        )


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="local")
    project_id: Mapped[str | None] = mapped_column(String(12), default=None)
    kind: Mapped[str] = mapped_column(String(32), default="image")
    prompt: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(32), default="cloud")
    model: Mapped[str | None] = mapped_column(String(200), default=None)
    width: Mapped[int] = mapped_column(default=1024)
    height: Mapped[int] = mapped_column(default=1024)
    seconds: Mapped[float] = mapped_column(default=2.0)
    attach_frame: Mapped[bool] = mapped_column(default=True)
    seed: Mapped[int | None] = mapped_column(default=None)
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    style: Mapped[str] = mapped_column(String(32), default="none")
    camera_move: Mapped[str] = mapped_column(String(32), default="none")
    enhance: Mapped[bool] = mapped_column(default=False)
    reference_frame_id: Mapped[str | None] = mapped_column(String(12), default=None)
    final_prompt: Mapped[str] = mapped_column(Text, default="")
    entities_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress: Mapped[float] = mapped_column(default=0)
    result_url: Mapped[str | None] = mapped_column(String(500), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    def to_schema(self) -> schemas.Job:
        return schemas.Job(
            id=self.id,
            user_id=self.user_id,
            project_id=self.project_id,
            kind=self.kind,
            prompt=self.prompt or "",
            model=self.model,
            seed=self.seed,
            negative_prompt=self.negative_prompt or "",
            style=self.style or "none",
            camera_move=self.camera_move or "none",
            enhance=self.enhance,
            reference_frame_id=self.reference_frame_id,
            final_prompt=self.final_prompt or "",
            entities_json=self.entities_json or "[]",
            status=self.status,  # type: ignore[arg-type]
            progress=self.progress,
            result_url=self.result_url,
            error=self.error,
        )


class EntityRow(Base):
    """Named consistency anchor (character, field, prop) + frame snapshot."""

    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="local")
    project_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("projects.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), default="character")
    name: Mapped[str] = mapped_column(String(80), default="")
    anchor: Mapped[str] = mapped_column(Text, default="")
    frame_id: Mapped[str | None] = mapped_column(String(12), default=None)

    def to_schema(self) -> schemas.Entity:
        return schemas.Entity(
            id=self.id,
            project_id=self.project_id,
            kind=self.kind,
            name=self.name,
            anchor=self.anchor or "",
            frame_id=self.frame_id,
        )


class ExportReportRow(Base):
    __tablename__ = "export_reports"

    job_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("jobs.id"), primary_key=True
    )
    url: Mapped[str] = mapped_column(String(500))
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    rendered_sec: Mapped[float] = mapped_column(default=0)
    clips_rendered: Mapped[int] = mapped_column(default=0)
    fps: Mapped[int] = mapped_column(default=24)
    width: Mapped[int] = mapped_column(default=1280)
    height: Mapped[int] = mapped_column(default=720)
    audio_cues: Mapped[int] = mapped_column(default=0)
    subtitles_vtt_url: Mapped[str | None] = mapped_column(String(500), default=None)

    def to_schema(self) -> schemas.ExportReport:
        import json

        try:
            warnings = json.loads(self.warnings_json or "[]")
        except ValueError:
            warnings = []
        return schemas.ExportReport(
            job_id=self.job_id,
            url=self.url,
            warnings=warnings,
            rendered_sec=self.rendered_sec,
            clips_rendered=self.clips_rendered,
            fps=self.fps,
            width=self.width,
            height=self.height,
            audio_cues=self.audio_cues,
            subtitles_vtt_url=self.subtitles_vtt_url,
        )


class LedgerRow(Base):
    __tablename__ = "credit_ledger"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=uuid_short)
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="local")
    delta: Mapped[int] = mapped_column(default=0)
    reason: Mapped[str] = mapped_column(String(200), default="")
    job_id: Mapped[str | None] = mapped_column(String(12), default=None)

    def to_schema(self) -> schemas.CreditEntry:
        return schemas.CreditEntry(
            id=self.id,
            user_id=self.user_id,
            delta=self.delta,
            reason=self.reason,
            job_id=self.job_id,
        )
