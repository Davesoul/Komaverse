"""P0 tests: /media is authenticated + ownership-checked, traversal-safe."""

from __future__ import annotations

import pytest
from conftest import create_user
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse import storage
from komaverse.api.media import serve_media
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, JobRow, UserRow
from komaverse.main import rate_limit_key


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
def other(factory) -> UserRow:
    with factory() as s:
        return create_user(s, email="other@test.local", name="O")


def _project(factory, user, name="M"):
    with factory() as s:
        return create_project(ProjectCreate(name=name), s, user)


def test_owner_can_download_own_upload(factory, user):
    project = _project(factory, user)
    rel, _url = storage.save_upload(project.id, "page.png", b"\x89PNG-fake")
    with factory() as s:
        resp = serve_media(rel, s, user)
    assert str(resp.path).endswith("page.png")
    assert resp.headers["Cache-Control"] == "private, max-age=31536000, immutable"


def test_other_user_gets_404_no_leak(factory, user, other):
    project = _project(factory, user)
    rel, _url = storage.save_upload(project.id, "page.png", b"bytes")
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            serve_media(rel, s, other)
    assert exc.value.status_code == 404


def test_missing_file_is_404(factory, user):
    project = _project(factory, user)
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            serve_media(f"projects/{project.id}/nope.png", s, user)
    assert exc.value.status_code == 404


def test_traversal_is_404(factory, user):
    project = _project(factory, user)
    with factory() as s:
        for evil in (
            "../komaverse.db",
            f"projects/{project.id}/../../evil.png",
            "projects/..%2f..%2fx",
        ):
            with pytest.raises(HTTPException) as exc:
                serve_media(evil, s, user)
            assert exc.value.status_code == 404


def test_unknown_prefix_is_404(factory, user):
    dest = settings.media_dir / "misc" / "f.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"bytes")
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            serve_media("misc/f.png", s, user)
    assert exc.value.status_code == 404


def test_ai_output_scoped_to_job_owner(factory, user, other):
    with factory() as s:
        job = JobRow(user_id=user.id, kind="image", prompt="x", provider="fake")
        s.add(job)
        s.commit()
        job_id = job.id
    dest = settings.media_dir / "ai" / f"{job_id}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"PNG")
    with factory() as s:
        resp = serve_media(f"ai/{job_id}.png", s, user)
    assert str(resp.path).endswith(".png")
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            serve_media(f"ai/{job_id}.png", s, other)
        assert exc.value.status_code == 404


def test_exports_scoped_to_project_owner(factory, user, other):
    project = _project(factory, user)
    dest = settings.media_dir / "exports" / project.id / "job1.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"MP4")
    with factory() as s:
        resp = serve_media(f"exports/{project.id}/job1.mp4", s, user)
    assert str(resp.path).endswith(".mp4")
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            serve_media(f"exports/{project.id}/job1.mp4", s, other)
        assert exc.value.status_code == 404


def test_storage_resolve_rejects_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    (tmp_path / "media").mkdir()
    with pytest.raises(storage.MediaTraversalError):
        storage.resolve("../outside.txt")
    ok = storage.resolve("projects/p/file.png")
    assert str(ok).startswith(str((tmp_path / "media").resolve()))


def test_read_upload_capped_fails_fast():
    import io

    assert storage.read_upload_capped(io.BytesIO(b"ab"), 10) == b"ab"
    with pytest.raises(storage.UploadTooLargeError):
        storage.read_upload_capped(io.BytesIO(b"a" * 100), 10, chunk=8)


def test_rate_limit_key_collapses_ids():
    assert rate_limit_key("/api/v1/pages/abc123/detect") == rate_limit_key(
        "/api/v1/pages/zzz999/detect"
    )
    assert rate_limit_key("/api/v1/agent/chat") == "/api/v1/agent/chat"
    assert rate_limit_key("/api/v1/ai/generate") == "/api/v1/ai/generate"
    assert rate_limit_key("/api/v1/projects/abc123/export").endswith("/export")
    assert "abc123" not in rate_limit_key("/api/v1/projects/abc123/export")
    assert rate_limit_key("/media/projects/p/f.png") == "/media/*"
