"""M3 tests: scenes/clips/frames CRUD + real MP4 export, all sync."""

from __future__ import annotations

import io

import base64

import cv2
import pytest
from conftest import create_user
from fastapi import BackgroundTasks, HTTPException, UploadFile
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import Headers

from komaverse.api.ai import get_job
from komaverse.api.animate import (
    ClipBatchCreate,
    ClipCreate,
    ClipOrder,
    ClipPatch,
    ExportRequest,
    FrameCreate,
    FramePut,
    LayerPut,
    ScenePatch,
    add_clip,
    add_clips_batch,
    create_frame,
    delete_clip,
    delete_frame,
    download_export,
    duplicate_frame,
    export_project,
    frame_thumbnail,
    get_export_report,
    get_frame,
    list_frames,
    list_scenes,
    list_sequences,
    order_clips,
    patch_clip,
    patch_scene,
    put_frame,
    run_export_job,
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
from komaverse.db_models import Base, FrameRow, PageRow, UserRow
from komaverse.renderer import composite_frame_image, render_project


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


def fake_page() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("L", (600, 800), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 560, 360], fill=0)
    d.rectangle([40, 400, 340, 760], fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def upload(session: Session, user: UserRow, project_id: str) -> str:
    page = upload_page(
        project_id,
        UploadFile(
            file=io.BytesIO(fake_page()),
            filename="p1.png",
            headers=Headers({"content-type": "image/png"}),
        ),
        session,
        user,
    )
    return page.id


def test_default_scene_and_clip_crud(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Anim"), s, user)
    with factory() as s:
        page_id = upload(s, user, project.id)
    with factory() as s:
        detected = detect_panels(page_id, DetectRequest(detector="heuristic"), s, user)
    with factory() as s:
        scenes = list_scenes(project.id, s, user)
    assert len(scenes) == 1 and scenes[0].name == "Scene 1"

    with factory() as s:
        clip = add_clip(
            scenes[0].id,
            ClipCreate(
                kind="panel",
                source_id=detected.panels[0].id,
                duration=1.5,
                motion="zoom-in",
            ),
            s,
            user,
        )
    assert clip.order == 0
    with factory() as s:
        patched = patch_clip(clip.id, ClipPatch(duration=3.0), s, user)
    assert patched.duration == 3.0
    with factory() as s:
        delete_clip(clip.id, s, user)
    with factory() as s:
        full = get_project(project.id, s, user)
    assert full.scenes[0].clips == []


def test_frame_roundtrip(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Draw"), s, user)
    with factory() as s:
        frame = create_frame(project.id, FrameCreate(name="F1", width=320, height=240), s, user)
    red_dot = (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    with factory() as s:
        saved = put_frame(
            frame.id,
            FramePut(
                layers=[
                    LayerPut(name="ink", layer_order=0, data_url=red_dot),
                    LayerPut(name="empty", layer_order=1),
                ]
            ),
            s,
            user,
        )
    assert len(saved.layers) == 2
    with factory() as s:
        loaded = get_frame(frame.id, s, user)
    assert loaded.layers[0].data_url == red_dot
    assert loaded.layers[1].data_url is None


def test_frame_dims_validation(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="V"), s, user)
    for w, h in [(0, 240), (320, 0), (-1, 240), (320, -5), (5000, 240), (320, 5000)]:
        with factory() as s, pytest.raises(HTTPException) as exc:
            create_frame(
                project.id, FrameCreate(name="bad", width=w, height=h), s, user
            )
        assert exc.value.status_code == 400
    with factory() as s:
        frame = create_frame(
            project.id, FrameCreate(name="ok", width=320, height=240), s, user
        )
    with factory() as s, pytest.raises(HTTPException) as exc:
        put_frame(frame.id, FramePut(width=0), s, user)
    assert exc.value.status_code == 400


def test_frame_layers_validation(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="V"), s, user)
    with factory() as s:
        frame = create_frame(
            project.id, FrameCreate(name="ok", width=320, height=240), s, user
        )
    bad_layer_sets = [
        [LayerPut(name="x", opacity=101)],
        [LayerPut(name="x", opacity=-1)],
        [LayerPut(name="x", blend_mode="bogus")],
        [LayerPut(name=f"l{i}") for i in range(65)],
    ]
    for layers in bad_layer_sets:
        with factory() as s, pytest.raises(HTTPException) as exc:
            put_frame(frame.id, FramePut(layers=layers), s, user)
        assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        put_frame(
            frame.id,
            FramePut(layers=[LayerPut(name="hog", data_url="x" * (20 * 1024 * 1024 + 1))]),
            s,
            user,
        )
    assert exc.value.status_code == 413


def _frame_with_layer(factory, user, project_id, name="F1"):
    with factory() as s:
        frame = create_frame(
            project_id, FrameCreate(name=name, width=320, height=240), s, user
        )
    red_dot = (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    with factory() as s:
        put_frame(
            frame.id,
            FramePut(layers=[LayerPut(name="ink", layer_order=0, data_url=red_dot)]),
            s,
            user,
        )
    return frame.id


def test_delete_frame(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Del"), s, user)
    frame_id = _frame_with_layer(factory, user, project.id)
    with factory() as s:
        delete_frame(frame_id, s, user)
    with factory() as s, pytest.raises(HTTPException) as exc:
        get_frame(frame_id, s, user)
    assert exc.value.status_code == 404
    with factory() as s, pytest.raises(HTTPException) as exc:
        delete_frame("missing-frame", s, user)
    assert exc.value.status_code == 404


def test_delete_frame_blocked_when_clipped(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Clip"), s, user)
    frame_id = _frame_with_layer(factory, user, project.id)
    with factory() as s:
        scenes = list_scenes(project.id, s, user)
        add_clip(
            scenes[0].id,
            ClipCreate(kind="frame", source_id=frame_id, duration=1.0),
            s,
            user,
        )
    with factory() as s, pytest.raises(HTTPException) as exc:
        delete_frame(frame_id, s, user)
    assert exc.value.status_code == 409
    with factory() as s:
        assert get_frame(frame_id, s, user).id == frame_id


def test_delete_frame_isolation(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Iso"), s, user)
        other = create_user(s, email="other@x.local")
    frame_id = _frame_with_layer(factory, user, project.id)
    with factory() as s, pytest.raises(HTTPException) as exc:
        delete_frame(frame_id, s, other)
    assert exc.value.status_code == 404


def test_duplicate_frame(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Dup"), s, user)
    frame_id = _frame_with_layer(factory, user, project.id, name="Hero")
    with factory() as s:
        copy = duplicate_frame(frame_id, s, user)
    assert copy.id != frame_id
    assert copy.name == "Hero copy"
    assert (copy.width, copy.height) == (320, 240)
    with factory() as s:
        orig = get_frame(frame_id, s, user)
    assert len(copy.layers) == len(orig.layers) == 1
    assert copy.layers[0].data_url == orig.layers[0].data_url
    assert copy.layers[0].id != orig.layers[0].id


def test_frame_thumbnail(factory, user):
    from PIL import Image

    with factory() as s:
        project = create_project(ProjectCreate(name="Thumb"), s, user)
    frame_id = _frame_with_layer(factory, user, project.id)
    with factory() as s:
        res = frame_thumbnail(frame_id, s, user)
    assert res.media_type == "image/png"
    assert res.body[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(res.body))
    assert max(img.size) <= 320
    # Paper background parity with the canvas (#f6f1e8) on an empty frame.
    with factory() as s:
        blank = create_frame(
            project.id, FrameCreate(name="blank", width=320, height=240), s, user
        )
    with factory() as s:
        res2 = frame_thumbnail(blank.id, s, user)
    px = Image.open(io.BytesIO(res2.body)).convert("RGB").getpixel((0, 0))
    assert px == (246, 241, 232)


def _first_panel_id(factory, user, project_id) -> str:
    with factory() as s:
        page_id = upload(s, user, project_id)
    with factory() as s:
        detected = detect_panels(page_id, DetectRequest(detector="heuristic"), s, user)
    assert detected.panels
    return detected.panels[0].id


def test_add_clip_validates_source(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="V"), s, user)
    with factory() as s:
        scenes = list_scenes(project.id, s, user)
    scene_id = scenes[0].id
    panel_id = _first_panel_id(factory, user, project.id)
    with factory() as s:
        frame = create_frame(
            project.id, FrameCreate(name="F", width=320, height=240), s, user
        )
    with factory() as s:
        c1 = add_clip(scene_id, ClipCreate(kind="panel", source_id=panel_id), s, user)
        c2 = add_clip(
            scene_id, ClipCreate(kind="frame", source_id=frame.id), s, user
        )
    assert (c1.order, c2.order) == (0, 1)
    for kind, sid in [("panel", "nope"), ("frame", "nope"), ("media", "nope")]:
        with factory() as s, pytest.raises(HTTPException) as exc:
            add_clip(scene_id, ClipCreate(kind=kind, source_id=sid), s, user)
        assert exc.value.status_code == 400
    # Another user's source is rejected too.
    with factory() as s:
        other = create_user(s, email="clipsrc@test.local")
        oproj = create_project(ProjectCreate(name="O"), s, other)
    other_panel = _first_panel_id(factory, other, oproj.id)
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_clip(
            scene_id, ClipCreate(kind="panel", source_id=other_panel), s, user
        )
    assert exc.value.status_code == 400


def _scene_with_panels(factory, user, name="Batch"):
    with factory() as s:
        project = create_project(ProjectCreate(name=name), s, user)
    with factory() as s:
        page_id = upload(s, user, project.id)
    with factory() as s:
        detected = detect_panels(page_id, DetectRequest(detector="heuristic"), s, user)
    with factory() as s:
        scenes = list_scenes(project.id, s, user)
    return project.id, scenes[0].id, [p.id for p in detected.panels]


def test_add_clips_batch(factory, user):
    project_id, scene_id, panels = _scene_with_panels(factory, user)
    assert len(panels) >= 2
    with factory() as s:
        clips = add_clips_batch(
            scene_id,
            ClipBatchCreate(
                clips=[
                    ClipCreate(kind="panel", source_id=panels[0], duration=1.0),
                    ClipCreate(kind="panel", source_id=panels[1], duration=2.0),
                ]
            ),
            s,
            user,
        )
    assert [c.order for c in clips] == [0, 1]
    # Atomic: one bad entry rejects the whole batch.
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_clips_batch(
            scene_id,
            ClipBatchCreate(
                clips=[
                    ClipCreate(kind="panel", source_id=panels[0]),
                    ClipCreate(kind="panel", source_id="nope"),
                ]
            ),
            s,
            user,
        )
    assert exc.value.status_code == 400
    with factory() as s:
        full = get_project(project_id, s, user)
    assert len(full.scenes[0].clips) == 2
    # Cap enforced.
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_clips_batch(
            scene_id,
            ClipBatchCreate(
                clips=[ClipCreate(kind="panel", source_id=panels[0])] * 201
            ),
            s,
            user,
        )
    assert exc.value.status_code == 400


def test_order_frames(factory, user):
    from komaverse.api.animate import (
        FrameCreate,
        FrameOrder,
        create_frame,
        list_frames,
        order_frames,
    )

    with factory() as s:
        project = create_project(ProjectCreate(name="Reorder"), s, user)
    with factory() as s:
        made = [
            create_frame(project.id, FrameCreate(name=f"F{i}"), s, user)
            for i in range(3)
        ]
    ids = [f.id for f in made]
    want = [ids[2], ids[0], ids[1]]
    with factory() as s:
        out = order_frames(project.id, FrameOrder(frame_ids=want), s, user)
    assert [f.id for f in out] == want
    with factory() as s:
        again = list_frames(project.id, s, user)
    assert [f.id for f in again] == want
    # Mismatched sets rejected.
    with factory() as s, pytest.raises(HTTPException) as exc:
        order_frames(project.id, FrameOrder(frame_ids=want[:-1]), s, user)
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        order_frames(project.id, FrameOrder(frame_ids=[*want, "nope"]), s, user)
    assert exc.value.status_code == 400


def test_order_clips(factory, user):
    project_id, scene_id, panels = _scene_with_panels(factory, user)
    with factory() as s:
        clips = add_clips_batch(
            scene_id,
            ClipBatchCreate(
                clips=[ClipCreate(kind="panel", source_id=pid) for pid in panels[:3]]
            ),
            s,
            user,
        )
    ids = [c.id for c in clips]
    want = [ids[2], ids[0], ids[1]] if len(ids) >= 3 else list(reversed(ids))
    with factory() as s:
        out = order_clips(scene_id, ClipOrder(clip_ids=want), s, user)
    assert [c.id for c in out] == want
    assert [c.order for c in out] == list(range(len(want)))
    # Mismatched sets rejected, order untouched.
    with factory() as s, pytest.raises(HTTPException) as exc:
        order_clips(scene_id, ClipOrder(clip_ids=want[:-1]), s, user)
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        order_clips(scene_id, ClipOrder(clip_ids=[*want, "nope"]), s, user)
    assert exc.value.status_code == 400
    with factory() as s:
        full = get_project(project_id, s, user)
    assert [c.id for c in full.scenes[0].clips] == want


def test_patch_scene(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="S"), s, user)
    with factory() as s:
        scenes = list_scenes(project.id, s, user)
    with factory() as s:
        renamed = patch_scene(scenes[0].id, ScenePatch(name="Intro"), s, user)
    assert renamed.name == "Intro"
    with factory() as s, pytest.raises(HTTPException) as exc:
        patch_scene("missing", ScenePatch(name="x"), s, user)
    assert exc.value.status_code == 404
    with factory() as s:
        other = create_user(s, email="scene@test.local")
    with factory() as s, pytest.raises(HTTPException) as exc:
        patch_scene(scenes[0].id, ScenePatch(name="x"), s, other)
    assert exc.value.status_code == 404


def test_export_renders_mp4(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Export"), s, user)
    with factory() as s:
        page_id = upload(s, user, project.id)
    with factory() as s:
        detected = detect_panels(page_id, DetectRequest(detector="heuristic"), s, user)
    assert len(detected.panels) >= 2
    with factory() as s:
        scenes = list_scenes(project.id, s, user)
        for panel in detected.panels:
            add_clip(
                scenes[0].id,
                ClipCreate(
                    kind="panel", source_id=panel.id, duration=0.4, motion="zoom-in"
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
    assert job.kind == "export" and job.status == "queued"
    run_export_job(job.id, factory)
    with factory() as s:
        done = get_job(job.id, s, user)
    assert done.status == "done" and done.progress == 1.0
    assert done.result_url and done.result_url.endswith(".mp4")
    video_path = settings.media_dir / done.result_url.removeprefix("/media/")
    assert video_path.exists() and video_path.stat().st_size > 1000
    cap = cv2.VideoCapture(str(video_path))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    assert frames >= 2, "expected at least 2 encoded frames"
    with factory() as s:
        report = get_export_report(job.id, s, user)
    assert report.clips_rendered >= 2
    assert report.url == done.result_url
    assert (report.fps, report.width, report.height) == (5, 320, 240)
    from komaverse.db_models import UsageEventRow

    with factory() as s:
        usage = s.query(UsageEventRow).filter_by(kind="export").all()
    # Exported frames as the compute proxy: one unit per rendered frame.
    assert usage and usage[-1].tokens == max(1, round(report.rendered_sec * report.fps))


def test_export_reports_clamps_and_skips(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Honest"), s, user)
    with factory() as s:
        p1 = upload(s, user, project.id)
        p2 = upload(s, user, project.id)
    with factory() as s:
        d1 = detect_panels(p1, DetectRequest(detector="heuristic"), s, user)
        d2 = detect_panels(p2, DetectRequest(detector="heuristic"), s, user)
    with factory() as s:
        scenes = list_scenes(project.id, s, user)
        add_clip(
            scenes[0].id,
            ClipCreate(kind="panel", source_id=d1.panels[0].id, duration=0.4),
            s,
            user,
        )
        add_clip(
            scenes[0].id,
            ClipCreate(kind="panel", source_id=d2.panels[0].id, duration=0.4),
            s,
            user,
        )
    with factory() as s:
        dead = settings.media_dir / s.get(PageRow, p2).image_path
    dead.unlink()  # second clip becomes unresolvable
    with factory() as s:
        out = render_project(project.id, s, fps=60, width=641, height=481)
    assert (out["fps"], out["width"], out["height"]) == (30, 640, 480)
    assert out["clips"] == 1
    assert any("fps" in w for w in out["warnings"])
    assert any("even" in w for w in out["warnings"])
    assert any("skipped" in w for w in out["warnings"])
    assert out["duration_sec"] == pytest.approx(0.4, abs=0.15)


def test_export_rejects_empty_timeline(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Empty"), s, user)
    with factory() as s, pytest.raises(HTTPException) as exc:
        export_project(project.id, ExportRequest(), BackgroundTasks(), s, user)
    assert exc.value.status_code == 400


def test_export_job_failure(factory, user):
    project_id, scene_id, panels = _scene_with_panels(factory, user, name="Fail")
    with factory() as s:
        add_clips_batch(
            scene_id,
            ClipBatchCreate(clips=[ClipCreate(kind="panel", source_id=panels[0])]),
            s,
            user,
        )
    with factory() as s:
        page_id = s.execute(
            select(PageRow).where(PageRow.project_id == project_id)
        ).scalars().first().id
        dead = settings.media_dir / s.get(PageRow, page_id).image_path
    dead.unlink()
    with factory() as s:
        job = export_project(project_id, ExportRequest(), BackgroundTasks(), s, user)
    run_export_job(job.id, factory)
    with factory() as s:
        done = get_job(job.id, s, user)
    assert done.status == "failed"
    assert "resolve" in (done.error or "")


def test_export_report_isolation(factory, user):
    project_id, scene_id, panels = _scene_with_panels(factory, user, name="Iso")
    with factory() as s:
        add_clips_batch(
            scene_id,
            ClipBatchCreate(clips=[ClipCreate(kind="panel", source_id=panels[0])]),
            s,
            user,
        )
    with factory() as s:
        job = export_project(
            project_id, ExportRequest(fps=5, width=320, height=240), BackgroundTasks(), s, user
        )
    run_export_job(job.id, factory)
    with factory() as s:
        other = create_user(s, email="repx@test.local")
    with factory() as s, pytest.raises(HTTPException) as exc:
        get_export_report(job.id, s, other)
    assert exc.value.status_code == 404
    with factory() as s, pytest.raises(HTTPException) as exc:
        download_export(job.id, s, other)
    assert exc.value.status_code == 404
    with factory() as s:
        dl = download_export(job.id, s, user)
    assert str(dl.path).endswith(".mp4") and dl.media_type == "video/mp4"


def test_blend_modes_honored(factory, user):
    from PIL import Image as PILImage

    def solid(rgb):
        buf = io.BytesIO()
        PILImage.new("RGB", (64, 64), rgb).save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    with factory() as s:
        project = create_project(ProjectCreate(name="Blend"), s, user)
    with factory() as s:
        frame = create_frame(
            project.id, FrameCreate(name="B", width=64, height=64), s, user
        )

    def composite_with(blend):
        with factory() as s:
            put_frame(
                frame.id,
                FramePut(
                    layers=[
                        LayerPut(name="base", layer_order=0, data_url=solid((200, 100, 50))),
                        LayerPut(
                            name="over",
                            layer_order=1,
                            data_url=solid((100, 200, 100)),
                            blend_mode=blend,
                        ),
                    ]
                ),
                s,
                user,
            )
        with factory() as s:
            row = s.get(FrameRow, frame.id)
            return composite_frame_image(row).convert("RGB").getpixel((32, 32))

    assert composite_with("normal") == (100, 200, 100)
    for mode in (
        "multiply", "screen", "overlay", "difference",
        "darken", "lighten", "soft-light", "hard-light",
    ):
        assert composite_with(mode) != (100, 200, 100), mode


def test_motion_frame_moves_pixels_for_every_motion():
    """Motions must visibly drift across a clip; none must hold still."""
    import numpy as np

    from komaverse.renderer import motion_frame

    rng = np.random.default_rng(7)
    base = rng.integers(0, 255, (360, 640, 3)).astype("uint8")
    for motion in ("zoom-in", "zoom-out", "pan-left", "pan-right", "pan-up", "pan-down"):
        first = motion_frame(base, 320, 180, 0.0, motion)
        last = motion_frame(base, 320, 180, 1.0, motion)
        assert not np.array_equal(first, last), motion
    still = motion_frame(base, 320, 180, 1.0, "none")
    assert np.array_equal(still, motion_frame(base, 320, 180, 0.0, "none"))


def _page_png(factory, user, project_id, w=200, h=100):
    import io as _io

    from fastapi import UploadFile as _UploadFile
    from PIL import Image as _Image
    from starlette.datastructures import Headers as _Headers

    buf = _io.BytesIO()
    _Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    with factory() as s:
        return upload_page(
            project_id,
            _UploadFile(
                file=_io.BytesIO(buf.getvalue()),
                filename="p.png",
                headers=_Headers({"content-type": "image/png"}),
            ),
            s,
            user,
        )


def test_frame_from_media_page(factory, user):
    from komaverse.api.animate import FrameFromMedia, frame_from_media

    with factory() as s:
        project = create_project(ProjectCreate(name="Framing"), s, user)
    _page_png(factory, user, project.id)
    with factory() as s:
        schema = frame_from_media(
            project.id, FrameFromMedia(source="page 1"), s, user
        )
    assert (schema.width, schema.height) == (200, 100)
    assert len(schema.layers) == 1


def test_frame_from_panel_crops(factory, user):
    from komaverse.api.animate import FrameFromMedia, frame_from_media
    from komaverse.db_models import PanelRow

    with factory() as s:
        project = create_project(ProjectCreate(name="Framing"), s, user)
    page = _page_png(factory, user, project.id)
    with factory() as s:
        row = PanelRow(page_id=page.id, x=20.0, y=10.0, w=60.0, h=40.0)
        s.add(row)
        s.commit()
        panel_id = row.id
    with factory() as s:
        schema = frame_from_media(
            project.id, FrameFromMedia(source=f"@panel:{panel_id}"), s, user
        )
    assert (schema.width, schema.height) == (60, 40)
    assert "panel" in schema.name


def test_frame_from_unknown_media_rejected(factory, user):
    from fastapi import HTTPException

    from komaverse.api.animate import FrameFromMedia, frame_from_media

    with factory() as s:
        project = create_project(ProjectCreate(name="Framing"), s, user)
    with factory() as s:
        with pytest.raises(HTTPException):
            frame_from_media(
                project.id, FrameFromMedia(source="@panel:nope"), s, user
            )


def test_sequences_scope_frames(factory, user):
    from komaverse.api.animate import (
        FrameOrder,
        SequenceCreate,
        SequencePatch,
        create_sequence,
        delete_sequence,
        duplicate_sequence,
        list_sequences,
        order_frames,
        order_sequence_frames,
        patch_sequence,
    )

    with factory() as s:
        project = create_project(ProjectCreate(name="Seq"), s, user)
    with factory() as s:
        assert list_sequences(project.id, s, user) == []
        a = create_sequence(project.id, SequenceCreate(name="A"), s, user)
        b = create_sequence(project.id, SequenceCreate(name="B"), s, user)
        assert [x.name for x in list_sequences(project.id, s, user)] == ["A", "B"]
    with factory() as s:
        fa = create_frame(project.id, FrameCreate(name="a1", sequence_id=a.id), s, user)
        fb = create_frame(project.id, FrameCreate(name="b1", sequence_id=b.id), s, user)
        assert (fa.sequence_id, fb.sequence_id) == (a.id, b.id)
        # Legacy project-wide reorder rejects a cross-sequence set...
        with pytest.raises(HTTPException):
            order_frames(project.id, FrameOrder(frame_ids=[fa.id, fb.id]), s, user)
        # ...while the per-sequence route accepts its exact set.
        got = order_sequence_frames(a.id, FrameOrder(frame_ids=[fa.id]), s, user)
        assert [f.id for f in got] == [fa.id]
        with pytest.raises(HTTPException):
            order_sequence_frames(a.id, FrameOrder(frame_ids=[fa.id, fb.id]), s, user)
        renamed = patch_sequence(a.id, SequencePatch(name="Alpha"), s, user)
        assert renamed.name == "Alpha"
        copy = duplicate_sequence(a.id, s, user)
        assert copy.name == "Alpha copy" and len(copy.frames) == 1
        assert copy.frames[0].name == "a1"
        delete_sequence(copy.id, s, user)
    with factory() as s:
        assert [x.name for x in list_sequences(project.id, s, user)] == ["Alpha", "B"]
        # Frames with no sequence join the first one on next create.
        legacy = create_frame(project.id, FrameCreate(name="legacy"), s, user)
        assert legacy.sequence_id == a.id


def test_legacy_frames_auto_group(factory, user):
    from komaverse.api.animate import list_sequences

    with factory() as s:
        project = create_project(ProjectCreate(name="Legacy"), s, user)
    with factory() as s:
        create_frame(project.id, FrameCreate(name="one"), s, user)
        create_frame(project.id, FrameCreate(name="two"), s, user)
    # No sequences yet: the next frame triggers the auto-group migration.
    with factory() as s:
        third = create_frame(project.id, FrameCreate(name="three"), s, user)
    with factory() as s:
        seqs = list_sequences(project.id, s, user)
        assert len(seqs) == 1 and seqs[0].name == "Sequence 1"
        assert [f.name for f in seqs[0].frames] == ["one", "two", "three"]
        assert third.sequence_id == seqs[0].id


def test_list_sequences_rescues_legacy_orphan_frames(factory, user):
    """Pre-sequence rows (frames, zero sequences): listing creates
    Sequence 1 and adopts the orphans, so Draw never dead-ends on
    'No frames yet'. Frameless projects still list empty."""
    with factory() as s:
        project = create_project(ProjectCreate(name="Legacy"), s, user)
        s.add(FrameRow(project_id=project.id, name="Old", sequence_id=None))
        s.commit()
    with factory() as s:
        (seq,) = list_sequences(project.id, s, user)
    assert seq.name == "Sequence 1"
    assert [f.name for f in seq.frames] == ["Old"]
    with factory() as s:
        (frame,) = list_frames(project.id, s, user)
    assert frame.sequence_id == seq.id


def _video_bytes(n=8, w=64, h=48) -> bytes:
    import os
    import tempfile

    import numpy

    path = tempfile.mktemp(suffix=".mp4")
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (w, h))
    for i in range(n):
        writer.write(numpy.full((h, w, 3), min(i * 30, 255), dtype=numpy.uint8))
    writer.release()
    with open(path, "rb") as f:
        data = f.read()
    os.unlink(path)
    return data


def test_video_upload_and_to_frames(factory, user):
    from komaverse.api.animate import (
        VideoToFrames,
        list_sequences,
        upload_video,
        video_to_frames,
    )

    with factory() as s:
        project = create_project(ProjectCreate(name="Vid"), s, user)
    with factory() as s:
        job = upload_video(
            project.id,
            UploadFile(
                file=io.BytesIO(_video_bytes()),
                filename="clip.mp4",
                headers=Headers({"content-type": "video/mp4"}),
            ),
            s,
            user,
        )
    assert job.kind == "video" and job.status == "done"
    assert job.seconds > 0
    with factory() as s:
        with pytest.raises(HTTPException, match="must be a video"):
            upload_video(
                project.id,
                UploadFile(
                    file=io.BytesIO(b"nope"),
                    filename="x.png",
                    headers=Headers({"content-type": "image/png"}),
                ),
                s,
                user,
            )
        frames = video_to_frames(job.id, VideoToFrames(max_frames=4), s, user)
    assert len(frames) == 4 and all(f.name.startswith("clip") for f in frames)
    assert all(len(f.layers) == 1 for f in frames)
    with factory() as s:
        seqs = list_sequences(project.id, s, user)
        assert len(seqs) == 1 and len(seqs[0].frames) == 4
        with pytest.raises(HTTPException):
            video_to_frames("nope", VideoToFrames(), s, user)


def test_duplicate_scene_and_scoped_export(factory, user):
    from komaverse.api.animate import duplicate_scene

    project_id, scene_id, panels = _scene_with_panels(factory, user)
    with factory() as s:
        add_clips_batch(
            scene_id,
            ClipBatchCreate(clips=[ClipCreate(kind="panel", source_id=panels[0], duration=0.5)]),
            s,
            user,
        )
        copy = duplicate_scene(scene_id, s, user)
        assert copy.name.endswith("copy")
        assert len(copy.clips) == 1 and copy.clips[0].source_id == panels[0]
    # Two timelines with clips: an unscoped export must ask which one.
    with factory() as s:
        with pytest.raises(HTTPException, match="Which timeline"):
            export_project(project_id, ExportRequest(fps=5, width=320, height=240), BackgroundTasks(), s, user)
        job = export_project(
            project_id,
            ExportRequest(fps=5, width=320, height=240, scene_id=copy.id),
            BackgroundTasks(),
            s,
            user,
        )
    run_export_job(job.id, factory)
    with factory() as s:
        done = get_job(job.id, s, user)
    assert done.status == "done" and (done.result_url or "").endswith(".mp4")


def test_clip_fx_validated_and_patched(factory, user):
    project_id, scene_id, panels = _scene_with_panels(factory, user)
    with factory() as s:
        clip = add_clip(
            scene_id,
            ClipCreate(
                kind="panel", source_id=panels[0], duration=1.0, motion="pan-up",
                transition="crossfade", transition_duration=0.4,
                fade_in=0.2, fade_out=0.3,
            ),
            s, user,
        )
    assert (clip.transition, clip.transition_duration, clip.fade_in, clip.fade_out) == ("crossfade", 0.4, 0.2, 0.3)
    with factory() as s:
        with pytest.raises(HTTPException):
            add_clip(scene_id, ClipCreate(kind="panel", source_id=panels[0], transition="star-wipe"), s, user)
        with pytest.raises(HTTPException):
            add_clip(scene_id, ClipCreate(kind="panel", source_id=panels[0], motion="barrel-roll"), s, user)
    with factory() as s:
        patched = patch_clip(clip.id, ClipPatch(transition="fade-black", transition_duration=5.0, fade_in=99.0), s, user)
    assert patched.transition == "fade-black"
    assert patched.transition_duration == 2.0  # clamped, never silent
    assert patched.fade_in == 10.0


def test_export_with_impact_flash(factory, user):
    project_id, scene_id, panels = _scene_with_panels(factory, user)
    with factory() as s:
        add_clip(scene_id, ClipCreate(kind="panel", source_id=panels[0], duration=1.0), s, user)
        second = add_clip(
            scene_id,
            ClipCreate(kind="panel", source_id=panels[1], duration=1.0, transition="impact"),
            s, user,
        )
    assert second.transition == "impact"
    with factory() as s:
        job = export_project(project_id, ExportRequest(fps=5, width=320, height=240), BackgroundTasks(), s, user)
    run_export_job(job.id, factory)
    with factory() as s:
        done = get_job(job.id, s, user)
    assert done.status == "done" and (done.result_url or "").endswith(".mp4")


def test_export_with_transition_and_fades(factory, user):
    project_id, scene_id, panels = _scene_with_panels(factory, user)
    with factory() as s:
        first = add_clip(scene_id, ClipCreate(kind="panel", source_id=panels[0], duration=1.0, fade_out=0.3), s, user)
        second = add_clip(
            scene_id,
            ClipCreate(kind="panel", source_id=panels[1], duration=1.0, motion="pan-down",
                       transition="crossfade", transition_duration=0.4, fade_in=0.2),
            s, user,
        )
    assert second.transition == "crossfade"
    with factory() as s:
        job = export_project(project_id, ExportRequest(fps=5, width=320, height=240), BackgroundTasks(), s, user)
    run_export_job(job.id, factory)
    with factory() as s:
        done = get_job(job.id, s, user)
        report = get_export_report(job.id, s, user)
    assert done.status == "done" and (done.result_url or "").endswith(".mp4")
    assert report.clips_rendered == 2
    video_path = settings.media_dir / (done.result_url or "").removeprefix("/media/")
    assert video_path.exists() and video_path.stat().st_size > 1000
