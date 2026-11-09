"""M3 vector layers (first slice): layer CRUD, SVG trace-import, guides."""

from __future__ import annotations

import json

import pytest
from conftest import create_user
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.api.animate import (
    FrameCreate,
    FramePut,
    GuidesPut,
    LayerPatch,
    LayerPut,
    SvgImport,
    VectorPath,
    create_frame,
    create_layer,
    duplicate_frame,
    get_frame,
    import_svg_layer,
    patch_layer,
    put_frame,
    put_guides,
)
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, LayerRow, UserRow


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    engine = create_engine(f"sqlite:///{tmp_path}/test_vector.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def user(factory) -> UserRow:
    with factory() as s:
        return create_user(s)


def _frame(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Vec"), s, user)
    with factory() as s:
        return create_frame(project.id, FrameCreate(name="F1", width=320, height=240), s, user)


def _vec(paths=("M10 10 L50 50",)):
    return json.dumps([{"d": d, "stroke": "#000", "stroke_width": 2.0, "fill": "none"} for d in paths])


def test_create_and_read_vector_layer(factory, user):
    frame = _frame(factory, user)
    with factory() as s:
        layer = create_layer(
            frame.id,
            LayerPut(name="ink-vec", kind="vector", vector_data=_vec(), layer_order=0),
            s,
            user,
        )
    assert layer.kind == "vector"
    assert json.loads(layer.vector_data)[0]["d"] == "M10 10 L50 50"
    with factory() as s:
        loaded = get_frame(frame.id, s, user)
    assert loaded.layers[0].kind == "vector"
    assert loaded.layers[0].data_url is None


def test_put_frame_roundtrip_preserves_vector_and_guides(factory, user):
    frame = _frame(factory, user)
    with factory() as s:
        saved = put_frame(
            frame.id,
            FramePut(
                layers=[LayerPut(name="v", kind="vector", vector_data=_vec(), layer_order=0)],
                guides_visible=True,
                guides=[{"kind": "thirds"}],
            ),
            s,
            user,
        )
    assert saved.layers[0].kind == "vector"
    assert saved.guides_visible is True
    assert saved.guides[0].kind == "thirds"
    with factory() as s:
        loaded = get_frame(frame.id, s, user)
    assert loaded.layers[0].vector_data == _vec()
    assert loaded.guides_visible is True
    assert loaded.guides[0].color == "#3b82f6"


def test_patch_layer_updates_vector_data(factory, user):
    frame = _frame(factory, user)
    with factory() as s:
        layer = create_layer(frame.id, LayerPut(name="r"), s, user)
    with factory() as s, pytest.raises(HTTPException) as exc:
        patch_layer(frame.id, layer.id, LayerPatch(vector_data=_vec()), s, user)
    assert exc.value.status_code == 400  # raster + vector_data rejected
    with factory() as s:
        patched = patch_layer(
            frame.id, layer.id, LayerPatch(kind="vector", vector_data=_vec(("M0 0 L9 9",))), s, user
        )
    assert patched.kind == "vector"
    assert "M0 0 L9 9" in patched.vector_data
    with factory() as s, pytest.raises(HTTPException) as exc:
        patch_layer(frame.id, layer.id, LayerPatch(vector_data='[{"d": "<evil>"}]'), s, user)
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        patch_layer(frame.id, "nope", LayerPatch(name="x"), s, user)
    assert exc.value.status_code == 404


def test_create_layer_validation(factory, user):
    frame = _frame(factory, user)
    with factory() as s, pytest.raises(HTTPException) as exc:
        create_layer(frame.id, LayerPut(name="x", kind="mesh"), s, user)
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        create_layer(frame.id, LayerPut(name="x", kind="vector", vector_data="not-json"), s, user)
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        create_layer(frame.id, LayerPut(name="x", opacity=101), s, user)
    assert exc.value.status_code == 400


def test_import_svg_paths_and_raw_svg(factory, user):
    frame = _frame(factory, user)
    with factory() as s:
        layer = import_svg_layer(
            frame.id,
            SvgImport(name="trace", paths=[VectorPath(d="M1 1 L20 20")]),
            s,
            user,
        )
    assert layer.kind == "vector"
    assert json.loads(layer.vector_data)[0]["d"] == "M1 1 L20 20"
    with factory() as s:
        raw = import_svg_layer(
            frame.id,
            SvgImport(name="raw", svg='<svg><path d="M5 5 L15 15"/><path d=\'M0 0 L3 3\'/></svg>'),
            s,
            user,
        )
    ds = [p["d"] for p in json.loads(raw.vector_data)]
    assert ds == ["M5 5 L15 15", "M0 0 L3 3"]
    with factory() as s, pytest.raises(HTTPException) as exc:
        import_svg_layer(frame.id, SvgImport(name="empty", svg="<svg></svg>"), s, user)
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        import_svg_layer(
            frame.id, SvgImport(name="bad", svg='<svg><path d="evil()"/></svg>'), s, user
        )
    assert exc.value.status_code == 400


def test_guides_toggle_and_validation(factory, user):
    frame = _frame(factory, user)
    with factory() as s:
        assert get_frame(frame.id, s, user).guides_visible is False
    with factory() as s:
        shown = put_guides(
            frame.id,
            GuidesPut(guides_visible=True, guides=[{"kind": "center", "opacity": 80}]),
            s,
            user,
        )
    assert shown.guides_visible is True
    assert shown.guides[0].kind == "center" and shown.guides[0].opacity == 80
    with factory() as s:
        hidden = put_guides(frame.id, GuidesPut(guides_visible=False), s, user)
    assert hidden.guides_visible is False
    assert len(hidden.guides) == 1  # toggle alone keeps the list
    with factory() as s, pytest.raises(HTTPException) as exc:
        put_guides(frame.id, GuidesPut(guides=[{"kind": "bogus"}]), s, user)
    assert exc.value.status_code == 400


def test_duplicate_frame_copies_vector_and_guides(factory, user):
    frame = _frame(factory, user)
    with factory() as s:
        put_frame(
            frame.id,
            FramePut(
                layers=[LayerPut(name="v", kind="vector", vector_data=_vec(), layer_order=0)],
                guides_visible=True,
                guides=[{"kind": "diagonal"}],
            ),
            s,
            user,
        )
    with factory() as s:
        copy = duplicate_frame(frame.id, s, user)
    assert copy.layers[0].kind == "vector"
    assert copy.layers[0].vector_data == _vec()
    assert copy.guides_visible is True
    assert copy.guides[0].kind == "diagonal"


def test_legacy_layer_row_reads_as_raster(factory, user):
    frame = _frame(factory, user)
    with factory() as s:
        layer = create_layer(frame.id, LayerPut(name="old"), s, user)
        row = s.get(LayerRow, layer.id)
        row.kind = None  # pre-M3 row: NULL kind column
        row.vector_data = None
        s.commit()
    with factory() as s:
        loaded = get_frame(frame.id, s, user)
    assert loaded.layers[0].kind == "raster"
    assert loaded.layers[0].vector_data is None
