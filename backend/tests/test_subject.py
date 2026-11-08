"""Subject lift: SAM boxes become layers (copy) or background removal (cut).

Box-level cutouts (tight masks are a later pass). SAM calls are stubbed;
only the no-key path touches the real provider guard.
"""

from __future__ import annotations

import base64
import io

import pytest
from conftest import create_user
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.agent import tool_manage_layers
from komaverse.ai.providers import MetaProvider
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, FrameRow, UserRow


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


def _frame(factory, user, w=200, h=200):
    with factory() as s:
        project = create_project(ProjectCreate(name="Subject"), s, user)
        f = FrameRow(project_id=project.id, name="S1", width=w, height=h)
        s.add(f)
        s.commit()
        return project.id, f.id


def _alpha(s, fid, index):
    f = s.get(FrameRow, fid)
    raw = base64.b64decode(f.layers[index].data_url.split(",", 1)[1])
    return Image.open(io.BytesIO(raw)).convert("RGBA").getchannel("A")


def test_subject_copy_lifts_boxes_to_new_layer(factory, user, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        MetaProvider, "segment",
        lambda self, path, concept: seen.update(path=path, concept=concept)
        or [{"x": 10, "y": 10, "w": 60, "h": 60}],
    )
    pid, fid = _frame(factory, user)
    diag = [[10 + i * 3, 10 + i * 3] for i in range(60)]
    with factory() as s:
        tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "stroke", "color": "ink", "brush": "fineliner",
                     "size": 12, "points": diag}],
        )
    with factory() as s:
        out = tool_manage_layers(
            s, user, pid, fid, op="subject", layer="Layer 1",
            concept="robot",
        )
    assert "Lifted 1 'robot' box" in out
    assert seen["concept"] == "robot"
    with factory() as s:
        f = s.get(FrameRow, fid)
        assert len(f.layers) == 2
        box = _alpha(s, fid, 1).getbbox()
        assert box is not None
        # Crop window matches the SAM box.
        assert box[0] >= 10 and box[1] >= 10
        assert box[2] <= 70 and box[3] <= 70
        # Source untouched by a copy lift.
        assert _alpha(s, fid, 0).getbbox() is not None


def test_subject_cut_clears_source(factory, user, monkeypatch):
    monkeypatch.setattr(
        MetaProvider, "segment",
        lambda self, path, concept: [{"x": 0, "y": 0, "w": 200, "h": 200}],
    )
    pid, fid = _frame(factory, user)
    diag = [[10 + i * 3, 10 + i * 3] for i in range(60)]
    with factory() as s:
        tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "stroke", "color": "ink", "brush": "fineliner",
                     "size": 12, "points": diag}],
        )
        out = tool_manage_layers(
            s, user, pid, fid, op="subject", keep="cut", concept="ink",
        )
    assert "source cleared" in out
    with factory() as s:
        assert _layer_box(s, fid, 0) is None
        assert _layer_box(s, fid, 1) is not None


def _layer_box(s, fid, index):
    return _alpha(s, fid, index).getbbox()


def test_subject_needs_meta_key(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "meta_api_key", None)
    pid, fid = _frame(factory, user)
    diag = [[10 + i * 3, 10 + i * 3] for i in range(60)]
    with factory() as s:
        tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "stroke", "color": "ink", "brush": "fineliner",
                     "size": 12, "points": diag}],
        )
        out = tool_manage_layers(s, user, pid, fid, op="subject")
    assert "Meta key" in out


def test_subject_empty_and_missing(factory, user, monkeypatch):
    monkeypatch.setattr(
        MetaProvider, "segment", lambda self, path, concept: [],
    )
    pid, fid = _frame(factory, user)
    with factory() as s:
        out = tool_manage_layers(s, user, pid, fid, op="subject")
        assert "needs a source layer" in out
        tool_manage_layers(s, user, pid, fid, op="add", name="Blank")
        out = tool_manage_layers(s, user, pid, fid, op="subject")
        assert "empty - nothing to lift" in out
    with factory() as s:
        tool_manage_layers(
            s, user, pid, fid, op="draw",
            shapes=[{"type": "stroke", "color": "ink", "brush": "fineliner",
                     "size": 12, "points": [[5, 5], [50, 50]]}],
        )
        out = tool_manage_layers(s, user, pid, fid, op="subject", concept="dragon")
        assert "No 'dragon' found" in out
