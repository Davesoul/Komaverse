"""Shared test helpers: fast password hashing + user factory."""

import pytest

import komaverse.auth as auth
from komaverse.auth import hash_password
from komaverse.config import settings
from komaverse.db_models import UserRow


@pytest.fixture(autouse=True)
def _fast_passwords(monkeypatch):
    monkeypatch.setattr(auth, "PBKDF2_ROUNDS", 1000)


@pytest.fixture(autouse=True)
def _hermetic_ai_settings(monkeypatch):
    """Tests never touch real providers: ambient .env keys stay out.

    Every test starts with unconfigured cloud AI; tests that need a key
    set it explicitly with monkeypatch (reverted afterwards as usual).
    """
    monkeypatch.setattr(settings, "ai_provider", "cloud")
    monkeypatch.setattr(settings, "ai_api_key", None)
    monkeypatch.setattr(settings, "ai_base_url", None)
    monkeypatch.setattr(settings, "meta_api_key", None)
    monkeypatch.setattr(settings, "openrouter_api_key", None)


def create_user(session, email="u@test.local", password="password123", name="T"):
    row = UserRow(email=email, name=name, password_hash=hash_password(password))
    session.add(row)
    session.commit()
    return row
