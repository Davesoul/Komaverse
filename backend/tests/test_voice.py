"""Voice synthesis (TTS) tests: ElevenLabs cloud + local free-tts bridge.

Provider HTTP is mocked throughout — no keys, servers, or tokens are
touched. The take-storage path (quota, save, stream check) runs for real
except the ffmpeg probe, which existing audio tests already cover.
"""

from __future__ import annotations

import pytest
from conftest import create_user
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from komaverse.api import audio as audio_api
from komaverse.api.agent import AgentConfigBody, agent_config, agent_status
from komaverse.api.audio import SynthCreate, synthesize_take
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, UserRow
from komaverse.studio_settings import restore_ai_settings, snapshot_ai_settings


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


@pytest.fixture
def project_id(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Voice"), s, user)
        return project.id


class FakeResp:
    def __init__(self, status=200, content=b"", payload=None, text=""):
        self.status_code = status
        self.content = content
        self._payload = payload
        self.text = text or (content[:200].decode("latin1") if content else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def no_stream(monkeypatch):
    monkeypatch.setattr(audio_api, "audio_has_stream", lambda path: True)


def test_parse_freetts_voice_shapes():
    from komaverse.ai.voice_local import parse_freetts_voice

    assert parse_freetts_voice(None) == ("kokoro", "af_sarah")
    assert parse_freetts_voice("") == ("kokoro", "af_sarah")
    assert parse_freetts_voice("kokoro:af_bella") == ("kokoro", "af_bella")
    assert parse_freetts_voice("af_nicole") == ("kokoro", "af_nicole")
    assert parse_freetts_voice("coqui-clone:/x/ref.wav") == (
        "coqui-clone",
        "/x/ref.wav",
    )


def test_synthesize_elevenlabs_ok(factory, user, project_id, saved_settings, monkeypatch, no_stream):
    import komaverse.ai.providers as providers

    monkeypatch.setattr(settings, "elevenlabs_api_key", "test-key")
    monkeypatch.setattr(settings, "voice_provider", "elevenlabs")
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, json=json)
        return FakeResp(200, content=b"ID3fake-mp3-bytes")

    monkeypatch.setattr(providers.httpx, "post", fake_post)
    with factory() as s:
        take = synthesize_take(
            project_id, SynthCreate(text="Hello village"), s, user
        )
    assert take.name == "Voice: Hello village"
    assert take.mime == "audio/mpeg"
    assert "/v1/text-to-speech/" in seen["url"]
    assert seen["headers"]["xi-api-key"] == "test-key"
    assert seen["json"]["text"] == "Hello village"
    assert seen["json"]["model_id"] == settings.elevenlabs_tts_model


def test_synthesize_elevenlabs_needs_key(factory, user, project_id, saved_settings):
    from komaverse.studio_settings import provider_for

    assert provider_for("voice") == "free-tts"  # no key in tests
    with factory() as s, pytest.raises(HTTPException) as exc:
        _elevenlabs_direct(s, user, project_id)
    assert exc.value.status_code == 400
    assert "ELEVENLABS_API_KEY" in exc.value.detail


def _elevenlabs_direct(s: Session, user: UserRow, project_id: str):
    from komaverse.config import settings as st

    st.voice_provider = "elevenlabs"
    st.elevenlabs_api_key = None
    try:
        return synthesize_take(project_id, SynthCreate(text="hi"), s, user)
    finally:
        st.voice_provider = None


def test_synthesize_elevenlabs_401(factory, user, project_id, saved_settings, monkeypatch, no_stream):
    import komaverse.ai.providers as providers

    monkeypatch.setattr(settings, "elevenlabs_api_key", "bad-key")
    monkeypatch.setattr(settings, "voice_provider", "elevenlabs")
    monkeypatch.setattr(
        providers.httpx,
        "post",
        lambda *a, **k: FakeResp(401, text="Unauthorized"),
    )
    with factory() as s, pytest.raises(HTTPException) as exc:
        synthesize_take(project_id, SynthCreate(text="hi"), s, user)
    assert exc.value.status_code == 400
    assert "401" in exc.value.detail


def test_synthesize_requires_text(factory, user, project_id, saved_settings):
    with factory() as s, pytest.raises(HTTPException) as exc:
        synthesize_take(project_id, SynthCreate(text="  "), s, user)
    assert exc.value.status_code == 400


def test_synthesize_caps_length(factory, user, project_id, saved_settings):
    with factory() as s, pytest.raises(HTTPException) as exc:
        synthesize_take(project_id, SynthCreate(text="x" * 2001), s, user)
    assert exc.value.status_code == 400


def test_synthesize_local_ok(factory, user, project_id, saved_settings, monkeypatch, no_stream):
    monkeypatch.setattr(settings, "voice_provider", "free-tts")
    calls = {}

    def fake_synth(text, *, engine=None, voice=None, lang="en"):
        calls.update(text=text, engine=engine, voice=voice, lang=lang)
        return b"ID3fake-mp3-bytes"

    monkeypatch.setattr(audio_api, "synthesize_local", fake_synth)
    with factory() as s:
        take = synthesize_take(
            project_id, SynthCreate(text="Once upon a panel"), s, user
        )
    assert take.name.startswith("Voice:")
    assert take.mime == "audio/mpeg"
    assert calls["text"] == "Once upon a panel"
    with factory() as s:
        takes = audio_api.list_takes(project_id, s, user)
    assert [t.id for t in takes] == [take.id]


def test_synthesize_local_missing_engine_names_the_install(
    factory, user, project_id, saved_settings, monkeypatch
):
    """kokoro:voice without pykokoro fails loudly with the pip fix."""
    import sys

    # Poison the import so the test holds with or without pykokoro
    # installed: `from pykokoro import ...` raises ImportError either way.
    monkeypatch.setitem(sys.modules, "pykokoro", None)
    monkeypatch.setattr(settings, "voice_provider", "free-tts")
    with factory() as s, pytest.raises(HTTPException) as exc:
        synthesize_take(
            project_id, SynthCreate(text="hi", voice="kokoro:af_sarah"), s, user
        )
    assert exc.value.status_code == 400
    assert "pykokoro" in exc.value.detail


def test_synthesize_local_refuses_clone(factory, user, project_id, saved_settings, monkeypatch):
    monkeypatch.setattr(settings, "voice_provider", "free-tts")
    with factory() as s, pytest.raises(HTTPException) as exc:
        synthesize_take(
            project_id,
            SynthCreate(text="hi", voice="coqui-clone:/x/ref.wav"),
            s,
            user,
        )
    assert exc.value.status_code == 400
    assert "cloning" in exc.value.detail


def test_synthesize_local_rejects_unknown_engine(factory, user, project_id, saved_settings, monkeypatch):
    monkeypatch.setattr(settings, "voice_provider", "free-tts")
    with factory() as s, pytest.raises(HTTPException) as exc:
        synthesize_take(
            project_id, SynthCreate(text="hi", engine="glados"), s, user
        )
    assert exc.value.status_code == 400
    assert "glados" in exc.value.detail


def test_synthesize_cues_in_one_trip(factory, user, project_id, saved_settings, monkeypatch, no_stream):
    monkeypatch.setattr(settings, "voice_provider", "free-tts")
    monkeypatch.setattr(
        audio_api,
        "synthesize_local",
        lambda text, *, engine=None, voice=None, lang="en": b"ID3x",
    )
    with factory() as s:
        from komaverse.api.animate import list_scenes

        scene_id = list_scenes(project_id, s, user)[0].id
    with factory() as s:
        take = synthesize_take(
            project_id,
            SynthCreate(text="cue me", cue_scene_id=scene_id, cue_start_sec=1.5),
            s,
            user,
        )
    with factory() as s:
        from komaverse.api.animate import list_scenes as scenes_of

        cues = scenes_of(project_id, s, user)[0].cues
    assert [(c.audio_id, c.start_sec) for c in cues] == [(take.id, 1.5)]


def test_voice_brain_roundtrip(factory, user, saved_settings):
    with factory() as s:
        out = agent_config(
            AgentConfigBody(
                voice_provider="free-tts", voice_model="kokoro:af_bella"
            ),
            session=s,
            user=user,
        )
    assert out.providers["voice"] == "free-tts"
    assert out.voice_model == "kokoro:af_bella"
    assert settings.freetts_voice_model == "kokoro:af_bella"
    with factory() as s:
        st = agent_status(session=s, user=user)
    assert st.providers["voice"] == "free-tts"
    assert st.voice_model == "kokoro:af_bella"


def test_voice_brain_rejects_speechless_provider(factory, user, saved_settings):
    with factory() as s, pytest.raises(HTTPException) as exc:
        agent_config(AgentConfigBody(voice_provider="meta"), session=s, user=user)
    assert exc.value.status_code == 400


def test_list_elevenlabs_voices_ok(saved_settings, monkeypatch):
    import komaverse.ai.providers as providers

    monkeypatch.setattr(settings, "elevenlabs_api_key", "test-key")
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, key=headers.get("xi-api-key"))
        return FakeResp(
            200,
            payload={
                "voices": [
                    {"voice_id": "abc123", "name": "Aria", "category": "premade"},
                    {"voice_id": "def456", "name": "Mine", "category": "cloned"},
                    {"name": "NoId"},
                ]
            },
        )

    monkeypatch.setattr(providers.httpx, "get", fake_get)
    voices = providers.list_elevenlabs_voices()
    assert seen["url"].endswith("/v1/voices")
    assert seen["key"] == "test-key"
    assert voices == [
        {"id": "abc123", "label": "Aria", "maker": "ElevenLabs"},
        {"id": "def456", "label": "Mine · cloned", "maker": "ElevenLabs"},
    ]


