"""Layered/animation import: .ora stacks and GIF/APNG cels land as frames."""

from __future__ import annotations

import io
import zipfile

import pytest
from conftest import create_user
from fastapi import HTTPException, UploadFile
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.api.animate import import_layered_frames
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, UserRow
from komaverse.layered_import import parse_ora, split_animation


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


def _png(color, size=(16, 12)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _make_ora() -> bytes:
    stack = """<image w="16" h="12"><stack>
      <layer name="Top" src="data/top.png" x="4" y="2" opacity="0.5" visibility="visible" composite-op="svg:multiply"/>
      <layer name="Hidden" src="data/hid.png" opacity="1.0" visibility="hidden" composite-op="svg:src-over"/>
      <layer name="Base" src="data/base.png" composite-op="svg:weird-op"/>
    </stack></image>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "image/openraster")
        zf.writestr("stack.xml", stack)
        zf.writestr("data/top.png", _png((255, 0, 0, 255), (8, 6)))
        zf.writestr("data/hid.png", _png((0, 255, 0, 255)))
        zf.writestr("data/base.png", _png((0, 0, 255, 255)))
    return buf.getvalue()


def test_parse_ora_stack_bottom_first():
    doc = parse_ora(_make_ora())
    assert (doc.width, doc.height) == (16, 12)
    assert [ly.name for ly in doc.layers] == ["Base", "Hidden", "Top"]
    base, hidden, top = doc.layers
    assert (hidden.visible, top.visible) == (False, True)
    assert (hidden.opacity, top.opacity) == (100, 50)
    assert base.blend_mode == "normal"  # unknown op falls back
    assert top.blend_mode == "multiply"
    # Offset layer pasted at (4,2): pixel inside is red, outside is clear.
    img = Image.open(io.BytesIO(top.png))
    assert img.size == (16, 12)
    assert img.getpixel((5, 3))[0] == 255
    assert img.getpixel((0, 0))[3] == 0


def test_parse_ora_rejects(tmp_path):
    import pytest as _pt

    with _pt.raises(Exception):
        parse_ora(b"not a zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("stack.xml", "<image/>")
    with _pt.raises(Exception):
        parse_ora(buf.getvalue())


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(data))


def test_import_ora_endpoint(factory, user):
    proj = _project(factory, user)
    with factory() as s:
        frames = import_layered_frames(
            proj.id, _upload("art.ora", _make_ora()), None, None, s, user
        )
    assert len(frames) == 1
    f = frames[0]
    assert (f.width, f.height) == (16, 12)
    assert [ly.name for ly in f.layers] == ["Base", "Hidden", "Top"]
    assert all(ly.data_url.startswith("data:image/png;base64,") for ly in f.layers)
    assert [ly.visible for ly in f.layers] == [True, False, True]


def _make_gif() -> bytes:
    frames = [
        Image.new("RGBA", (10, 8), (255, 0, 0, 255)),
        Image.new("RGBA", (10, 8), (0, 255, 0, 255)),
        Image.new("RGBA", (10, 8), (0, 0, 255, 255)),
    ]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], loop=0)
    return buf.getvalue()


def test_split_animation_order():
    w, h, pngs = split_animation(_make_gif())
    assert (w, h) == (10, 8) and len(pngs) == 3
    first = Image.open(io.BytesIO(pngs[0]))
    assert first.getpixel((0, 0))[:3] == (255, 0, 0)
    last = Image.open(io.BytesIO(pngs[-1]))
    assert last.getpixel((0, 0))[:3] == (0, 0, 255)


def test_import_gif_makes_sequence(factory, user):
    proj = _project(factory, user)
    with factory() as s:
        frames = import_layered_frames(
            proj.id, _upload("walk.gif", _make_gif()), None, None, s, user
        )
    assert len(frames) == 3
    assert all(len(f.layers) == 1 for f in frames)
    assert all(f.sequence_id == frames[0].sequence_id for f in frames)
    assert frames[0].sequence_id is not None


def test_import_rejects_bad_extension(factory, user):
    proj = _project(factory, user)
    with factory() as s, pytest.raises(HTTPException) as exc:
        import_layered_frames(proj.id, _upload("art.kra", b"PK\x03\x04"), None, None, s, user)
    assert exc.value.status_code == 400
