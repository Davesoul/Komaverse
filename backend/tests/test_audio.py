"""Audio v1 tests: takes (record/upload), timeline cues, export mux."""

from __future__ import annotations

import io
import subprocess
import wave

import pytest
from conftest import create_user
from fastapi import BackgroundTasks, HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import Headers

from komaverse.api.ai import get_job
from komaverse.api.animate import (
    ClipBatchCreate,
    ClipCreate,
    ExportRequest,
    add_clips_batch,
    export_project,
    get_export_report,
    list_scenes,
    run_export_job,
)
from komaverse.api.audio import (
    CueCreate,
    CuePatch,
    TakePatch,
    add_cue,
    delete_cue,
    delete_take,
    list_takes,
    patch_cue,
    patch_take,
    upload_take,
)
from komaverse.api.scans import (
    DetectRequest,
    ProjectCreate,
    create_project,
    detect_panels,
    get_project,
    upload_page,
)
from komaverse.config import settings
from komaverse.db_models import Base, UserRow


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def user(factory) -> UserRow:
    with factory() as s:
        return create_user(s)


def wav_bytes(seconds=1.0, freq=440.0, rate=8000) -> bytes:
    import math
    import struct

    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(int(seconds * rate))
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def upload(
    session: Session, user: UserRow, project_id: str, data: bytes | None = None,
    name="take.wav", mime="audio/wav", duration=1.0,
):
    return upload_take(
        project_id,
        UploadFile(
            file=io.BytesIO(data if data is not None else wav_bytes()),
            filename=name,
            headers=Headers({"content-type": mime}),
        ),
        duration,
        session,
        user,
    )


def ffmpeg_streams(path) -> str:
    import imageio_ffmpeg

    proc = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    return proc.stderr


