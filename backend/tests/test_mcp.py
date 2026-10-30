"""MCP stdio server: protocol handshake, tool catalog, and dispatch."""

import io
import re
import time

import pytest
from conftest import create_user
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers

from komaverse.agent import run_agent_turn  # noqa: F401 — keeps agent importable here
from komaverse.api.mcp import handle_mcp
from komaverse.api.scans import create_project as create_project_ep
from komaverse.config import settings
from komaverse.db_models import Base, JobRow, UserRow
from komaverse.mcp_server import TOOLS, call_tool, dispatch


def wait_job(factory, job_id, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with factory() as s:
            job = s.get(JobRow, job_id)
            if job is not None and job.status in ("done", "failed"):
                return job.status
        time.sleep(0.5)
    raise TimeoutError(f"job {job_id} still running after {timeout}s")


def seed_project(factory, user, name="MCP"):
    from PIL import Image, ImageDraw

    from komaverse.api.scans import DetectRequest, ProjectCreate, detect_panels, upload_page

    img = Image.new("L", (600, 800), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 560, 360], fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    with factory() as s:
        project = create_project_ep(ProjectCreate(name=name), s, user)
    with factory() as s:
        page = upload_page(
            project.id,
            UploadFile(
                file=io.BytesIO(buf.getvalue()),
                filename="p1.png",
                headers=Headers({"content-type": "image/png"}),
            ),
            s,
            user,
        )
        detect_panels(
            page.id, DetectRequest(detector="heuristic", confirm=True), s, user
        )
    return project.id


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
def uid(factory, user) -> str:
    return user.id


def test_mcp_initialize():
    res = dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, None, "x"
    )
    assert res["id"] == 1
    assert res["result"]["protocolVersion"] == "2024-11-05"
    assert res["result"]["serverInfo"]["name"] == "komaverse-studio"
    assert "tools" in res["result"]["capabilities"]


def test_mcp_initialized_notification_is_silent(factory, uid):
    assert (
        dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}, factory, uid)
        is None
    )


def test_mcp_tools_list_covers_studio():
    names = {t["name"] for t in TOOLS()}
    assert {
        "project_status",
        "list_projects",
        "create_project",
        "create_frame",
        "detect_panels",
        "auto_direct",
        "export_video",
        "create_image",
        "create_video",
        "check_job",
        "add_clip",
        "add_subtitles",
        "list_takes",
        "add_cue",
        "frame_from_media",
    } <= names
    for tool in TOOLS():
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


def test_mcp_unknown_method_and_tool(factory, uid):
    res = dispatch({"jsonrpc": "2.0", "id": 2, "method": "nope"}, factory, uid)
    assert res["error"]["code"] == -32601
    res = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        },
        factory,
        uid,
    )
    assert res["error"]["code"] == -32602


def test_mcp_call_create_and_status(factory, uid):
    res = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "create_project", "arguments": {"name": "MCP Film"}},
        },
        factory,
        uid,
    )
    text = res["result"]["content"][0]["text"]
    assert "MCP Film" in text
    import re

    pid = re.search(r"\[project ([A-Za-z0-9_-]+)\]", text).group(1)
    res = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "project_status", "arguments": {"project_id": pid}},
        },
        factory,
        uid,
    )
    assert "MCP Film" in res["result"]["content"][0]["text"]


def test_mcp_missing_job_reports_text(factory, uid):
    res = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "check_job", "arguments": {"job_id": "missing000000"}},
        },
        factory,
        uid,
    )
    assert "not found" in res["result"]["content"][0]["text"]


def test_mcp_export_returns_immediately(factory, user):
    from komaverse import agent as agent_mod

    pid = seed_project(factory, user)
    with factory() as s:
        summary = agent_mod.tool_export(s, user, pid, factory, background=True)
    assert "[job " in summary
    jid = re.search(r"\[job ([A-Za-z0-9_-]+)\]", summary).group(1)
    assert wait_job(factory, jid) == "done"
    with factory() as s:
        seen = agent_mod.tool_check_job(s, user, jid)
    assert "done" in seen.lower() and ".mp4" in seen


def test_mcp_generate_returns_immediately(factory, user):
    from komaverse import agent as agent_mod

    pid = seed_project(factory, user)
    with factory() as s:
        summary = agent_mod.tool_generate(
            s, user, pid, "a moonlit robot", factory, background=True
        )
    assert "[job " in summary
    jid = re.search(r"\[job ([A-Za-z0-9_-]+)\]", summary).group(1)
    assert wait_job(factory, jid) == "done"
    with factory() as s:
        seen = agent_mod.tool_check_job(s, user, jid)
    assert ".png" in seen and "[frame " in seen


def test_mcp_http_export_is_async(factory, user):
    pid = seed_project(factory, user)
    status, payload = handle_mcp(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "export_video", "arguments": {"project_id": pid}},
        },
        factory,
        user.email,
    )
    assert status == 200
    text = payload["result"]["content"][0]["text"]
    assert "[job " in text
    jid = re.search(r"\[job ([A-Za-z0-9_-]+)\]", text).group(1)
    assert wait_job(factory, jid) == "done"


def test_mcp_http_unknown_user(factory):
    status, payload = handle_mcp(
        {"jsonrpc": "2.0", "id": 11, "method": "tools/list"}, factory, "nobody@x.io"
    )
    assert status == 401


def test_mcp_tool_error_becomes_is_error(factory, uid):
    res = dispatch(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "add_clip",
                "arguments": {"project_id": "missing000000", "kind": "frame", "source_id": "x"},
            },
        },
        factory,
        uid,
    )
    assert res["result"]["isError"] is True
