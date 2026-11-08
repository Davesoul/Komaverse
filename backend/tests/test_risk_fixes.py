"""Risk-fix regression: generic 500 envelope, sqlite parents, ffmpeg readiness,
plus progressive tab slices (summary / pages / timeline)."""

import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from starlette.requests import Request

from komaverse.api.scans import (
    ProjectCreate,
    create_project,
    get_project_pages,
    get_project_summary,
    get_project_timeline,
    list_projects,
    upload_page,
)
from komaverse.config import settings
from komaverse.db import _sqlite_file_for_url
from komaverse.db_models import Base
from komaverse.main import _unhandled_error_handler, app
from komaverse.observability import check_readiness


@pytest.fixture
def factory(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def user(factory):
    from conftest import create_user

    with factory() as s:
        return create_user(s)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/projects",
            "headers": [],
        }
    )


def test_generic_exception_maps_to_json_500():
    res = asyncio.run(_unhandled_error_handler(_request(), ValueError("boom")))
    assert res.status_code == 500
    assert Exception in app.exception_handlers


def test_sqlite_file_parsing():
    assert _sqlite_file_for_url("sqlite:///./komaverse.db").name == "komaverse.db"
    assert _sqlite_file_for_url("sqlite:////data/komaverse.db").parent.name == "data"
    assert _sqlite_file_for_url("sqlite:///:memory:") is None


def test_readiness_reports_ffmpeg(tmp_path):
    ok, checks = check_readiness("sqlite:///:memory:", tmp_path)
    assert "ffmpeg" in checks
    assert "database" in checks
    assert "media" in checks
    assert isinstance(ok, bool)


def _fake_png() -> bytes:
    from PIL import Image

    img = Image.new("L", (200, 200), 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_project_view_single_round_trip(factory, user):
    """GET /view returns summary counts plus pages in one response."""
    from komaverse.api.scans import get_project_view

    with factory() as s:
        project = create_project(ProjectCreate(name="V"), s, user)
    with factory() as s:
        upload_page(
            project.id,
            UploadFile(
                file=io.BytesIO(_fake_png()),
                filename="p1.png",
                headers=Headers({"content-type": "image/png"}),
            ),
            s,
            user,
        )
    with factory() as s:
        view = get_project_view(project.id, s, user)
        assert view.page_count == 1
        assert view.scene_count == 1
        assert len(view.pages) == 1
        assert view.pages[0].panels == []


def test_project_slices_progressive(factory, user):
    """Tabs bootstrap from summary + their own slice, never the full tree."""
    with factory() as s:
        project = create_project(ProjectCreate(name="Slice"), s, user)
    with factory() as s:
        upload_page(
            project.id,
            UploadFile(
                file=io.BytesIO(_fake_png()),
                filename="p1.png",
                headers=Headers({"content-type": "image/png"}),
            ),
            s,
            user,
        )
    with factory() as s:
        summaries = list_projects(s, user)
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.page_count == 1
        assert summary.name == "Slice"
        assert not hasattr(summary, "pages")
    with factory() as s:
        assert get_project_summary(project.id, s, user).page_count == 1
        pages = get_project_pages(project.id, s, user)
        assert len(pages) == 1
        timeline = get_project_timeline(project.id, s, user)
        assert len(timeline) == 1  # create_project seeds Scene 1
        assert timeline[0].clips == []
    with factory() as s:
        from conftest import create_user

        stranger = create_user(s, email="slice-other@test.local")
    with factory() as s, pytest.raises(HTTPException) as exc:
        get_project_pages(project.id, s, stranger)
    assert exc.value.status_code == 404