def test_list_elevenlabs_voices_needs_key(saved_settings):
    import komaverse.ai.providers as providers

    with pytest.raises(Exception) as exc:
        providers.list_elevenlabs_voices()
    assert "ELEVENLABS_API_KEY" in str(exc.value)


def test_list_freetts_voices_is_the_vendored_catalog():
    """No server involved: the picker list is the known Kokoro set."""
    from komaverse.ai.voice_local import FREETTS_KNOWN_VOICES, list_freetts_voices

    voices, source = list_freetts_voices()
    assert source == "local"
    assert len(voices) == len(FREETTS_KNOWN_VOICES) == 61
    assert voices[0] == {"id": "kokoro:af_alloy", "label": "af_alloy", "maker": "Kokoro"}
    assert any(v["id"] == "kokoro:af_sarah" for v in voices)


def test_voice_local_auto_pick_prefers_kokoro(monkeypatch):
    import komaverse.ai.voice_local as local

    monkeypatch.setattr(local, "_kokoro_available", lambda: True)
    assert local._auto_engine() == "kokoro"


def test_voice_local_auto_pick_falls_back(monkeypatch):
    import komaverse.ai.voice_local as local

    monkeypatch.setattr(local, "_kokoro_available", lambda: False)
    monkeypatch.setattr(local, "_espeak_exe", lambda: None)
    monkeypatch.setattr(local, "_pyttsx3_available", lambda: True)
    assert local._auto_engine() == "pyttsx3"


