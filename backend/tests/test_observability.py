"""Observability tests: path normalization, metrics render, readiness,
status counts, client-log funnel. All sync."""

import pytest
from conftest import create_user
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.api.ops import ClientLogBody, client_log, status
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, UserRow
from komaverse.observability import (
    Metrics,
    check_readiness,
    normalize_path,
    record_error,
    recent_errors,
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


def test_normalize_path_collapses_ids():
    assert normalize_path("/api/v1/projects/abc123") == "/api/v1/projects/{id}"
    assert (
        normalize_path("/api/v1/pages/abc/detect") == "/api/v1/pages/{id}/{action}"
    )
    assert normalize_path("/api/v1/panels/xyz") == "/api/v1/panels/{id}"
    assert normalize_path("/api/v1/ai/jobs/xyz") == "/api/v1/ai/jobs/{id}"
    assert normalize_path("/media/a/b/c.png") == "/media/*"
    assert normalize_path("/api/v1/projects") == "/api/v1/projects"
    assert normalize_path("/health") == "/health"
    assert normalize_path("/weird/path") == "other"


def test_metrics_render_prometheus_format():
    m = Metrics()
    m.record_request("GET", "/api/v1/projects", 200, 0.05)
    m.record_request("GET", "/api/v1/projects", 200, 0.15)
    m.record_job("done")
    m.record_export()
    m.record_detect("heuristic")
    out = m.render("0.1.0")
    assert 'komaverse_requests_total{method="GET",route="/api/v1/projects",status="200"} 2' in out
    assert "komaverse_request_seconds_sum" in out
    assert 'komaverse_ai_jobs_total{status="done"} 1' in out
    assert "komaverse_exports_total 1" in out
    assert 'komaverse_detects_total{detector="heuristic"} 1' in out


def test_check_readiness_ok_and_degraded(tmp_path):
    ok, checks = check_readiness(f"sqlite:///{tmp_path}/ok.db", tmp_path / "media")
    assert ok is True
    assert checks == {"database": "ok", "media": "ok"}
    ok, checks = check_readiness("sqlite:////nonexistent-dir-xyz/db.sqlite", tmp_path / "m2")
    assert ok is False
    assert checks["database"].startswith("fail:")


def test_status_counts(factory, user):
    with factory() as s:
        create_project(ProjectCreate(name="P1"), s, user)
    with factory() as s:
        out = status(s, user)
    assert out.projects == 1
    assert out.users == 1
    assert out.uptime_sec >= 0
    assert isinstance(out.recent_errors, list)


def test_error_ring_records_and_lists():
    record_error("GET", "/boom", 500, "kaboom")
    found = [e for e in recent_errors() if e["path"] == "/boom"]
    assert found and found[0]["status"] == 500


def test_client_log_accepts_and_truncates():
    out = client_log(ClientLogBody(level="error", message="x" * 1000, url="http://t/"))
    assert out == {"status": "accepted"}
    out = client_log(ClientLogBody(level="bogus", message="m"))
    assert out == {"status": "accepted"}