def test_take_crud(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Snd"), s, user)
    with factory() as s:
        take = upload(s, user, project.id)
    assert take.url.startswith("/media/") and take.duration_sec == 1.0
    with factory() as s:
        takes = list_takes(project.id, s, user)
    assert [t.id for t in takes] == [take.id]
    with factory() as s:
        renamed = patch_take(take.id, TakePatch(name="Voiceover"), s, user)
    assert renamed.name == "Voiceover"
    with factory() as s:
        delete_take(take.id, s, user)
    with factory() as s:
        assert list_takes(project.id, s, user) == []
    with factory() as s:
        full = get_project(project.id, s, user)
    assert full.audios == []


def test_take_upload_validation(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Snd"), s, user)
    with factory() as s, pytest.raises(HTTPException) as exc:
        upload(s, user, project.id, b"GIF89a", name="x.gif", mime="image/gif")
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        upload(s, user, project.id, b"data", name="x.exe", mime="audio/x-exe")
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        delete_take("missing", s, user)
    assert exc.value.status_code == 404


def test_upload_rejects_undecodable_audio(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Snd"), s, user)
    with factory() as s, pytest.raises(HTTPException) as exc:
        upload(s, user, project.id, b"not-audio-at-all")
    assert exc.value.status_code == 400
    assert "decode" in exc.value.detail
    leftovers = list((settings.media_dir / "projects" / project.id).glob("*"))
    assert leftovers == []


def test_delete_take_removes_file(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Snd"), s, user)
    with factory() as s:
        take = upload(s, user, project.id)
    path = settings.media_dir / take.url.removeprefix("/media/")
    assert path.exists()
    with factory() as s:
        delete_take(take.id, s, user)
    assert not path.exists()


def test_take_isolation(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Snd"), s, user)
        other = create_user(s, email="snd@test.local")
    with factory() as s:
        take = upload(s, user, project.id)
    # Other user cannot see or touch it (project-scoped → 404).
    with factory() as s, pytest.raises(HTTPException) as exc:
        list_takes(project.id, s, other)
    assert exc.value.status_code == 404
    with factory() as s, pytest.raises(HTTPException) as exc:
        delete_take(take.id, s, other)
    assert exc.value.status_code == 404


def _panel_project(factory, user):
    from PIL import Image, ImageDraw

    img = Image.new("L", (600, 800), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 560, 360], fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    with factory() as s:
        project = create_project(ProjectCreate(name="Cue"), s, user)
    with factory() as s:
        page = upload_page(
            project.id,
            UploadFile(
                file=io.BytesIO(buf.getvalue()),
                filename="p.png",
                headers=Headers({"content-type": "image/png"}),
            ),
            s,
            user,
        )
    with factory() as s:
        detected = detect_panels(page.id, DetectRequest(detector="heuristic"), s, user)
    with factory() as s:
        scenes = list_scenes(project.id, s, user)
    with factory() as s:
        add_clips_batch(
            scenes[0].id,
            ClipBatchCreate(
                clips=[
                    ClipCreate(
                        kind="panel", source_id=detected.panels[0].id, duration=0.5
                    )
                ]
            ),
            s,
            user,
        )
    return project.id, scenes[0].id


def test_cue_crud(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = upload(s, user, project_id)
    with factory() as s:
        cue = add_cue(
            scene_id,
            CueCreate(audio_id=take.id, start_sec=0.0, volume=0.8, fade_in=0.1),
            s,
            user,
        )
    assert cue.volume == 0.8
    with factory() as s:
        full = get_project(project_id, s, user)
    assert [c.id for c in full.scenes[0].cues] == [cue.id]
    with factory() as s:
        patched = patch_cue(cue.id, CuePatch(volume=0.5), s, user)
    assert patched.volume == 0.5
    with factory() as s:
        delete_cue(cue.id, s, user)
    with factory() as s:
        full = get_project(project_id, s, user)
    assert full.scenes[0].cues == []


def test_cue_validation(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = upload(s, user, project_id, duration=2.0)
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_cue(scene_id, CueCreate(audio_id="missing"), s, user)
    assert exc.value.status_code == 404
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_cue(scene_id, CueCreate(audio_id=take.id, volume=5.0), s, user)
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_cue(scene_id, CueCreate(audio_id=take.id, start_sec=-1.0), s, user)
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_cue(scene_id, CueCreate(audio_id=take.id, offset_sec=99.0), s, user)
    assert exc.value.status_code == 400
    # Cross-project take rejected.
    with factory() as s:
        other_project = create_project(ProjectCreate(name="Other"), s, user)
    with factory() as s:
        foreign = upload(s, user, other_project.id)
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_cue(scene_id, CueCreate(audio_id=foreign.id), s, user)
    assert exc.value.status_code == 400


def test_delete_take_blocked_when_cued(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = upload(s, user, project_id)
    with factory() as s:
        cue = add_cue(scene_id, CueCreate(audio_id=take.id), s, user)
    with factory() as s, pytest.raises(HTTPException) as exc:
        delete_take(take.id, s, user)
    assert exc.value.status_code == 409
    with factory() as s:
        delete_cue(cue.id, s, user)
    with factory() as s:
        delete_take(take.id, s, user)


def test_export_muxes_audio(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = upload(s, user, project_id, wav_bytes(seconds=2.0), duration=2.0)
    with factory() as s:
        add_cue(scene_id, CueCreate(audio_id=take.id, start_sec=0.0), s, user)
    with factory() as s:
        job = export_project(
            project_id, ExportRequest(fps=5, width=320, height=240), BackgroundTasks(), s, user
        )
    run_export_job(job.id, factory)
    with factory() as s:
        done = get_job(job.id, s, user)
    assert done.status == "done"
    video = settings.media_dir / done.result_url.removeprefix("/media/")
    streams = ffmpeg_streams(video)
    assert "Audio: aac" in streams
    assert "Video: h264" in streams
    with factory() as s:
        report = get_export_report(job.id, s, user)
    assert report.audio_cues == 1


def test_export_skips_unreadable_audio(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = upload(s, user, project_id, wav_bytes(), duration=1.0)
    # Corrupt after upload: the mux must degrade, never fail the export.
    damaged = settings.media_dir / take.url.removeprefix("/media/")
    damaged.write_bytes(b"not-audio-at-all")
    with factory() as s:
        add_cue(scene_id, CueCreate(audio_id=take.id), s, user)
    with factory() as s:
        job = export_project(
            project_id, ExportRequest(fps=5, width=320, height=240), BackgroundTasks(), s, user
        )
    run_export_job(job.id, factory)
    with factory() as s:
        done = get_job(job.id, s, user)
    assert done.status == "done"
    video = settings.media_dir / done.result_url.removeprefix("/media/")
    assert "Audio:" not in ffmpeg_streams(video)
    with factory() as s:
        report = get_export_report(job.id, s, user)
    assert report.audio_cues == 0
    assert any("audio" in w.lower() for w in report.warnings)
