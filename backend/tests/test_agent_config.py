"""Studio AI config: widget-driven provider/model switch (persisted, hot),
cloud-fallback visibility, SQLite pragmas."""

import pytest
from conftest import create_user
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import komaverse.agent as agent_mod
from komaverse.api.agent import (
    AgentChatBody,
    AgentConfigBody,
    agent_config,
    agent_status,
    chat,
)
from komaverse.config import settings
from komaverse.db import _sqlite_pragmas
from komaverse.db_models import Base, UserRow
from komaverse.studio_settings import (
    apply_ai_overrides,
    get_ai_overrides,
    restore_ai_settings,
    snapshot_ai_settings,
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
def saved_settings():
    saved = snapshot_ai_settings()
    yield saved
    restore_ai_settings(saved)


def test_agent_config_switch_persists_and_applies(factory, user, saved_settings):
    with factory() as s:
        out = agent_config(
            AgentConfigBody(provider="meta", chat_model="muse-spark-test"),
            session=s,
            user=user,
        )
    assert out.provider == "meta"
    assert out.chat_model == "muse-spark-test"
    assert "ai_provider" in out.overrides
    assert "meta_chat_model" in out.overrides
    assert settings.ai_provider == "meta"
    assert settings.meta_chat_model == "muse-spark-test"
    with factory() as s:
        assert get_ai_overrides(s)["ai_provider"] == "meta"


def test_agent_config_blank_model_resets_to_env(factory, user, saved_settings):
    with factory() as s:
        agent_config(
            AgentConfigBody(provider="openrouter", image_model="qwen/qwen-image-2.1-turbo"),
            session=s,
            user=user,
        )
        agent_config(
            AgentConfigBody(provider="meta", image_model="muse-image-1.0"),
            session=s,
            user=user,
        )
        assert settings.openrouter_image_model == "qwen/qwen-image-2.1-turbo"
        assert settings.meta_image_model == "muse-image-1.0"
    # One blank clears the kind for every provider: no dormant row may
    # resurrect an old model on a later provider switch.
    with factory() as s:
        out = agent_config(
            AgentConfigBody(provider="openrouter", image_model=""), session=s, user=user
        )
        assert "openrouter_image_model" not in out.overrides
        assert "meta_image_model" not in out.overrides
        assert settings.openrouter_image_model == "black-forest-labs/flux-3-image-klein"
        assert settings.meta_image_model == "muse-image-1.0"


def test_agent_config_transcribe_model_roundtrip(factory, user, saved_settings):
    """The Brain's Transcribe row selects a Meta voice model, Meta-only."""
    from komaverse.studio_settings import provider_for

    assert provider_for("transcribe") == "meta"
    with factory() as s:
        out = agent_config(
            AgentConfigBody(transcribe_model="muse-voice-transcribe-2.0"),
            session=s,
            user=user,
        )
    assert out.transcribe_model == "muse-voice-transcribe-2.0"
    assert out.providers["transcribe"] == "meta"
    assert "meta_transcribe_model" in out.overrides
    assert settings.meta_transcribe_model == "muse-voice-transcribe-2.0"
    with factory() as s:
        out = agent_config(
            AgentConfigBody(transcribe_model=""), session=s, user=user
        )
        assert "meta_transcribe_model" not in out.overrides
        assert settings.meta_transcribe_model == "muse-voice-transcribe-1.0"


def test_agent_config_rejects_unknown_provider(factory, user, saved_settings):
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            agent_config(AgentConfigBody(provider="nope"), session=s, user=user)
    assert exc.value.status_code == 400
    assert settings.ai_provider == "cloud"  # hermetic default untouched


def test_agent_config_rejects_offcatalog_openrouter_image(
    factory, user, saved_settings
):
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            agent_config(
                AgentConfigBody(provider="openrouter", image_model="nope/not-real"),
                session=s,
                user=user,
            )
    assert exc.value.status_code == 400


def test_agent_config_rejects_model_for_modelless_provider(
    factory, user, saved_settings
):
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            agent_config(
                AgentConfigBody(provider="fake", chat_model="anything"),
                session=s,
                user=user,
            )
    assert exc.value.status_code == 400


def seed_minimal_project(factory, user, name="Cfg"):
    from komaverse.api.scans import ProjectCreate, create_project

    with factory() as s:
        return create_project(ProjectCreate(name=name), s, user).id


def test_cloud_fallback_reports_error(factory, user, monkeypatch):
    """A failing cloud brain answers from demo AND says why (no silent stuck)."""
    pid = seed_minimal_project(factory, user)
    monkeypatch.setattr(settings, "ai_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")

    def boom(*a, **k):
        raise RuntimeError("cloud says 402")

    monkeypatch.setattr(agent_mod, "cloud_turn", boom)
    with factory() as s:
        out = agent_mod.run_agent_turn("hi", pid, s, user)
    assert out["brain"] == "demo"
    assert out["cloud_error"] and "402" in out["cloud_error"]


def test_demo_without_cloud_attempt_has_no_error(factory, user):
    """Unconfigured provider: plain demo, nothing failed."""
    pid = seed_minimal_project(factory, user)
    with factory() as s:
        out = agent_mod.run_agent_turn("hi", pid, s, user)
    assert out["brain"] == "demo"
    assert out.get("cloud_error") is None


def test_chat_endpoint_carries_cloud_error(factory, user, monkeypatch):
    pid = seed_minimal_project(factory, user)
    monkeypatch.setattr(settings, "ai_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")

    def boom(*a, **k):
        raise RuntimeError("cloud says 402")

    monkeypatch.setattr(agent_mod, "cloud_turn", boom)
    with factory() as s:
        reply = chat(AgentChatBody(project_id=pid, message="hi"), s, user)
    assert reply.brain == "demo"
    assert reply.cloud_error and "402" in reply.cloud_error


def test_apply_ai_overrides_roundtrip(factory, saved_settings):
    with factory() as s:
        from komaverse.studio_settings import set_ai_override

        set_ai_override(s, "ai_provider", "meta")
        applied = apply_ai_overrides(s)
    assert applied == {"ai_provider": "meta"}
    assert settings.ai_provider == "meta"


def test_sqlite_pragmas_enable_wal_and_timeout():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    _sqlite_pragmas(conn, None)
    assert conn.execute("PRAGMA journal_mode;").fetchone()[0] in ("wal", "memory")
    assert conn.execute("PRAGMA busy_timeout;").fetchone()[0] == 10000
    conn.close()


def test_model_catalog_serves_chat_options(factory, user):
    """Brain panel: verified chat/vision options per provider + meta image."""
    from komaverse.api.ai import ai_models

    with factory() as s:
        out = ai_models(user)
    assert [m["id"] for m in out["chat"]["meta"]] == [
        "muse-spark-1.3",
        "muse-spark-1.3-contributor",
    ]
    assert [m["id"] for m in out["chat"]["openrouter"]] == [
        "openai/gpt-4o-mini",
        "google/gemini-2.5-flash",
        "google/gemini-2.5-flash-lite",
        "anthropic/claude-haiku-4.5",
        "meta-llama/llama-4-maverick",
        "deepseek/deepseek-chat-v3.1",
    ]
    assert [m["id"] for m in out["vision"]["openrouter"]] == [
        "google/gemini-2.5-flash",
        "google/gemini-2.5-flash-lite",
        "anthropic/claude-haiku-4.5",
    ]
    assert [m["id"] for m in out["meta_image"]] == ["muse-image-1.0"]
    assert out["chat"]["openrouter"][0]["maker"] == "OpenAI"
    assert out["chat"]["meta"][1]["maker"] == "Meta"
    assert out["image"][0]["maker"] == "Black Forest Labs"
    assert out["video"][0]["maker"] == "ByteDance"
    # Existing sections untouched.
    assert [m["id"] for m in out["image"]][0] == "black-forest-labs/flux-3-image-klein"
    assert [m["id"] for m in out["video"]][0] == "bytedance/seedance-2.0"


def test_provider_for_falls_back_to_global(factory, user):
    from komaverse.studio_settings import provider_for

    assert provider_for("chat") == "cloud"
    assert provider_for("vision") == "cloud"
    assert provider_for("image") == "cloud"
    assert provider_for("video") == "openrouter"


def test_agent_config_mixed_per_kind_routing(factory, user, saved_settings):
    """Meta for chat, OpenRouter for video: each family routes alone."""
    with factory() as s:
        out = agent_config(
            AgentConfigBody(
                chat_provider="meta",
                chat_model="muse-spark-1.3-contributor",
                image_provider="openrouter",
                image_model="qwen/qwen-image-2.1-turbo",
                video_model="google/veo-3.1",
            ),
            session=s,
            user=user,
        )
    assert out.providers == {
        "chat": "meta",
        "vision": "cloud",
        "image": "openrouter",
        "video": "openrouter",
        "transcribe": "meta",
    }
    assert out.chat_model == "muse-spark-1.3-contributor"
    assert out.image_model == "qwen/qwen-image-2.1-turbo"
    assert out.video_model == "google/veo-3.1"
    assert settings.meta_chat_model == "muse-spark-1.3-contributor"
    assert settings.openrouter_image_model == "qwen/qwen-image-2.1-turbo"
    assert settings.openrouter_video_model == "google/veo-3.1"


def test_agent_config_rejects_wrong_kind_provider(factory, user, saved_settings):
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            agent_config(
                AgentConfigBody(chat_provider="comfyui"), session=s, user=user
            )
    assert exc.value.status_code == 400


def test_status_lists_active_overrides(factory, user, saved_settings):
    with factory() as s:
        assert agent_status(s, user).overrides == []
        agent_config(AgentConfigBody(provider="meta"), session=s, user=user)
    with factory() as s:
        assert agent_status(s, user).overrides == ["ai_provider"]
