"""Scene subtitle tracks. Auth required; all data scoped to the owner.

Upload an .srt per scene (replaces any existing track); the parsed
segments drive the timeline lane and the preview caption, and the export
burns them in when ffmpeg's subtitles filter works (VTT sidecar always
ships for the in-app player).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from komaverse import storage
from komaverse.auth import ensure_owner, get_current_user, own_project
from komaverse.config import settings
from komaverse.db import get_session
from komaverse.db_models import SceneRow, SubtitleTrackRow, UserRow
from komaverse.models import SubtitleTrack
from komaverse.subtitles import SubSegment, parse_srt

router = APIRouter(prefix="/api/v1")

MAX_SRT_BYTES = 512 * 1024


def _get_scene(session: Session, user: UserRow, scene_id: str) -> SceneRow:
    row = session.get(SceneRow, scene_id)
    if row is None:
        raise HTTPException(404, "scene not found")
    ensure_owner(session, user, row.project_id)
    return row


def _track_schema(
    session: Session, row: SubtitleTrackRow,
) -> SubtitleTrack:
    segments = _read_segments(session, row)
    return row.to_schema(segments)


def _read_segments(session: Session, row: SubtitleTrackRow) -> list[SubSegment]:
    path = storage.resolve(row.file_path)
    try:
        return parse_srt(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise HTTPException(400, f"stored subtitles unreadable: {exc}") from None


@router.post("/scenes/{scene_id}/subtitles", response_model=SubtitleTrack, status_code=201)
def upload_subtitles(
    scene_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    scene = _get_scene(session, user, scene_id)
    name = (file.filename or "").lower()
    if not name.endswith(".srt"):
        raise HTTPException(400, "file must be .srt")
    try:
        data = storage.read_upload_capped(file.file, MAX_SRT_BYTES)
    except storage.UploadTooLargeError:
        raise HTTPException(413, "subtitle file too large") from None
    if not data:
        raise HTTPException(400, "empty file")
    try:
        segments = parse_srt(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(400, f"not a usable .srt: {exc}") from None
    try:
        storage.check_quota(scene.project_id, len(data))
    except storage.QuotaExceededError as exc:
        raise HTTPException(413, str(exc)) from None
    rel, _url = storage.save_upload(scene.project_id, "subtitles.srt", data)
    old = session.execute(
        select(SubtitleTrackRow).where(SubtitleTrackRow.scene_id == scene.id)
    ).scalars().first()
    if old is not None:
        storage.remove_file(old.file_path)
        session.delete(old)
        session.flush()
    row = SubtitleTrackRow(scene_id=scene.id, file_path=rel)
    session.add(row)
    session.commit()
    return row.to_schema(segments)


@router.get("/scenes/{scene_id}/subtitles", response_model=SubtitleTrack | None)
def get_subtitles(
    scene_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    scene = _get_scene(session, user, scene_id)
    row = session.execute(
        select(SubtitleTrackRow).where(SubtitleTrackRow.scene_id == scene.id)
    ).scalars().first()
    if row is None:
        return None
    return _track_schema(session, row)


@router.delete("/scenes/{scene_id}/subtitles", status_code=204)
def delete_subtitles(
    scene_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    scene = _get_scene(session, user, scene_id)
    row = session.execute(
        select(SubtitleTrackRow).where(SubtitleTrackRow.scene_id == scene.id)
    ).scalars().first()
    if row is None:
        return
    storage.remove_file(row.file_path)
    session.delete(row)
    session.commit()
