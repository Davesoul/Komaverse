"""BYOK: encrypted per-user provider keys for generations.

Own-key generations bill the user's provider account instead of studio
credits; freemium stays daily-limited while paid tiers are unlimited.
Chat, voice, and detection keep using studio keys.
"""

from __future__ import annotations

import pytest
from conftest import create_user
from cryptography.fernet import Fernet
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import komaverse.api.ai as ai_mod
from komaverse.ai.providers import FakeProvider, get_provider
from komaverse.api.ai import GenerateRequest, generate, get_balance, run_ai_job
from komaverse.api.user_keys import KeySaveBody, list_keys, remove_key, save_key
from komaverse.config import settings
from komaverse.db_models import Base, SubscriptionRow, UserRow
from komaverse.user_keys import get_user_key


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    monkeypatch.setattr(settings, "paywall_enabled", True)
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def user(factory) -> UserRow:
    with factory() as s:
        return create_user(s)


@pytest.fixture
def vault(monkeypatch):
    monkeypatch.setattr(settings, "key_vault_secret", Fernet.generate_key().decode())
    return settings.key_vault_secret


def _save_cloud_key(s, user, raw="sk-cloud-USERKEY1234"):
    from komaverse.user_keys import set_user_key

    return set_user_key(s, user.id, "cloud", raw)


def test_vault_roundtrip_and_masked_list(factory, user, vault):
    with factory() as s:
        masked = _save_cloud_key(s, user)
        assert masked == "••••1234"
        assert get_user_key(s, user.id, "cloud") == "sk-cloud-USERKEY1234"
        rows = list_keys(s, user)
    assert [(r.provider, r.masked) for r in rows] == [("cloud", "••••1234")]
    assert "USERKEY" not in str(rows)


def test_vault_rejects_bad_provider_and_short_key(factory, user, vault):
    from komaverse.user_keys import set_user_key

    with factory() as s:
        with pytest.raises(KeyError):
            set_user_key(s, user.id, "mop", "sk-clouddddddd")
        with pytest.raises(ValueError):
            set_user_key(s, user.id, "cloud", "tiny")


def test_save_without_server_secret_is_503(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "key_vault_secret", None)
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            save_key(KeySaveBody(provider="openrouter", key="sk-or-USERKEY1234"), s, user)
    assert exc.value.status_code == 503


def test_generate_with_own_key_skips_credits(factory, user, vault, monkeypatch):
    monkeypatch.setattr(settings, "free_monthly_credits", 0)
    with factory() as s:
        _save_cloud_key(s, user)
    with factory() as s:
        job = generate(
            GenerateRequest(kind="image", prompt="a robot", provider="cloud"),
            BackgroundTasks(),
            s,
            user,
        )
    assert job.cost_credits == 0
    assert job.used_own_key is True
    with factory() as s:
        assert get_balance(s, user.id) == 0


def test_run_ai_job_uses_own_key(factory, user, vault, monkeypatch):
    seen = {}

    def _fake(name, api_key=None):
        seen["api_key"] = api_key
        return FakeProvider()

    monkeypatch.setattr(ai_mod, "get_provider", _fake)
    with factory() as s:
        _save_cloud_key(s, user, raw="sk-cloud-OWNKEY9999")
    with factory() as s:
        job = generate(
            GenerateRequest(kind="image", prompt="a robot", provider="cloud"),
            BackgroundTasks(),
            s,
            user,
        )
    run_ai_job(job.id, factory)
    assert seen["api_key"] == "sk-cloud-OWNKEY9999"


def test_own_key_freemium_daily_limit(factory, user, vault, monkeypatch):
    monkeypatch.setattr(settings, "own_key_freemium_daily_limit", 1)
    with factory() as s:
        _save_cloud_key(s, user)
    with factory() as s:
        generate(
            GenerateRequest(kind="image", prompt="one", provider="cloud"),
            BackgroundTasks(),
            s,
            user,
        )
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            generate(
                GenerateRequest(kind="image", prompt="two", provider="cloud"),
                BackgroundTasks(),
                s,
                user,
            )
    assert exc.value.status_code == 429


def test_own_key_unlimited_with_subscription(factory, user, vault, monkeypatch):
    monkeypatch.setattr(settings, "own_key_freemium_daily_limit", 1)
    with factory() as s:
        _save_cloud_key(s, user)
        s.add(SubscriptionRow(user_id=user.id, stripe_subscription_id="sub_1", status="active"))
        s.commit()
    with factory() as s:
        for i in range(2):
            job = generate(
                GenerateRequest(kind="image", prompt=f"pic {i}", provider="cloud"),
                BackgroundTasks(),
                s,
                user,
            )
            assert job.used_own_key is True


def test_delete_key_falls_back_to_studio_billing(factory, user, vault, monkeypatch):
    monkeypatch.setattr(settings, "free_monthly_credits", 0)
    with factory() as s:
        _save_cloud_key(s, user)
        assert remove_key("cloud", s, user) is None
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            generate(
                GenerateRequest(kind="image", prompt="a robot", provider="cloud"),
                BackgroundTasks(),
                s,
                user,
            )
    assert exc.value.status_code == 402


def test_provider_key_override_unit():
    assert get_provider("openrouter", api_key="sk-or-x")._key() == "sk-or-x"
    assert get_provider("meta", api_key="sk-meta-x")._key() == "sk-meta-x"
    assert get_provider("cloud", api_key="sk-cloud-x")._api_key() == "sk-cloud-x"
    assert get_provider("cloud")._api_key() is None
