"""Voice takes + timeline cues. Auth required; all data scoped to the owner.

Takes are library recordings (mic captures or uploaded files); cues place
takes on a scene's timeline. The renderer mixes cued takes into the MP4.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from komaverse import storage
from komaverse.ai.providers import (
    ElevenLabsProvider,
    MetaProvider,
    ProviderError,
    parse_freetts_voice,
    synthesize_freetts,
)
from komaverse.studio_settings import provider_for
from komaverse.api.usage import record_usage
from komaverse.auth import ensure_owner, get_current_user, own_project
from komaverse.config import settings
from komaverse.db import get_session
from komaverse.db_models import AudioCueRow, AudioTakeRow, SceneRow, UserRow
from komaverse.models import AudioCue, AudioTake
from komaverse.renderer import audio_has_stream, transcode_to_wav

router = APIRouter(prefix="/api/v1")

AUDIO_EXTS = {
    ".wav", ".mp3", ".m4a", ".ogg", ".oga", ".opus",
    ".webm", ".mp4", ".aac", ".flac",
}
MAX_TAKE_SEC = 3600.0
MAX_VOLUME = 2.0


class TakePatch(BaseModel):
    name: str | None = None


class CueCreate(BaseModel):
    audio_id: str = ""
    start_sec: float = 0.0
    offset_sec: float = 0.0
    end_sec: float | None = None
    lane: int = 0
    volume: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.0


class CuePatch(BaseModel):
    start_sec: float | None = None
    offset_sec: float | None = None
    end_sec: float | None = None
    lane: int | None = None
    volume: float | None = None
    fade_in: float | None = None
    fade_out: float | None = None


class TranscriptTurn(BaseModel):
    transcript: str
    start_sec: float = 0.0
    end_sec: float = 0.0
    speaker: str | None = None


class Transcript(BaseModel):
    take_id: str
    transcript: str
    duration_sec: float = 0.0
    turns: list[TranscriptTurn] = []


class SynthCreate(BaseModel):
    """Generate a voice take from text (TTS).

    voice is provider-shaped and optional (blank = Brain default):
    an ElevenLabs voice id, or free-tts "engine:voice". engine/lang
    override the free-tts call only. cue_scene_id cues the new take at
    cue_start_sec in one round trip.
    """

    text: str = ""
    voice: str | None = None
    engine: str | None = None
    lang: str = "en"
    cue_scene_id: str | None = None
    cue_start_sec: float = 0.0


MAX_SYNTH_CHARS = 2000


def _get_scene(session: Session, user: UserRow, scene_id: str) -> SceneRow:
    row = session.get(SceneRow, scene_id)
    if row is None:
        raise HTTPException(404, "scene not found")
    ensure_owner(session, user, row.project_id)
    return row


def _get_take(session: Session, user: UserRow, audio_id: str) -> AudioTakeRow:
    row = session.get(AudioTakeRow, audio_id)
    if row is None:
        raise HTTPException(404, "audio not found")
    ensure_owner(session, user, row.project_id)
    return row


def _check_cue_timing(
    start: float, offset: float, end: float | None, lane: int, volume: float,
    fade_in: float, fade_out: float, duration: float,
) -> None:
    if start < 0 or start > MAX_TAKE_SEC:
        raise HTTPException(400, f"start_sec out of range: {start}")
    if lane < 0 or lane > 7:
        raise HTTPException(400, f"lane out of range: {lane}")
    if offset < 0:
        raise HTTPException(400, f"offset_sec out of range: {offset}")
    if duration > 0 and offset > duration:
        raise HTTPException(400, "offset_sec is past the end of the take")
    if end is not None and end - start < 0.1:
        raise HTTPException(400, "end_sec must leave at least 0.1s of audio")
    if not 0 <= volume <= MAX_VOLUME:
        raise HTTPException(400, f"volume must be 0..{MAX_VOLUME}, got {volume}")
    if fade_in < 0 or fade_out < 0:
        raise HTTPException(400, "fades cannot be negative")


@router.post("/projects/{project_id}/audio", response_model=AudioTake, status_code=201)
def upload_take(
    project_id: str,
    file: UploadFile = File(...),
    duration_sec: float = Form(0.0),
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Store one voice take. duration_sec comes from the client's decode
    (the server clamps it; muxing tolerates 0 = unknown)."""
    project = own_project(session, user, project_id)
    if not (file.content_type or "").startswith("audio/"):
        raise HTTPException(400, "file must be audio")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXTS:
        raise HTTPException(400, f"unsupported audio type: {ext or '(none)'}")
    try:
        data = storage.read_upload_capped(
            file.file, settings.upload_max_mb * 1024 * 1024
        )
    except storage.UploadTooLargeError:
        raise HTTPException(413, "file too large") from None
    if not data:
        raise HTTPException(400, "empty file")
    try:
        storage.check_quota(project.id, len(data))
    except storage.QuotaExceededError as exc:
        raise HTTPException(413, str(exc)) from None
    rel, url = storage.save_upload(project.id, file.filename or "take", data)
    if not audio_has_stream(storage.resolve(rel)):
        storage.remove_file(rel)
        raise HTTPException(400, "cannot decode audio")
    row = AudioTakeRow(
        project_id=project.id,
        name=Path(file.filename or "take").stem[:200],
        file_path=rel,
        url=url,
        mime=file.content_type or "",
        duration_sec=min(max(duration_sec, 0.0), MAX_TAKE_SEC),
    )
    session.add(row)
    session.commit()
    return row.to_schema()