def test_voice_local_auto_pick_empty_names_installs(monkeypatch):
    import komaverse.ai.voice_local as local

    monkeypatch.setattr(local, "_kokoro_available", lambda: False)
    monkeypatch.setattr(local, "_espeak_exe", lambda: None)
    monkeypatch.setattr(local, "_pyttsx3_available", lambda: False)
    with pytest.raises(Exception) as exc:
        local._auto_engine()
    assert "pykokoro" in str(exc.value)


def test_voice_local_wav_to_mp3_roundtrip(tmp_path):
    """The file pipeline (wav -> bundled-ffmpeg mp3) runs with no TTS."""
    import numpy as np

    from komaverse.ai.voice_local import _wav_to_mp3, _write_wav_mono
    from komaverse.renderer import audio_has_stream

    wav = tmp_path / "s.wav"
    mp3 = tmp_path / "s.mp3"
    _write_wav_mono(wav, np.zeros(8000, dtype=np.float32), 8000)
    _wav_to_mp3(wav, mp3)
    assert mp3.stat().st_size > 0
    assert audio_has_stream(mp3)


def test_ai_voices_route(user):
    from komaverse.api.ai import ai_voices

    out = ai_voices("free-tts", user)
    assert out.provider == "free-tts"
    assert out.source == "local"
    assert len(out.voices) > 50
    with pytest.raises(HTTPException) as exc:
        ai_voices("meta", user)
    assert exc.value.status_code == 400


def test_voice_local_honors_brain_default_voice(monkeypatch):
    """Blank engine+voice speaks the Brain default (was: always af_sarah)."""
    import komaverse.ai.voice_local as local

    monkeypatch.setattr(settings, "freetts_voice_model", "kokoro:af_bella")
    monkeypatch.setattr(local, "_kokoro_available", lambda: True)
    seen = {}

    def fake_kokoro(text, voice, lang, mp3_path):
        seen.update(voice=voice)
        mp3_path.write_bytes(b"ID3x")

    monkeypatch.setattr(local, "_run_kokoro", fake_kokoro)
    assert local.synthesize_local("hello village") == b"ID3x"
    assert seen["voice"] == "af_bella"


def test_voice_local_falls_back_when_default_engine_missing(monkeypatch):
    """Brain default on a missing engine still speaks via auto-pick."""
    import komaverse.ai.voice_local as local

    monkeypatch.setattr(settings, "freetts_voice_model", "kokoro:af_bella")
    monkeypatch.setattr(local, "_kokoro_available", lambda: False)
    monkeypatch.setattr(local, "_espeak_exe", lambda: None)
    monkeypatch.setattr(local, "_pyttsx3_available", lambda: True)
    seen = {}

    def fake_pyttsx3(text, mp3_path):
        seen.update(engine="pyttsx3")
        mp3_path.write_bytes(b"ID3x")

    monkeypatch.setattr(local, "_run_pyttsx3", fake_pyttsx3)
    assert local.synthesize_local("hello village") == b"ID3x"
    assert seen["engine"] == "pyttsx3"
