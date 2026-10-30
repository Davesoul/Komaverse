"""M2 scan-flow tests: direct sync calls against an isolated DB + media dir.

No event loop, no sockets — runs anywhere, including restricted sandboxes.
Route registration is asserted separately so URL wiring stays covered.
"""

from __future__ import annotations

import io

import pytest
from conftest import create_user
from fastapi import HTTPException, UploadFile
from fastapi.routing import APIRoute
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import Headers

from komaverse.api.scans import (
    DetectRequest,
    PanelCreate,
    PanelOrder,
    PanelPatch,
    ProjectCreate,
    add_panel,
    create_project,
    delete_page,
    delete_panel,
    delete_project,
    detect_panels,
    get_project,
    order_panels,
    panel_preview,
    patch_panel,
    project_storage,
    router,
    upload_page,
)
from komaverse.config import settings
from komaverse.db_models import Base, PanelRow, UserRow


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
    d.rectangle([360, 400, 560, 760], fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def upload(session: Session, user: UserRow, project_id: str, data: bytes, name="p1.png"):
    return upload_page(
        project_id,
        UploadFile(
            file=io.BytesIO(data),
            filename=name,
            headers=Headers({"content-type": "image/png"}),
        ),
        session,
        user,
    )


def test_delete_panel_blocked_when_clipped(factory, user):
    from komaverse.api.animate import (
        ClipCreate,
        add_clip,
        delete_clip,
        list_scenes,
    )

    with factory() as s:
        project = create_project(ProjectCreate(name="Guard"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        detected = detect_panels(page.id, DetectRequest(detector="heuristic"), s, user)
    panel_id = detected.panels[0].id
    with factory() as s:
        scenes = list_scenes(project.id, s, user)
        clip = add_clip(
            scenes[0].id,
            ClipCreate(kind="panel", source_id=panel_id, duration=1.0),
            s,
            user,
        )
    with factory() as s, pytest.raises(HTTPException) as exc:
        delete_panel(panel_id, s, user)
    assert exc.value.status_code == 409
    with factory() as s:
        delete_clip(clip.id, s, user)
    with factory() as s:
        delete_panel(panel_id, s, user)
    with factory() as s:
        assert panel_id not in [p.id for p in get_project(project.id, s, user).pages[0].panels]


def test_panel_preview(factory, user):
    from PIL import Image

    with factory() as s:
        project = create_project(ProjectCreate(name="Prev"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        detected = detect_panels(page.id, DetectRequest(detector="heuristic"), s, user)
    with factory() as s:
        res = panel_preview(detected.panels[0].id, s, user)
    assert res.media_type == "image/png"
    img = Image.open(io.BytesIO(res.body))
    assert max(img.size) <= 320 and min(img.size) > 0
    with factory() as s, pytest.raises(HTTPException) as exc:
        panel_preview("missing", s, user)
    assert exc.value.status_code == 404
    with factory() as s:
        big = panel_preview(detected.panels[0].id, s, user, px=960)
    big_img = Image.open(io.BytesIO(big.body))
    assert max(big_img.size) <= 960 and max(big_img.size) >= max(img.size)
    with factory() as s:
        clamped = panel_preview(detected.panels[0].id, s, user, px=99999)
    assert max(Image.open(io.BytesIO(clamped.body)).size) <= 1600


def test_scan_flow(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Ch1"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    assert (page.width, page.height) == (600, 800)

    with factory() as s:
        res = detect_panels(page.id, DetectRequest(detector="heuristic"), s, user)
    assert res.detector_used == "heuristic"
    assert res.fell_back is False
    assert len(res.panels) >= 3, res.panels

    first = res.panels[0]
    with factory() as s:
        patched = patch_panel(first.id, PanelPatch(x=first.x + 5), s, user)
    assert patched.x == first.x + 5

    with factory() as s:
        full = get_project(project.id, s, user)
    assert len(full.pages[0].panels) >= 3

    with factory() as s:
        delete_panel(first.id, s, user)
    with factory() as s:
        full = get_project(project.id, s, user)
    assert len(full.pages[0].panels) >= 2


def test_cloud_detector_falls_back_without_config(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Ch2"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        res = detect_panels(page.id, DetectRequest(detector="cloud"), s, user)
    assert res.detector_used == "heuristic"
    assert res.fell_back is True
    assert len(res.panels) >= 3


def test_yolo_detector_falls_back_without_weights(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "yolo_model", "models/does-not-exist.pt")
    with factory() as s:
        project = create_project(ProjectCreate(name="ChY"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        res = detect_panels(page.id, DetectRequest(detector="yolo"), s, user)
    assert res.detector_used == "heuristic"
    assert res.fell_back is True
    assert len(res.panels) >= 3


def test_yolo_leoxs22_routes_and_falls_back(factory, user, monkeypatch):
    import komaverse.detect as detect_mod

    monkeypatch.setattr(detect_mod, "YOLO_LEOXS", "models/does-not-exist.pt")
    with factory() as s:
        project = create_project(ProjectCreate(name="ChL"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        res = detect_panels(page.id, DetectRequest(detector="yolo-leoxs22"), s, user)
    assert res.detector_used == "heuristic"
    assert res.fell_back is True
    assert len(res.panels) >= 3


def test_weights_download_once_then_load(tmp_path, monkeypatch):
    from komaverse import detect as detect_mod

    source = tmp_path / "hosted-leox.pt"
    source.write_bytes(b"not-a-real-model")
    dest = tmp_path / "cache" / "leox.pt"
    monkeypatch.setattr(settings, "yolo_leoxs22_url", source.as_uri())
    assert detect_mod._ensure_weights(dest, source.as_uri(), "yolo-leoxs22") is True
    assert dest.read_bytes() == b"not-a-real-model"
    # Second call is a no-op (already cached), even with a bogus URL.
    assert detect_mod._ensure_weights(dest, "file:///does-not-exist-xyz.pt", "x") is True
    assert dest.read_bytes() == b"not-a-real-model"


def test_weights_download_failure_falls_back(factory, user, monkeypatch, tmp_path):
    import komaverse.detect as detect_mod

    missing = tmp_path / "leox-missing.pt"
    monkeypatch.setattr(detect_mod, "YOLO_LEOXS", str(missing))
    monkeypatch.setattr(settings, "yolo_leoxs22_url", "file:///does-not-exist-xyz.pt")
    with factory() as s:
        project = create_project(ProjectCreate(name="ChD"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        res = detect_panels(page.id, DetectRequest(detector="yolo-leoxs22"), s, user)
    assert not missing.exists()
    assert res.detector_used == "heuristic"
    assert res.fell_back is True
    assert len(res.panels) >= 3


def test_rejects_non_images(factory, user):
    from fastapi import HTTPException

    with factory() as s:
        project = create_project(ProjectCreate(name="Ch3"), s, user)
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            upload_page(
                project.id,
                UploadFile(
                    file=io.BytesIO(b"nope"),
                    filename="x.txt",
                    headers=Headers({"content-type": "text/plain"}),
                ),
                s,
                user,
            )
    assert exc.value.status_code == 400


def test_users_cannot_see_each_others_projects(factory, user):
    from fastapi import HTTPException

    with factory() as s:
        project = create_project(ProjectCreate(name="Mine"), s, user)
    with factory() as s:
        other = create_user(s, email="other@test.local")
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            get_project(project.id, s, other)
    assert exc.value.status_code == 404


def test_scan_routes_registered():
    paths = {r.path for r in router.routes if isinstance(r, APIRoute)}
    assert paths == {
        "/api/v1/projects",
        "/api/v1/projects/{project_id}",
        "/api/v1/projects/{project_id}/pages",
        "/api/v1/projects/{project_id}/storage",
        "/api/v1/pages/{page_id}",
        "/api/v1/pages/{page_id}/detect",
        "/api/v1/pages/{page_id}/panels",
        "/api/v1/pages/{page_id}/panels/order",
        "/api/v1/panels/{panel_id}",
        "/api/v1/panels/{panel_id}/preview",
    }


def test_redetect_needs_confirm(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="C"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        first = detect_panels(page.id, DetectRequest(detector="heuristic"), s, user)
    assert len(first.panels) >= 3
    with factory() as s, pytest.raises(HTTPException) as exc:
        detect_panels(page.id, DetectRequest(detector="heuristic"), s, user)
    assert exc.value.status_code == 409
    with factory() as s:
        second = detect_panels(
            page.id, DetectRequest(detector="heuristic", confirm=True), s, user
        )
    assert len(second.panels) >= 3


def test_cloud_detect_limited_per_user(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="R"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        for _ in range(10):
            detect_panels(
                page.id,
                DetectRequest(detector="cloud", confirm=True),
                s,
                user,
            )
        with pytest.raises(HTTPException) as exc:
            detect_panels(
                page.id, DetectRequest(detector="cloud", confirm=True), s, user
            )
    assert exc.value.status_code == 429


def test_detect_rejects_bad_direction(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="D"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s, pytest.raises(HTTPException) as exc:
        detect_panels(page.id, DetectRequest(direction="diagonal"), s, user)
    assert exc.value.status_code == 400


def test_reading_order_direction_unit():
    from komaverse.detect import Box, sort_reading_order

    left = Box(x=10.0, y=10.0, w=100.0, h=100.0)
    right = Box(x=200.0, y=10.0, w=100.0, h=100.0)
    rtl = sort_reading_order([left, right], 320, "rtl")
    assert [b["x"] for b in rtl] == [200.0, 10.0]
    ltr = sort_reading_order([left, right], 320, "ltr")
    assert [b["x"] for b in ltr] == [10.0, 200.0]


def test_detect_ltr_smoke_and_crop_url(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="L"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        res = detect_panels(page.id, DetectRequest(detector="heuristic", direction="ltr"), s, user)
    assert len(res.panels) >= 3
    assert res.panels[0].crop_url == f"/api/v1/panels/{res.panels[0].id}/preview"


def test_cloud_detector_sorts_server_side(factory, user, tmp_path, monkeypatch):
    from komaverse.ai.providers import CloudProvider
    from komaverse.detect import CloudDetector

    img = tmp_path / "page.png"
    img.write_bytes(fake_page())

    def _unsorted(_self, _image):
        return [
            {"x": 10.0, "y": 400.0, "w": 300.0, "h": 360.0},  # left first (wrong)
            {"x": 360.0, "y": 400.0, "w": 200.0, "h": 360.0},
        ]

    monkeypatch.setattr(CloudProvider, "detect_panels", _unsorted)
    boxes = CloudDetector().detect(img, "rtl")
    assert [b["x"] for b in boxes] == [360.0, 10.0]
    boxes = CloudDetector().detect(img, "ltr")
    assert [b["x"] for b in boxes] == [10.0, 360.0]


def test_boxes_from_text_tolerates_model_noise():
    from komaverse.ai.providers import _boxes_from_text

    text = (
        '[{"x": 100, "y": 100, "w": 200, "h": 200}, '
        '{"x": null, "y": 0, "w": 10, "h": 10}, '
        '{"x": -50, "y": 0, "w": 0, "h": 10}, '
        '{"x": 900, "y": 900, "w": 5000, "h": 10}] not json {oops'
    )
    boxes = _boxes_from_text(text, 600, 800)
    assert len(boxes) == 2  # null + degenerate dropped, overflow clamped
    assert boxes[0] == {"x": 60.0, "y": 80.0, "w": 120.0, "h": 160.0}
    assert boxes[1]["x"] == 540.0 and boxes[1]["w"] == 600.0
    assert _boxes_from_text("no boxes here", 600, 800) == []


def test_add_panel_validates_bounds(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="B"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_panel(page.id, PanelCreate(x=500, y=10, w=200, h=100), s, user)
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        add_panel(page.id, PanelCreate(x=-5, y=0, w=50, h=50), s, user)
    assert exc.value.status_code == 400
    with factory() as s:
        panel = add_panel(page.id, PanelCreate(x=10, y=10, w=50, h=50), s, user)
    assert panel.reading_order == 0
    with factory() as s:
        again = add_panel(page.id, PanelCreate(x=70, y=10, w=50, h=50), s, user)
    assert again.reading_order == 1


def test_patch_panel_validates_merged_box(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="P"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        panel = add_panel(page.id, PanelCreate(x=10, y=10, w=50, h=50), s, user)
    with factory() as s, pytest.raises(HTTPException) as exc:
        patch_panel(panel.id, PanelPatch(w=5000), s, user)
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        patch_panel(panel.id, PanelPatch(reading_order=-1), s, user)
    assert exc.value.status_code == 400
    with factory() as s:
        ok = patch_panel(panel.id, PanelPatch(x=20), s, user)
    assert ok.x == 20


def test_delete_panel_renumbers(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="R"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        res = detect_panels(page.id, DetectRequest(detector="heuristic"), s, user)
    assert len(res.panels) >= 3
    with factory() as s:
        delete_panel(res.panels[1].id, s, user)
    with factory() as s:
        full = get_project(project.id, s, user)
    orders = sorted(p.reading_order for p in full.pages[0].panels)
    assert orders == list(range(len(orders)))


def test_order_panels_sets_sequence(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="O"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        res = detect_panels(page.id, DetectRequest(detector="heuristic"), s, user)
    ids = [p.id for p in res.panels]
    with factory() as s, pytest.raises(HTTPException) as exc:
        order_panels(page.id, PanelOrder(panel_ids=ids[:-1]), s, user)
    assert exc.value.status_code == 400
    with factory() as s:
        ordered = order_panels(page.id, PanelOrder(panel_ids=ids[::-1]), s, user)
    assert [p.id for p in ordered] == ids[::-1]
    assert [p.reading_order for p in ordered] == list(range(len(ids)))


def test_delete_page_removes_file_and_renumbers(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="DP"), s, user)
    with factory() as s:
        first = upload(s, user, project.id, fake_page(), name="a.png")
        second = upload(s, user, project.id, fake_page(), name="b.png")
    path = settings.media_dir / first.image_url.removeprefix("/media/")
    assert path.exists()
    with factory() as s:
        delete_page(first.id, s, user)
    assert not path.exists()
    with factory() as s:
        full = get_project(project.id, s, user)
    assert [p.id for p in full.pages] == [second.id]
    assert full.pages[0].index == 0


def test_delete_page_blocked_when_clipped(factory, user):
    from komaverse.api.animate import ClipCreate, add_clip, list_scenes

    with factory() as s:
        project = create_project(ProjectCreate(name="DC"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        res = detect_panels(page.id, DetectRequest(detector="heuristic"), s, user)
    with factory() as s:
        scenes = list_scenes(project.id, s, user)
        add_clip(
            scenes[0].id,
            ClipCreate(kind="panel", source_id=res.panels[0].id, duration=1.0),
            s,
            user,
        )
    with factory() as s, pytest.raises(HTTPException) as exc:
        delete_page(page.id, s, user)
    assert exc.value.status_code == 409


def test_delete_project_removes_everything(factory, user):
    from komaverse.api.animate import create_frame
    from komaverse.api.animate import FrameCreate

    with factory() as s:
        project = create_project(ProjectCreate(name="DX"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
        frame = create_frame(
            project.id, FrameCreate(name="F", width=64, height=64), s, user
        )
    with factory() as s:
        detect_panels(page.id, DetectRequest(detector="heuristic"), s, user)
    page_file = settings.media_dir / page.image_url.removeprefix("/media/")
    assert page_file.exists()
    with factory() as s:
        delete_project(project.id, s, user)
    assert not page_file.exists()
    assert not (settings.media_dir / "projects" / project.id).exists()
    with factory() as s, pytest.raises(HTTPException) as exc:
        get_project(project.id, s, user)
    assert exc.value.status_code == 404


def test_project_storage_reports_usage(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="ST"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        out = project_storage(project.id, s, user)
    on_disk = settings.media_dir / page.image_url.removeprefix("/media/")
    assert out.bytes_used == on_disk.stat().st_size
    assert out.quota_bytes == settings.project_quota_mb * 1024 * 1024


def test_upload_rejects_over_quota(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "project_quota_mb", 0)
    with factory() as s:
        project = create_project(ProjectCreate(name="Q"), s, user)
    with factory() as s, pytest.raises(HTTPException) as exc:
        upload(s, user, project.id, fake_page())
    assert exc.value.status_code == 413


def test_upload_rejects_huge_dimensions(factory, user):
    from PIL import Image

    with factory() as s:
        project = create_project(ProjectCreate(name="HD"), s, user)
    img = Image.new("RGB", (12001, 10), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    with factory() as s, pytest.raises(HTTPException) as exc:
        upload(s, user, project.id, buf.getvalue(), name="wide.png")
    assert exc.value.status_code == 400


def test_preview_clamps_oob_legacy_box(factory, user):
    from PIL import Image

    with factory() as s:
        project = create_project(ProjectCreate(name="V"), s, user)
    with factory() as s:
        page = upload(s, user, project.id, fake_page())
    with factory() as s:
        row = PanelRow(page_id=page.id, x=-50, y=-50, w=5000, h=5000)
        s.add(row)
        s.commit()
        pid = row.id
    with factory() as s:
        res = panel_preview(pid, s, user)
    assert res.media_type == "image/png"
    img = Image.open(io.BytesIO(res.body))
    assert max(img.size) <= 320 and min(img.size) > 0
