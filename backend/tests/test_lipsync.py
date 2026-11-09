"""M2 lip-sync backend tests: offline viseme timing per take/cue + export sidecar."""

from __future__ import annotations

import io
import json
import math
import struct
import wave

import pytest
from conftest import create_user
from fastapi import BackgroundTasks, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
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
    add_cue,
    get_take_lipsync,
    recompute_take_lipsync,
    scene_lipsync,
    synthesize_sfx,
    upload_take,
)
from komaverse.api.scans import (
    DetectRequest,
    ProjectCreate,
    create_project,
    detect_panels,
    upload_page,
)
from komaverse.config import settings
from komaverse.db_models import AudioTakeRow, Base, UserRow
from komaverse.lipsync import (
    analyze_wav_bytes,
    map_frames_to_cue,
    openness_to_viseme,
    procedural_visemes,
)


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


def silence_bytes(seconds=1.0, rate=8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(seconds * rate) * 2)
    return buf.getvalue()


def upload(session, user, project_id, data=None, name="take.wav", duration=1.0):
    return upload_take(
        project_id,
        UploadFile(
            file=io.BytesIO(data if data is not None else wav_bytes()),
            filename=name,
            headers=Headers({"content-type": "audio/wav"}),
        ),
        duration,
        session,
        user,
    )


def _panel_project(factory, user):
    from PIL import Image, ImageDraw

    img = Image.new("L", (600, 800), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 560, 360], fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    with factory() as s:
        project = create_project(ProjectCreate(name="Lip"), s, user)
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
                clips=[ClipCreate(kind="panel", source_id=detected.panels[0].id, duration=2.0)]
            ),
            s,
            user,
        )
    return project.id, scenes[0].id


# --- pure offline analysis (no DB, no ffmpeg) ---


def test_openness_to_viseme_buckets():
    assert openness_to_viseme(0.0) == "closed"
    assert openness_to_viseme(0.14) == "closed"
    assert openness_to_viseme(0.15) == "narrow"
    assert openness_to_viseme(0.39) == "narrow"
    assert openness_to_viseme(0.4) == "open"
    assert openness_to_viseme(0.69) == "open"
    assert openness_to_viseme(0.7) == "wide"
    assert openness_to_viseme(1.0) == "wide"


def test_envelope_silence_all_closed():
    frames = analyze_wav_bytes(silence_bytes(1.0))
    assert len(frames) == 10
    assert all(f["viseme"] == "closed" and f["openness"] == 0.0 for f in frames)
    assert frames[0]["start_sec"] == 0.0
    assert frames[-1]["end_sec"] == pytest.approx(1.0)


def test_envelope_tone_has_open_frames():
    frames = analyze_wav_bytes(wav_bytes(1.0))
    assert len(frames) == 10
    assert any(f["viseme"] != "closed" for f in frames)
    assert all(0.0 <= f["openness"] <= 1.0 for f in frames)
    for a, b in zip(frames, frames[1:], strict=False):
        assert b["start_sec"] == pytest.approx(a["end_sec"])


def test_procedural_deterministic_and_sized():
    again = procedural_visemes(1.0, "hello world")
    assert procedural_visemes(1.0, "hello world") == again
    assert len(again) == 10
    assert len(procedural_visemes(0.35, "hi")) == 4
    idle = procedural_visemes(1.0, "")
    assert all(f["viseme"] == "closed" for f in idle)
    assert procedural_visemes(1.0, "hello world") != idle


def test_map_frames_offset_rate_end():
    frames = procedural_visemes(1.0, "one two three")
    assert len(frames) == 10
    # Head-trim: take time 0.4 lands on cue time 0.0.
    mapped = map_frames_to_cue(frames, 2.0, offset_sec=0.4, rate=1.0)
    assert mapped[0]["start_sec"] == pytest.approx(2.0)
    assert all(f["start_sec"] >= 2.0 for f in mapped)
    assert len(mapped) == 6
    # Double speed halves durations.
    fast = map_frames_to_cue(frames, 0.0, rate=2.0)
    assert fast[0]["end_sec"] == pytest.approx(0.05)
    assert fast[-1]["end_sec"] == pytest.approx(0.5)
    # End trim caps the tail.
    capped = map_frames_to_cue(frames, 0.0, end_sec=0.25)
    assert capped[-1]["end_sec"] == pytest.approx(0.25)
    assert all(f["start_sec"] < 0.25 for f in capped)


# --- take/cue API surface ---


def test_upload_stores_lipsync(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Lip"), s, user)
    with factory() as s:
        take = upload(s, user, project.id, wav_bytes(1.0), duration=1.0)
    assert len(take.lipsync) == 10
    assert any(f.viseme != "closed" for f in take.lipsync)
    with factory() as s:
        track = get_take_lipsync(take.id, s, user)
    assert track.take_id == take.id
    assert [f.model_dump() for f in track.frames] == [f.model_dump() for f in take.lipsync]


def test_sfx_take_stores_lipsync(factory, user):
    from komaverse.api.audio import SfxCreate

    with factory() as s:
        project = create_project(ProjectCreate(name="Lip"), s, user)
    with factory() as s:
        take = synthesize_sfx(project.id, SfxCreate(kind="impact"), s, user)
    assert len(take.lipsync) > 0


def test_cue_lipsync_mapping_and_scene_endpoint(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = upload(s, user, project_id, wav_bytes(2.0), duration=2.0)
    with factory() as s:
        cue = add_cue(
            scene_id,
            CueCreate(audio_id=take.id, start_sec=1.0, offset_sec=0.5, rate=2.0),
            s,
            user,
        )
    # Take 2s @2x from offset 0.5 plays 0.75s starting at cue time 1.0.
    assert cue.lipsync[0].start_sec == pytest.approx(1.0)
    assert cue.lipsync[-1].end_sec == pytest.approx(1.75)
    assert all(f.start_sec >= 1.0 for f in cue.lipsync)
    with factory() as s:
        scene = scene_lipsync(scene_id, s, user)
    assert scene.scene_id == scene_id
    assert len(scene.cues) == 1
    assert scene.cues[0].cue_id == cue.id
    assert scene.cues[0].frames[0].start_sec == pytest.approx(1.0)


def test_recompute_repairs_legacy_take(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Lip"), s, user)
    with factory() as s:
        take = upload(s, user, project.id, wav_bytes(1.0), duration=1.0)
    # Simulate a pre-M2 row with no stored timing.
    with factory() as s:
        row = s.get(AudioTakeRow, take.id)
        row.lipsync_json = ""
        s.commit()
    with factory() as s:
        assert s.get(AudioTakeRow, take.id).to_schema().lipsync == []
    with factory() as s:
        track = recompute_take_lipsync(take.id, s, user)
    assert len(track.frames) == 10


def test_export_includes_lipsync_sidecar(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = upload(s, user, project_id, wav_bytes(2.0), duration=2.0)
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
    with factory() as s:
        report = get_export_report(job.id, s, user)
    assert report.lipsync_url and report.lipsync_url.startswith("/media/")
    sidecar = settings.media_dir / report.lipsync_url.removeprefix("/media/")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["fps"] == 5
    assert len(payload["cues"]) == 1
    assert len(payload["cues"][0]["frames"]) == 20
    assert payload["cues"][0]["frames"][0]["start_sec"] == pytest.approx(0.0)
