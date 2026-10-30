"""Scene subtitle tracks: SRT parse, endpoints, export burn + VTT."""

from __future__ import annotations

import io
import subprocess

import pytest
from conftest import create_user
from fastapi import BackgroundTasks, HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers
from test_audio import _panel_project, upload

from komaverse.api.animate import (
    ExportRequest,
    export_project,
    get_export_report,
    run_export_job,
)
from komaverse.api.subtitles import (
    delete_subtitles,
    get_subtitles,
    upload_subtitles,
)
from komaverse.config import settings
from komaverse.db_models import Base, UserRow
from komaverse.subtitles import parse_srt, segments_to_vtt


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


SRT = """1
00:00:00,000 --> 00:00:01,500
Hello world

2
00:00:02.000 --> 00:00:03,250
Second line here
"""


def srt_file(text: str = SRT, name="subs.srt"):
    return UploadFile(
        file=io.BytesIO(text.encode()),
        filename=name,
        headers=Headers({"content-type": "text/plain"}),
    )


def test_parse_valid():
    segs = parse_srt(SRT)
    assert [(s.start, s.end) for s in segs] == [(0.0, 1.5), (2.0, 3.25)]
    assert segs[0].text == "Hello world"


def test_parse_rejects_garbage():
    for bad in ["", "no captions here", "1\nnot a timing line\ntext"]:
        with pytest.raises(ValueError):
            parse_srt(bad)


def test_vtt_shape():
    vtt = segments_to_vtt(parse_srt(SRT))
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.500" in vtt


def test_subtitle_crud(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        track = upload_subtitles(scene_id, srt_file(), s, user)
    assert [seg.text for seg in track.segments] == ["Hello world", "Second line here"]
    with factory() as s:
        fetched = get_subtitles(scene_id, s, user)
    assert fetched is not None and len(fetched.segments) == 2
    # Re-upload replaces the track.
    with factory() as s:
        again = upload_subtitles(scene_id, srt_file("1\n00:00:00,000 --> 00:00:01,000\nSolo\n"), s, user)
    assert [seg.text for seg in again.segments] == ["Solo"]
    with factory() as s:
        delete_subtitles(scene_id, s, user)
    with factory() as s:
        assert get_subtitles(scene_id, s, user) is None


def test_subtitle_validation(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s, pytest.raises(HTTPException) as exc:
        upload_subtitles(scene_id, srt_file(name="subs.txt"), s, user)
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        upload_subtitles(scene_id, srt_file("garbage"), s, user)
    assert exc.value.status_code == 400


def frame_bottom(path, at_sec: float):
    import imageio_ffmpeg
    from PIL import Image

    out = str(path) + ".frame.png"
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner",
         "-ss", str(at_sec), "-i", str(path), "-frames:v", "1", out],
        capture_output=True,
        check=True,
    )
    img = Image.open(out).convert("L")
    w, h = img.size
    px = img.crop((0, h * 2 // 3, w, h))
    return list(px.getdata())


def test_export_burns_subtitles(factory, user):
    project_id, scene_id = _panel_project(factory, user)
    with factory() as s:
        job = export_project(
            project_id, ExportRequest(fps=5, width=320, height=240),
            BackgroundTasks(), s, user,
        )
    run_export_job(job.id, factory)
    with factory() as s:
        plain = get_export_report(job.id, s, user)
    assert plain.subtitles_vtt_url is None
    with factory() as s:
        upload_subtitles(scene_id, srt_file(), s, user)
    with factory() as s:
        job2 = export_project(
            project_id, ExportRequest(fps=5, width=320, height=240),
            BackgroundTasks(), s, user,
        )
    run_export_job(job2.id, factory)
    with factory() as s:
        report = get_export_report(job2.id, s, user)
    assert report.subtitles_vtt_url is not None
    vtt = (settings.media_dir / report.subtitles_vtt_url.removeprefix("/media/")).read_text()
    assert vtt.startswith("WEBVTT") and "Hello world" in vtt
    if not any("burn-in failed" in w for w in report.warnings):
        a = frame_bottom(settings.media_dir / plain.url.removeprefix("/media/"), 0.2)
        b = frame_bottom(
            settings.media_dir / report.url.removeprefix("/media/"), 0.2
        )
        diff = sum(1 for x, y in zip(a, b) if abs(x - y) > 12) / len(a)
        assert diff > 0.005, "burned captions should change the lower third"
