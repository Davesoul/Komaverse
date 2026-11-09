"""Sequence interchange: Krita/CSP import (stills, zip, gif) + export (zip/gif/mp4)."""

from __future__ import annotations

import base64
import io
import zipfile

import pytest
from conftest import create_user
from fastapi import HTTPException, UploadFile
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers

from komaverse.api.animate import SequenceCreate, create_sequence, export_sequence, import_sequence
from komaverse.api.scans import ProjectCreate, create_project
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


def _project(factory, user, name="P"):
    with factory() as s:
        return create_project(ProjectCreate(name=name), s, user)


def _png(color=(255, 0, 0, 255), size=(16, 12)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _gif() -> bytes:
    buf = io.BytesIO()
    frames = [Image.new("RGBA", (16, 12), (i * 80, 0, 0, 255)) for i in range(3)]
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], loop=0)
    return buf.getvalue()


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("b.png", _png((0, 255, 0, 255)))
        zf.writestr("a.png", _png((0, 0, 255, 255)))
        zf.writestr("notes.txt", b"not a cel")
    return buf.getvalue()


def _up(name: str, data: bytes) -> UploadFile:
    return UploadFile(
        filename=name,
        file=io.BytesIO(data),
        headers=Headers({"content-type": "application/octet-stream"}),
    )


def _seeded(factory, user):
    project = _project(factory, user)
    with factory() as s:
        frames = import_sequence(
            project.id,
            [_up("a.png", _png((255, 0, 0, 255))), _up("b.png", _png((0, 255, 0, 255)))],
            name="Strip",
            width=16, height=12, session=s,
            user=user,
        )
        return project.id, frames[0].sequence_id


def test_import_gif_makes_cels(factory, user):
    project = _project(factory, user)
    with factory() as s:
        frames = import_sequence(project.id, [_up("walk.gif", _gif())], width=16, height=12, session=s, user=user)
    assert len(frames) == 3
    assert [f.name for f in frames] == ["walk 1", "walk 2", "walk 3"]
    assert all(len(f.layers) == 1 and f.layers[0].data_url for f in frames)


def _cel_color(frame) -> tuple:
    raw = frame.layers[0].data_url.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB").getpixel((8, 6))


def test_import_multi_stills_in_order(factory, user):
    project = _project(factory, user)
    with factory() as s:
        frames = import_sequence(
            project.id,
            [
                _up("krita_002.png", _png((255, 0, 0, 255))),
                _up("krita_001.png", _png((0, 0, 255, 255))),
            ],
            width=16, height=12, session=s,
            user=user,
        )
    # Upload order wins (Krita numbers live in the filenames, not pixels).
    assert [_cel_color(f) for f in frames] == [(255, 0, 0), (0, 0, 255)]


def test_import_zip_skips_non_images(factory, user):
    project = _project(factory, user)
    with factory() as s:
        frames = import_sequence(project.id, [_up("scene.zip", _zip())], width=16, height=12, session=s, user=user)
    assert len(frames) == 2
    assert frames[0].sequence_id == frames[1].sequence_id


def test_import_rejects_bad_extension(factory, user):
    project = _project(factory, user)
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            import_sequence(project.id, [_up("anim.txt", b"nope")], session=s, user=user)
    assert exc.value.status_code == 400


def test_import_rejects_over_cap(factory, user):
    project = _project(factory, user)
    files = [_up(f"f{i:03d}.png", _png()) for i in range(50)]
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            import_sequence(project.id, files, width=16, height=12, session=s, user=user)
    assert exc.value.status_code == 400
    assert "48" in exc.value.detail


def test_export_pngzip(factory, user):
    _pid, sid = _seeded(factory, user)
    with factory() as s:
        resp = export_sequence(sid, "pngzip", 12, s, user)
    assert resp.media_type == "application/zip"
    names = sorted(zipfile.ZipFile(io.BytesIO(resp.body)).namelist())
    assert len(names) == 2
    assert all(n.endswith(f"_{i:03d}.png") for i, n in enumerate(names, 1))


def test_export_gif_loops(factory, user):
    _pid, sid = _seeded(factory, user)
    with factory() as s:
        resp = export_sequence(sid, "gif", 12, s, user)
    assert resp.media_type == "image/gif"
    assert resp.body[:6] == b"GIF89a"


def test_export_mp4_plays(factory, user):
    _pid, sid = _seeded(factory, user)
    with factory() as s:
        resp = export_sequence(sid, "mp4", 12, s, user)
    assert resp.media_type == "video/mp4"
    assert b"ftyp" in resp.body[:32]


def test_export_rejects_unknown_format(factory, user):
    _pid, sid = _seeded(factory, user)
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            export_sequence(sid, "avi", 12, s, user)
    assert exc.value.status_code == 400


def test_export_rejects_empty_sequence(factory, user):
    project = _project(factory, user)
    with factory() as s:
        seq = create_sequence(project.id, SequenceCreate(name="Empty"), s, user)
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            export_sequence(seq.id, "gif", 12, s, user)
    assert exc.value.status_code == 400
