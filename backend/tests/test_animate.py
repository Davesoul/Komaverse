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
    list_scenes,
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
