"""Usage telemetry: persisted events + per-period summary."""

import io
from datetime import datetime, timedelta

import pytest
from conftest import create_user
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers

from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, UsageEventRow, UserRow


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    monkeypatch.setattr(settings, "ai_api_key", None)
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def user(factory) -> UserRow:
    with factory() as s:
        return create_user(s)


def seed_project(factory, user, name="Usage"):
    with factory() as s:
        return create_project(ProjectCreate(name=name), s, user).id


def test_usage_summary_buckets_by_day(factory, user):
    from komaverse.api.usage import usage_summary

    now = datetime.now()
    with factory() as s:
        for age_days, kind, label in [
            (0, "chat", "demo"),
            (1, "detect", "heuristic"),
            (10, "image", "fake:default"),
            (40, "video", "openrouter:default"),
        ]:
            s.add(
                UsageEventRow(
                    user_id=user.id,
                    kind=kind,
                    label=label,
                    tokens=100,
                    cost=5.0,
                    created_at=now - timedelta(days=age_days),
                )
            )
        s.commit()
    with factory() as s:
        week = usage_summary(7, s, user)
    assert week.totals == {"chat": 1, "detect": 1}
    assert week.tokens_total == 200
    with factory() as s:
        month = usage_summary(30, s, user)
    assert month.totals.get("image") == 1
    with factory() as s:
        everything = usage_summary(0, s, user)
    assert everything.totals.get("video") == 1
    assert everything.cost_total == 20.0
    days = [p.day for p in everything.series]
    assert days == sorted(days)
    assert any(p.calls > 0 for p in everything.series)


def test_chat_turn_records_usage(factory, user):
    from komaverse.agent import run_agent_turn
    from komaverse.db_models import UsageEventRow

    pid = seed_project(factory, user)
    with factory() as s:
        before = s.query(UsageEventRow).count()
    with factory() as s:
        run_agent_turn("project status", pid, s, user)
    with factory() as s:
        rows = s.query(UsageEventRow).all()
    assert len(rows) == before + 1
    assert rows[-1].kind == "chat"
    assert rows[-1].tokens and rows[-1].tokens > 0


def test_detect_records_detector(factory, user):
    from PIL import Image, ImageDraw

    from komaverse.api.scans import DetectRequest, detect_panels, upload_page
    from komaverse.db_models import UsageEventRow

    img = Image.new("L", (600, 800), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 560, 360], fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    pid = seed_project(factory, user)
    with factory() as s:
        page = upload_page(
            pid,
            UploadFile(
                file=io.BytesIO(buf.getvalue()),
                filename="p.png",
                headers=Headers({"content-type": "image/png"}),
            ),
            s,
            user,
        )
    with factory() as s:
        detect_panels(
            page.id, DetectRequest(detector="heuristic", confirm=True), s, user
        )
    with factory() as s:
        rows = s.query(UsageEventRow).filter_by(kind="detect").all()
    assert rows and rows[-1].label == "heuristic"
    # 600x800 page -> 2x2 tiles of 512px: 85 + 170 * 4 vision-token estimate.
    assert rows[-1].tokens == 85 + 170 * 4


def test_generate_records_model_and_cost(factory, user):
    from komaverse.agent import tool_generate
    from komaverse.db_models import UsageEventRow

    pid = seed_project(factory, user)
    with factory() as s:
        summary = tool_generate(s, user, pid, "a robot", factory)
    assert ".png" in summary
    with factory() as s:
        rows = s.query(UsageEventRow).filter_by(kind="image").all()
    assert rows and "fake" in (rows[-1].label or "")
    assert rows[-1].cost == 0
