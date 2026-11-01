"""Text title cards + CapCut-style clip effects (filters, moves, blends).

Covers the clip API surface (add/batch/patch/preview), the renderer
helpers (text raster, grades, new blends, drift), and one real export
with a text card so the burn-in path is proven, not mocked.
"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import create_user
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.api.ai import get_job
from komaverse.api.animate import (
    ClipBatchCreate,
    ClipCreate,
    ClipPatch,
    ExportRequest,
    FrameCreate,
    add_clip,
    add_clips_batch,
    clip_preview,
    create_frame,
    export_project,
    list_scenes,
    patch_clip,
    run_export_job,
)
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, UserRow
from komaverse.renderer import (
    apply_filter,
    blend_frames,
    motion_frame,
    render_text_image,
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


@pytest.fixture
def scene_id(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Cards"), s, user)
    with factory() as s:
        return list_scenes(project.id, s, user)[0].id


def test_add_text_clip_roundtrip(factory, user, scene_id):
    with factory() as s:
        clip = add_clip(
            scene_id,
            ClipCreate(
                kind="text",
                text="Episode One",
                text_size=72,
                text_color="#ffd166",
                text_position="bottom",
                text_anim="rise",
                duration=2.0,
            ),
            s,
            user,
        )
    assert clip.kind == "text"
    assert clip.source_id == ""
    assert clip.text == "Episode One"
    assert clip.text_size == 72
    assert clip.text_color == "#ffd166"
    assert clip.text_position == "bottom"
    assert clip.text_anim == "rise"
    assert clip.filter == "none"


def test_text_clip_needs_copy(factory, user, scene_id):
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_clip(scene_id, ClipCreate(kind="text", text="  "), s, user)
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "field,value",
    [
        ("text_color", "red"),
        ("text_color", "#fff"),
        ("text_position", "left"),
        ("text_anim", "spin"),
        ("filter", "vhs"),
    ],
)
def test_text_clip_rejects_bad_styling(factory, user, scene_id, field, value):
    body = {"kind": "text", "text": "Hi", field: value}
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_clip(scene_id, ClipCreate(**body), s, user)
    assert exc.value.status_code == 400


def test_text_clip_too_long(factory, user, scene_id):
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_clip(scene_id, ClipCreate(kind="text", text="x" * 501), s, user)
    assert exc.value.status_code == 400


def test_batch_with_blank_text_is_all_or_nothing(factory, user, scene_id):
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_clips_batch(
            scene_id,
            ClipBatchCreate(
                clips=[
                    ClipCreate(kind="text", text="Cold open", duration=1.0),
                    ClipCreate(kind="text", text="  ", duration=1.0),
                ]
            ),
            s,
            user,
        )
    assert exc.value.status_code == 400
    # Nothing was stored: the batch validates before writing.
    with factory() as s:
        from komaverse.db_models import ClipRow
        from sqlalchemy import select

        rows = s.execute(
            select(ClipRow).where(ClipRow.scene_id == scene_id)
        ).scalars().all()
    assert rows == []


def test_patch_text_clip(factory, user, scene_id):
    with factory() as s:
        clip = add_clip(
            scene_id, ClipCreate(kind="text", text="Draft"), s, user
        )
    with factory() as s:
        out = patch_clip(
            clip.id,
            ClipPatch(
                text="Final title",
                text_size=96,
                text_color="#ff5a5f",
                text_anim="fade",
                filter="vintage",
            ),
            s,
            user,
        )
    assert out.text == "Final title"
    assert out.text_size == 96
    assert out.text_color == "#ff5a5f"
    assert out.text_anim == "fade"
    assert out.filter == "vintage"


def test_patch_copy_on_still_clip_rejected(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Stills"), s, user)
    with factory() as s:
        frame = create_frame(
            project.id, FrameCreate(name="F1", width=320, height=240), s, user
        )
        sid = list_scenes(project.id, s, user)[0].id
        clip = add_clip(
            sid,
            ClipCreate(kind="frame", source_id=frame.id, filter="sepia"),
            s,
            user,
        )
    # A filter patch is fine on any kind; copy is text-only.
    with factory() as s:
        out = patch_clip(clip.id, ClipPatch(filter="vivid"), s, user)
    assert out.filter == "vivid"
    with factory() as s, pytest.raises(HTTPException) as exc:
        patch_clip(clip.id, ClipPatch(text="Nope"), s, user)
    assert exc.value.status_code == 400


def test_new_motions_and_transitions_accepted(factory, user, scene_id):
    with factory() as s:
        clip = add_clip(
            scene_id,
            ClipCreate(
                kind="text",
                text="Moves",
                motion="drift",
                transition="push-left",
                duration=1.0,
            ),
            s,
            user,
        )
    assert clip.motion == "drift"
    assert clip.transition == "push-left"
    with factory() as s:
        for transition in ("wipe-right", "fade-white"):
            out = patch_clip(clip.id, ClipPatch(transition=transition), s, user)
            assert out.transition == transition


def test_clip_preview_returns_png(factory, user, scene_id):
    with factory() as s:
        clip = add_clip(
            scene_id, ClipCreate(kind="text", text="Thumb"), s, user
        )
    with factory() as s:
        res = clip_preview(clip.id, session=s, user=user)
    assert res.media_type == "image/png"
    assert bytes(res.body).startswith(b"\x89PNG")


def test_render_text_image_card():
    img = render_text_image("Hello", size=64)
    assert img.shape == (1080, 1920, 3)
    assert img.dtype == np.uint8
    # The card ground is near-black, and copy lights pixels up.
    assert (img.mean(axis=2) > 40).sum() > 100
    top = render_text_image("Hello", position="top")
    bottom = render_text_image("Hello", position="bottom")
    assert not np.array_equal(top, bottom)


def test_render_text_anim_progress_moves():
    settle = render_text_image("Hi", anim="rise", progress=1.0)
    start = render_text_image("Hi", anim="rise", progress=0.0)
    assert not np.array_equal(settle, start)
    # Settled anim matches the static card exactly.
    static = render_text_image("Hi")
    assert np.array_equal(settle, static)


def test_apply_filter_shapes():
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 255, (48, 64, 3), dtype=np.uint8)
    for name in ("none", "grayscale", "sepia", "vintage", "vivid"):
        out = apply_filter(frame, name)
        assert out.shape == frame.shape and out.dtype == np.uint8
    gray = apply_filter(frame, "grayscale")
    assert (gray[..., 0] == gray[..., 1]).all()
    assert not np.array_equal(apply_filter(frame, "sepia"), frame)
    # Unknown grades pass through untouched (never a crash in the loop).
    assert np.array_equal(apply_filter(frame, "nope"), frame)


def test_blend_new_kinds():
    a = np.zeros((8, 16, 3), dtype=np.uint8)
    b = np.full((8, 16, 3), 200, dtype=np.uint8)
    mid = blend_frames(a, b, 4, 9, "fade-white")
    assert mid.mean() > 150  # white flash dominates mid-blend
    wl = blend_frames(a, b, 4, 9, "wipe-left")
    wr = blend_frames(a, b, 4, 9, "wipe-right")
    assert not np.array_equal(wl, wr)
    assert (wr[:, :8] == 0).all() and (wr[:, 8:] == 200).all()
    push = blend_frames(a, b, 8, 9, "push-left")
    assert (push[:, -1] == 200).all()  # b has entered from the right


def test_motion_drift_travels_diagonally():
    base = np.zeros((40, 60, 3), dtype=np.uint8)
    base[30:, 45:] = 255
    first = motion_frame(base, 20, 20, 0.0, "drift")
    last = motion_frame(base, 20, 20, 1.0, "drift")
    assert not np.array_equal(first, last)
    assert last.mean() > first.mean()


def test_export_burns_text_card_and_filter(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Burn"), s, user)
    with factory() as s:
        from komaverse.api.animate import list_scenes as scenes_of

        sid = scenes_of(project.id, s, user)[0].id
        add_clip(
            sid,
            ClipCreate(
                kind="text", text="Burn me", text_anim="fade", duration=1.0
            ),
            s,
            user,
        )
        add_clip(
            sid,
            ClipCreate(
                kind="text", text="Grade me", filter="sepia", duration=1.0
            ),
            s,
            user,
        )
    with factory() as s:
        job = export_project(
            project.id,
            ExportRequest(fps=5, width=320, height=240),
            BackgroundTasks(),
            s,
            user,
        )
    run_export_job(job.id, factory)
    with factory() as s:
        done = get_job(job.id, s, user)
    assert done.status == "done"
    assert done.result_url