@router.get("/projects/{project_id}/audio", response_model=list[AudioTake])
def list_takes(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    project = own_project(session, user, project_id)
    res = session.execute(
        select(AudioTakeRow)
        .where(AudioTakeRow.project_id == project.id)
        .order_by(AudioTakeRow.created_at)
    )
    return [r.to_schema() for r in res.scalars().all()]


@router.patch("/audio/{audio_id}", response_model=AudioTake)
def patch_take(
    audio_id: str,
    body: TakePatch,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = _get_take(session, user, audio_id)
    if body.name is not None and body.name.strip():
        row.name = body.name.strip()[:200]
    session.commit()
    return row.to_schema()


@router.delete("/audio/{audio_id}", status_code=204)
def delete_take(
    audio_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = _get_take(session, user, audio_id)
    refs = session.execute(
        select(AudioCueRow).where(AudioCueRow.audio_id == row.id)
    ).scalars().all()
    if refs:
        raise HTTPException(
            409, f"take is used by {len(refs)} cue(s); remove them first"
        )
    rel = row.file_path
    session.delete(row)
    session.commit()
    storage.remove_file(rel)


@router.post("/audio/{audio_id}/transcribe", response_model=Transcript)
def transcribe_take(
    audio_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
    mode: str = "ENDPOINTING",
):
    """Speech-to-text for one take via Muse Voice Transcribe (Meta).

    Returns the transcript plus per-turn timings (turn-level, not
    word-level). Needs META_API_KEY; without it the call fails with a
    400 naming the missing key instead of hanging a job.
    """
    row = _get_take(session, user, audio_id)
    try:
        audio = storage.resolve(row.file_path).read_bytes()
    except OSError as exc:
        raise HTTPException(400, f"cannot read take audio: {exc}") from None
    try:
        wav = transcode_to_wav(audio, Path(row.file_path).suffix)
    except RuntimeError as exc:
        raise HTTPException(400, f"cannot prepare audio: {exc}") from None
    try:
        res = MetaProvider().transcribe_audio(
            wav,
            Path(row.file_path).stem + ".wav",
            mode=(mode or "ENDPOINTING").upper(),
        )
    except ProviderError as exc:
        raise HTTPException(400, f"transcription failed: {exc}") from None
    record_usage(
        session,
        user.id,
        "transcribe",
        settings.meta_transcribe_model,
        len(res.get("transcript") or "") // 4,
    )
    turns = []
    for t in res.get("turns") or []:
        text = str(t.get("transcript") or "").strip()
        if not text:
            continue
        turns.append(
            TranscriptTurn(
                transcript=text,
                start_sec=(t.get("startMs") or 0) / 1000,
                end_sec=(t.get("endMs") or 0) / 1000,
                speaker=t.get("speaker"),
            )
        )
    return Transcript(
        take_id=row.id,
        transcript=res.get("transcript") or "",
        duration_sec=(res.get("audioDurationMs") or 0) / 1000,
        turns=turns,
    )


@router.post(
    "/projects/{project_id}/audio/synthesize",
    response_model=AudioTake,
    status_code=201,
)
def synthesize_take(
    project_id: str,
    body: SynthCreate,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Text-to-speech into the take library (Render tab "Generate").

    Provider comes from the Brain voice row: ElevenLabs cloud voices
    (needs ELEVENLABS_API_KEY) or the local free-tts server (needs
    `python server.py` running in D:\\Coding\\free-tts). The MP3 lands
    as a normal take, so waveforms, cues, and the export mix just work.
    Duration stays 0 (unknown until decoded); the mux tolerates that.
    """
    project = own_project(session, user, project_id)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    if len(text) > MAX_SYNTH_CHARS:
        raise HTTPException(
            400, f"text too long ({len(text)} > {MAX_SYNTH_CHARS} chars)"
        )
    provider = provider_for("voice")
    try:
        if provider == "elevenlabs":
            data = ElevenLabsProvider().synthesize(text, voice=body.voice)
            model = settings.elevenlabs_tts_model
        elif provider == "free-tts":
            engine, voice = parse_freetts_voice(body.voice)
            data = synthesize_freetts(
                text,
                engine=(body.engine or "").strip() or engine,
                voice=voice,
                lang=body.lang,
            )
            model = settings.freetts_voice_model
        else:  # pragma: no cover — provider_for only returns the two above
            raise HTTPException(400, f'voice provider "{provider}" is not a TTS engine')
    except ProviderError as exc:
        raise HTTPException(400, f"voice generation failed: {exc}") from None
    if not data:
        raise HTTPException(400, "voice generation returned empty audio")
    try:
        storage.check_quota(project.id, len(data))
    except storage.QuotaExceededError as exc:
        raise HTTPException(413, str(exc)) from None
    rel, url = storage.save_upload(project.id, "voice.mp3", data)
    if not audio_has_stream(storage.resolve(rel)):
        storage.remove_file(rel)
        raise HTTPException(400, "generated audio is undecodable")
    row = AudioTakeRow(
        project_id=project.id,
        name=f"Voice: {text[:40]}",
        file_path=rel,
        url=url,
        mime="audio/mpeg",
        duration_sec=0.0,
    )
    session.add(row)
    session.commit()
    record_usage(session, user.id, "voice", model, len(text) // 4)
    if body.cue_scene_id:
        scene = session.get(SceneRow, body.cue_scene_id)
        if scene is None or scene.project_id != project.id:
            raise HTTPException(400, "cue scene belongs to another project")
        ensure_owner(session, user, scene.project_id)
        _check_cue_timing(
            body.cue_start_sec, 0.0, None, 0, 1.0, 0.0, 0.0, 0.0,
        )
        session.add(
            AudioCueRow(
                scene_id=scene.id,
                audio_id=row.id,
                start_sec=body.cue_start_sec,
            )
        )
        session.commit()
    return row.to_schema()


@router.post("/scenes/{scene_id}/cues", response_model=AudioCue, status_code=201)
def add_cue(
    scene_id: str,
    body: CueCreate,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    scene = _get_scene(session, user, scene_id)
    audio = session.get(AudioTakeRow, body.audio_id)
    if audio is None:
        raise HTTPException(404, "audio not found")
    ensure_owner(session, user, audio.project_id)
    if audio.project_id != scene.project_id:
        raise HTTPException(400, "audio belongs to another project")
    _check_cue_timing(
        body.start_sec, body.offset_sec, body.end_sec, body.lane, body.volume,
        body.fade_in, body.fade_out, audio.duration_sec,
    )
    row = AudioCueRow(
        scene_id=scene.id,
        audio_id=audio.id,
        start_sec=body.start_sec,
        offset_sec=body.offset_sec,
        end_sec=body.end_sec,
        lane=body.lane,
        volume=body.volume,
        fade_in=body.fade_in,
        fade_out=body.fade_out,
    )
    session.add(row)
    session.commit()
    return row.to_schema()


@router.patch("/cues/{cue_id}", response_model=AudioCue)
def patch_cue(
    cue_id: str,
    body: CuePatch,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(AudioCueRow, cue_id)
    if row is None:
        raise HTTPException(404, "cue not found")
    ensure_owner(session, user, row.scene.project_id)
    start = body.start_sec if body.start_sec is not None else row.start_sec
    offset = body.offset_sec if body.offset_sec is not None else row.offset_sec
    # Tri-state: provided null clears the trim, absent leaves it alone.
    end = body.end_sec if "end_sec" in body.model_fields_set else row.end_sec
    lane = body.lane if body.lane is not None else (row.lane or 0)
    volume = body.volume if body.volume is not None else row.volume
    fade_in = body.fade_in if body.fade_in is not None else row.fade_in
    fade_out = body.fade_out if body.fade_out is not None else row.fade_out
    _check_cue_timing(
        start, offset, end, lane, volume, fade_in, fade_out,
        row.audio.duration_sec,
    )
    row.start_sec, row.offset_sec, row.end_sec = start, offset, end
    row.lane = lane
    row.volume, row.fade_in, row.fade_out = volume, fade_in, fade_out
    session.commit()
    return row.to_schema()


@router.delete("/cues/{cue_id}", status_code=204)
def delete_cue(
    cue_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(AudioCueRow, cue_id)
    if row is None:
        raise HTTPException(404, "cue not found")
    ensure_owner(session, user, row.scene.project_id)
    session.delete(row)
    session.commit()
