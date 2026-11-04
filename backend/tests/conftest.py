"""Shared test helpers: fast password hashing + user factory."""

import pytest

import komaverse.ai.providers as providers
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
    Model ids are pinned too: a user .env may override them (e.g. a
    cheaper chat variant), but the suite must assert code defaults.
    """
    monkeypatch.setattr(settings, "ai_provider", "cloud")
    monkeypatch.setattr(settings, "chat_provider", None)
    monkeypatch.setattr(settings, "vision_provider", None)
    monkeypatch.setattr(settings, "image_provider", None)
    monkeypatch.setattr(settings, "ai_api_key", None)
    monkeypatch.setattr(settings, "ai_base_url", None)
    monkeypatch.setattr(settings, "meta_api_key", None)
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    monkeypatch.setattr(settings, "meta_chat_model", "muse-spark-1.3")
    monkeypatch.setattr(settings, "meta_vision_model", None)
    monkeypatch.setattr(settings, "meta_image_model", "muse-image-1.0")
    monkeypatch.setattr(settings, "meta_transcribe_model", "muse-voice-transcribe-1.0")
    monkeypatch.setattr(settings, "meta_sam_model", "sam-3.1")


@pytest.fixture(autouse=True)
def _hermetic_video_caps(monkeypatch):
    """The capability matrix is live network: tests start offline.

    Every test starts with an empty cache and a failing fetch, so
    per-model validation skips unless the test injects its own matrix
    (tests that need data re-stub _fetch_video_caps themselves).
    """
    monkeypatch.setattr(providers, "_VIDEO_CAPS", {"at": 0.0, "models": {}})
    monkeypatch.setattr(providers, "_fetch_video_caps", lambda: {})


def create_user(session, email="u@test.local", password="password123", name="T"):
    row = UserRow(email=email, name=name, password_hash=hash_password(password))
    session.add(row)
    session.commit()
    return row
