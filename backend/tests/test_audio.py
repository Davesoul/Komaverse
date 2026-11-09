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

from komaverse.api.ai import EntityCreate, create_entity, get_job
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
    SfxCreate,
    SynthCreate,
    TakePatch,
    add_cue,
    captions_from_cues,
    delete_cue,
    delete_take,
    list_takes,
    patch_cue,
    patch_take,
    synthesize_sfx,
    synthesize_take,
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
from komaverse.db_models import AudioCueRow, Base, SubtitleTrackRow, UserRow
from komaverse.renderer import _atempo_chain


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


def test_cue_end_trim(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = upload(s, user, project_id, duration=6.0)
    with factory() as s:
        cue = add_cue(
            scene_id,
            CueCreate(audio_id=take.id, start_sec=1.0, end_sec=4.0),
            s,
            user,
        )
    assert cue.end_sec == 4.0
    # Right-edge trim persists; clearing restores take-end playback.
    with factory() as s:
        patched = patch_cue(cue.id, CuePatch(end_sec=2.5), s, user)
    assert patched.end_sec == 2.5
    with factory() as s:
        cleared = patch_cue(cue.id, CuePatch(end_sec=None), s, user)
    assert cleared.end_sec is None
    # Untouched patch keeps the trim.
    with factory() as s:
        patch_cue(cue.id, CuePatch(end_sec=3.0), s, user)
    with factory() as s:
        kept = patch_cue(cue.id, CuePatch(volume=0.7), s, user)
    assert kept.end_sec == 3.0
    # Degenerate trims rejected.
    with factory() as s, pytest.raises(HTTPException) as exc:
        patch_cue(cue.id, CuePatch(end_sec=1.0), s, user)
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_cue(scene_id, CueCreate(audio_id=take.id, end_sec=0.05), s, user)
    assert exc.value.status_code == 400


def test_cue_head_trim(factory, user):
    """Head-trim skips into the take: patching offset_sec slides the
    timeline start right with it, the end stays, and the export mixes
    the cue from its offset."""
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = upload(s, user, project_id, wav_bytes(seconds=4.0), duration=4.0)
    with factory() as s:
        cue = add_cue(
            scene_id,
            CueCreate(audio_id=take.id, start_sec=1.0),
            s,
            user,
        )
    assert (cue.start_sec, cue.offset_sec) == (1.0, 0.0)
    # Shaving 1.5s off the head moves the start, never the tail.
    with factory() as s:
        trimmed = patch_cue(cue.id, CuePatch(start_sec=2.5, offset_sec=1.5), s, user)
    assert (trimmed.start_sec, trimmed.offset_sec) == (2.5, 1.5)
    # Skipping past the take end is rejected, never clamped silently.
    with factory() as s, pytest.raises(HTTPException) as exc:
        patch_cue(cue.id, CuePatch(offset_sec=99.0), s, user)
    assert exc.value.status_code == 400
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
    with factory() as s:
        report = get_export_report(job.id, s, user)
    assert report.audio_cues == 1


def test_cue_lane(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = upload(s, user, project_id)
    with factory() as s:
        cue = add_cue(scene_id, CueCreate(audio_id=take.id, lane=2), s, user)
    assert cue.lane == 2
    with factory() as s:
        moved = patch_cue(cue.id, CuePatch(lane=1), s, user)
    assert moved.lane == 1
    with factory() as s, pytest.raises(HTTPException) as exc:
        patch_cue(cue.id, CuePatch(lane=9), s, user)
    assert exc.value.status_code == 400


def test_cue_rate(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = upload(s, user, project_id)
    with factory() as s:
        cue = add_cue(scene_id, CueCreate(audio_id=take.id, rate=2.0), s, user)
    assert cue.rate == 2.0
    with factory() as s:
        slowed = patch_cue(cue.id, CuePatch(rate=0.5), s, user)
    assert slowed.rate == 0.5
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_cue(scene_id, CueCreate(audio_id=take.id, rate=0.1), s, user)
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        patch_cue(cue.id, CuePatch(rate=5.0), s, user)
    assert exc.value.status_code == 400


def test_atempo_chain_splits_out_of_range_rates():
    """ffmpeg atempo accepts 0.5..2.0 per filter; wider cue rates chain."""
    assert _atempo_chain(1.0) == ["atempo=1.000"]
    assert _atempo_chain(1.5) == ["atempo=1.500"]
    assert _atempo_chain(4.0) == ["atempo=2.000", "atempo=2.000"]
    assert _atempo_chain(0.25) == ["atempo=0.500", "atempo=0.500"]


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


def test_legacy_cue_without_fades_serializes():
    """Pre-fade cue rows hold NULL (ALTER-added columns on old DBs are
    nullable): to_schema must emit the schema defaults instead of 500ing
    the scene."""
    row = AudioCueRow(
        id="q1",
        scene_id="s1",
        audio_id="a1",
        start_sec=0.0,
        offset_sec=0.0,
        volume=1.0,
        fade_in=None,
        fade_out=None,
        rate=None,
    )
    cue = row.to_schema()
    assert cue.fade_in == 0.0
    assert cue.fade_out == 0.0
    assert cue.rate == 1.0


def test_cue_speaker_text_roundtrip(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = upload(s, user, project_id)
    with factory() as s:
        cue = add_cue(
            scene_id,
            CueCreate(audio_id=take.id, speaker="Mina", text="Look out!"),
            s,
            user,
        )
    assert (cue.speaker, cue.text) == ("Mina", "Look out!")
    with factory() as s:
        patched = patch_cue(cue.id, CuePatch(speaker="Jin"), s, user)
    assert (patched.speaker, patched.text) == ("Jin", "Look out!")


def test_synth_routes_entity_voice(factory, user, monkeypatch):
    from komaverse.api import audio as audio_api

    monkeypatch.setattr(settings, "voice_provider", "free-tts")
    calls = {}

    def fake_synth(text, *, engine=None, voice=None, lang="en"):
        calls.update(voice=voice)
        return b"ID3fake-mp3-bytes"

    monkeypatch.setattr(audio_api, "synthesize_local", fake_synth)
    monkeypatch.setattr(audio_api, "audio_has_stream", lambda path: True)
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        ent = create_entity(
            project_id,
            EntityCreate(kind="character", name="Mina", voice="espeak:en+f3"),
            s,
            user,
        )
    with factory() as s:
        synthesize_take(
            project_id,
            SynthCreate(
                text="Run!",
                entity_id=ent.id,
                cue_scene_id=scene_id,
                cue_start_sec=1.0,
            ),
            s,
            user,
        )
    assert calls["voice"] == "espeak:en+f3"
    with factory() as s:
        cues = get_project(project_id, s, user).scenes[0].cues
    assert [(c.speaker, c.text) for c in cues] == [("Mina", "Run!")]
    with factory() as s, pytest.raises(HTTPException) as exc:
        synthesize_take(
            project_id, SynthCreate(text="Run!", entity_id="missing"), s, user
        )
    assert exc.value.status_code == 404


def test_sfx_kinds_and_cue(factory, user):
    from komaverse.sfx_synth import SFX_KINDS

    assert set(SFX_KINDS) == {"impact", "whoosh", "pop", "sparkle", "roomtone"}
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = synthesize_sfx(
            project_id,
            SfxCreate(kind="impact", cue_scene_id=scene_id, cue_start_sec=0.5),
            s,
            user,
        )
    assert take.duration_sec == pytest.approx(0.6)
    with factory() as s:
        cues = get_project(project_id, s, user).scenes[0].cues
    assert len(cues) == 1
    assert (cues[0].lane, cues[0].start_sec) == (1, 0.5)
    with factory() as s, pytest.raises(HTTPException) as exc:
        synthesize_sfx(project_id, SfxCreate(kind="nuke"), s, user)
    assert exc.value.status_code == 400


def test_captions_from_cues(factory, user):
    from komaverse.subtitles import parse_srt

    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        take = upload(s, user, project_id, duration=4.0)
    with factory() as s:
        add_cue(
            scene_id,
            CueCreate(audio_id=take.id, start_sec=1.0, text="Hello there"),
            s,
            user,
        )
    with factory() as s:
        track = captions_from_cues(scene_id, s, user)
    assert track.scene_id == scene_id
    with factory() as s:
        stored = s.get(SubtitleTrackRow, track.id)
        path = settings.media_dir / stored.file_path
    segments = parse_srt(path.read_text(encoding="utf-8-sig"))
    assert [seg.text for seg in segments] == ["Hello there"]
    assert segments[0].start == pytest.approx(1.0)
    with factory() as s:
        for cue in get_project(project_id, s, user).scenes[0].cues:
            delete_cue(cue.id, s, user)
    with factory() as s, pytest.raises(HTTPException) as exc:
        captions_from_cues(scene_id, s, user)
    assert exc.value.status_code == 400


def test_ducking_mix_ducks_bed_under_dialogue(factory, user, tmp_path):
    import imageio_ffmpeg

    from komaverse.renderer import mix_audio_tracks
    from komaverse.sfx_synth import render_sfx

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [exe, "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
         "-pix_fmt", "yuv420p", str(silent)],
        capture_output=True,
        check=True,
    )
    dlg_wav = tmp_path / "dlg.wav"
    bed_wav = tmp_path / "bed.wav"
    dlg_wav.write_bytes(render_sfx("pop")[0])
    bed_wav.write_bytes(render_sfx("roomtone")[0])
    dialogue = {
        "path": dlg_wav, "start_sec": 0.0, "offset_sec": 0.0, "volume": 1.0,
        "fade_in": 0.0, "fade_out": 0.0, "rate": 1.0, "end_sec": 0.18, "lane": 0,
    }
    bed = {
        "path": bed_wav, "start_sec": 0.0, "offset_sec": 0.0, "volume": 1.0,
        "fade_in": 0.0, "fade_out": 0.0, "rate": 1.0, "end_sec": 1.0, "lane": 1,
    }
    dest = tmp_path / "ducked.mp4"
    mix_audio_tracks(silent, [dialogue, bed], 1.0, dest)
    assert dest.stat().st_size > 0
    assert "Duration: 00:00:01" in ffmpeg_streams(dest)
    # Bed-only mix keeps the legacy direct path.
    solo = tmp_path / "solo.mp4"
    mix_audio_tracks(silent, [bed], 1.0, solo)
    assert solo.stat().st_size > 0

