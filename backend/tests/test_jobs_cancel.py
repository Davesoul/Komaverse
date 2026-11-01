"""Job cancel: endpoint guards, worker skip, agent tool, MCP dispatch."""

import pytest
from conftest import create_user
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.agent import run_agent_turn, tool_cancel_job
from komaverse.api.ai import cancel_job, run_ai_job
from komaverse.api.animate import run_export_job
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, ExportReportRow, FrameRow, JobRow, UserRow


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


def seed_queued_job(factory, user, kind="image", project_id=None, status="queued"):
    with factory() as s:
        job = JobRow(user_id=user.id, project_id=project_id, kind=kind, status=status)
        s.add(job)
        s.commit()
        return job.id


def test_cancel_queued_job(factory, user):
    jid = seed_queued_job(factory, user)
    with factory() as s:
        stopped = cancel_job(jid, s, user)
    assert stopped.status == "cancelled"
    with factory() as s:
        assert s.get(JobRow, jid).status == "cancelled"


def test_cancel_running_job(factory, user):
    jid = seed_queued_job(factory, user, status="running")
    with factory() as s:
        stopped = cancel_job(jid, s, user)
    assert stopped.status == "cancelled"


def test_cancel_terminal_job_409(factory, user):
    jid = seed_queued_job(factory, user, status="done")
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            cancel_job(jid, s, user)
    assert exc.value.status_code == 409


def test_cancel_missing_and_foreign_job(factory, user):
    with factory() as s:
        other = create_user(s, email="other@t.local")
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            cancel_job("missing000000", s, user)
        assert exc.value.status_code == 404
        foreign = JobRow(user_id=other.id, kind="image", status="queued")
        s.add(foreign)
        s.commit()
        fid = foreign.id
    with factory() as s:
        with pytest.raises(HTTPException) as exc2:
            cancel_job(fid, s, user)
        assert exc2.value.status_code == 404


def test_cancelled_ai_job_never_runs(factory, user):
    jid = seed_queued_job(factory, user, status="cancelled")
    run_ai_job(jid, factory)
    with factory() as s:
        job = s.get(JobRow, jid)
        assert job.status == "cancelled"
        assert job.result_url is None
        assert s.query(FrameRow).count() == 0


def test_cancelled_export_never_runs(factory, user):
    with factory() as s:
        pid = create_project(ProjectCreate(name="Cancel"), s, user).id
    jid = seed_queued_job(factory, user, kind="export", project_id=pid, status="cancelled")
    run_export_job(jid, factory)
    with factory() as s:
        assert s.get(JobRow, jid).status == "cancelled"
        assert s.query(ExportReportRow).count() == 0


def test_tool_cancel_job_demo_turn(factory, user):
    with factory() as s:
        pid = create_project(ProjectCreate(name="Cancel"), s, user).id
    jid = seed_queued_job(factory, user, kind="video", project_id=pid)
    with factory() as s:
        out = run_agent_turn(f"cancel job [job {jid}]", pid, s, user)
    assert out["actions"] and out["actions"][0]["tool"] == "cancel_job"
    assert "Cancelled" in out["reply"]
    with factory() as s:
        assert s.get(JobRow, jid).status == "cancelled"


def test_tool_cancel_job_unknown(factory, user):
    with factory() as s:
        pid = create_project(ProjectCreate(name="Cancel"), s, user).id
    with factory() as s:
        assert "not found" in tool_cancel_job(s, user, "missing000000")
