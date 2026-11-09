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


def test_agent_config_trace_defaults_round_trip(factory, user, saved_settings):
    """The Brain Drawing section persists linework frontend + detail + thin."""
    with factory() as s:
        out = agent_config(
            AgentConfigBody(
                trace_mode="anilines-detail", trace_detail="fast", trace_thin=False
            ),
            session=s,
            user=user,
        )
    assert out.trace_mode == "anilines-detail"
    assert out.trace_detail == "fast"
    assert out.trace_thin is False
    assert settings.trace_mode == "anilines-detail"
    assert settings.trace_detail == "fast"
    assert settings.trace_thin == "false"
    with factory() as s:
        assert get_ai_overrides(s)["trace_mode"] == "anilines-detail"
        out = agent_config(
            AgentConfigBody(trace_mode="", trace_detail=""), session=s, user=user
        )
        assert "trace_mode" not in out.overrides
        assert "trace_detail" not in out.overrides
        assert settings.trace_mode == "ink"
        assert settings.trace_detail == "auto"


def test_agent_config_trace_raw_round_trip(factory, user, saved_settings):
    """The Brain Direct switch persists; null leaves it alone."""
    with factory() as s:
        out = agent_config(AgentConfigBody(trace_raw=True), session=s, user=user)
    assert out.trace_raw is True
    assert settings.trace_raw == "true"
    with factory() as s:
        out = agent_config(AgentConfigBody(), session=s, user=user)
        assert out.trace_raw is True
        out = agent_config(
            AgentConfigBody(trace_raw=False), session=s, user=user
        )
        assert out.trace_raw is False
        assert "trace_raw" in out.overrides


def test_agent_config_second_user_is_forbidden(factory, user, saved_settings):
    """Studio-wide config is first-account-only; members get 403."""
    with factory() as s:
        other = create_user(s, email="member@test.local")
        other_id = other.id
    with factory() as s:
        other = s.get(UserRow, other_id)
        with pytest.raises(HTTPException) as exc:
            agent_config(AgentConfigBody(trace_mode="edges"), session=s, user=other)
    assert exc.value.status_code == 403
    with factory() as s:
        out = agent_config(
            AgentConfigBody(trace_mode="edges"), session=s, user=user
        )
        assert out.trace_mode == "edges"


def test_agent_config_trace_mode_rejects_lab_only(factory, user, saved_settings):
    """linedraw is a lab comparison, not an agent frontend: 400, unchanged."""
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            agent_config(AgentConfigBody(trace_mode="linedraw"), session=s, user=user)
    assert exc.value.status_code == 400
    assert settings.trace_mode == "ink"


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


def test_baseten_brain_override_roundtrip(factory, saved_settings):
    """Hackathon open-weights bonus: Baseten key + chat override persist."""
    from komaverse.studio_settings import (
        model_field,
        provider_for,
        set_ai_override,
    )

    with factory() as s:
        set_ai_override(s, "baseten_api_key", "bk-test")
        set_ai_override(s, "chat_provider", "baseten")
        applied = apply_ai_overrides(s)
    assert applied["baseten_api_key"] == "bk-test"
    assert applied["chat_provider"] == "baseten"
    assert settings.baseten_api_key == "bk-test"
    assert provider_for("chat") == "baseten"
    assert model_field("baseten", "chat") == "baseten_chat_model"


def test_agent_config_accepts_baseten_chat(factory, user, saved_settings):
    with factory() as s:
        out = agent_config(
            AgentConfigBody(
                chat_provider="baseten",
                baseten_api_key="bk-test",
            ),
            session=s,
            user=user,
        )
    assert out.providers["chat"] == "baseten"


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
    assert [m["id"] for m in out["chat"]["baseten"]] == [
        "meta-models/muse-spark-open-1026",
    ]
    assert out["chat"]["baseten"][0]["maker"] == "Meta"
    assert [m["id"] for m in out["meta_image"]] == ["muse-image-1.0"]
    assert out["chat"]["openrouter"][0]["maker"] == "OpenAI"
    assert out["chat"]["meta"][1]["maker"] == "Meta"
    assert out["image"][0]["maker"] == "Black Forest Labs"
    assert out["video"][0]["maker"] == "ByteDance"
    # Existing sections untouched.
    assert [m["id"] for m in out["image"]][0] == "black-forest-labs/flux-3-image-klein"
    assert [m["id"] for m in out["video"]][0] == "bytedance/seedance-2.0"
    for wanted in (
        "heygen/heygen-video-1",
        "minimax/hailuo-3-max",
        "alibaba/wan-3.0-prime",
        "bytedance/seedance-2.0-mini",
        "bytedance/seedance-2.0-fast",
        "alibaba/wan-2.6",
    ):
        assert wanted in [m["id"] for m in out["video"]], wanted


def test_provider_for_falls_back_to_global(factory, user):
    from komaverse.studio_settings import provider_for

    assert provider_for("chat") == "cloud"
    assert provider_for("vision") == "cloud"  # no Meta key in tests
    assert provider_for("image") == "cloud"
    assert provider_for("video") == "openrouter"
    assert provider_for("voice") == "free-tts"


def test_provider_for_vision_prefers_meta_when_keyed(factory, user, saved_settings, monkeypatch):
    """Vision defaults to the multimodal contributor model when keyed."""
    from komaverse.studio_settings import provider_for

    monkeypatch.setattr(settings, "vision_provider", None)
    monkeypatch.setattr(settings, "meta_api_key", "mk-test")
    assert provider_for("vision") == "meta"
    monkeypatch.setattr(settings, "vision_provider", "openrouter")
    assert provider_for("vision") == "openrouter"


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
        "voice": "free-tts",
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


def test_agent_config_keys_write_masked_clear(factory, user, saved_settings):
    """Keys paste via the widget: stored hot, masked on read, blank clears."""
    with factory() as s:
        out = agent_config(
            AgentConfigBody(meta_api_key="sk-test-123", openrouter_api_key="  "),
            session=s,
            user=user,
        )
    assert "meta_api_key" in out.keys_set
    assert settings.meta_api_key == "sk-test-123"
    with factory() as s:
        status = agent_status(session=s, user=user)
        assert "sk-test-123" not in status.model_dump_json()
        assert "meta_api_key" in status.keys_set
    with factory() as s:
        out = agent_config(
            AgentConfigBody(meta_api_key=""), session=s, user=user
        )
        assert "meta_api_key" not in get_ai_overrides(s)
def test_agent_config_accepts_portal_chat(factory, user, saved_settings):
    """Brain Chat row: Koma Portal maps to the studio ai_* models."""
    with factory() as s:
        out = agent_config(
            AgentConfigBody(chat_provider="portal", chat_model="koma-chat-1"),
            session=s,
            user=user,
        )
    assert out.providers["chat"] == "portal"
    assert out.chat_model == "koma-chat-1"
    assert settings.chat_provider == "portal"
    assert settings.ai_chat_model == "koma-chat-1"
    assert "chat_provider" in out.overrides
    assert "ai_chat_model" in out.overrides
def test_agent_config_accepts_portal_video(factory, user, saved_settings):
    """Brain Video row: Koma Portal is selectable and persists."""
    with factory() as s:
        out = agent_config(
            AgentConfigBody(video_provider="portal"),
            session=s,
            user=user,
        )
    assert out.providers["video"] == "portal"
    assert "video_provider" in out.overrides
    assert settings.video_provider == "portal"
