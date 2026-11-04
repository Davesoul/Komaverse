"""Local XTTS voice cloning tests: clone, synthesize, catalog, delete.

The real XTTS model (~2GB) is never touched: synthesis runs against a
fake TTS object, and the no-coqui error path runs against the genuine
missing package.
"""

from __future__ import annotations

import io
import wave

import pytest
from conftest import create_user
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.api import audio as audio_api
from komaverse.api.ai import ai_voices, clone_voice, delete_clone, list_clones
from komaverse.api.audio import SynthCreate, synthesize_take
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, UserRow


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
def project_id(factory, user):
    with factory() as s:
        return create_project(ProjectCreate(name="XTTS"), s, user).id


@pytest.fixture
def no_stream(monkeypatch):
    monkeypatch.setattr(audio_api, "audio_has_stream", lambda path: True)


def _wav(seconds=1.0, rate=8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


def _sample(name="ref.wav", data=None) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(data or _wav()))


def test_clone_xtts_saves_reference(factory, user):
    import wave as _wave
    from sqlalchemy import select

    from komaverse.db_models import ClonedVoiceRow

    with factory() as s:
        voice = clone_voice("Local", [_sample()], s, user, engine="xtts")
    assert voice.engine == "xtts" and voice.voice_id == "" and voice.sample_count == 1
    with factory() as s:
        assert [v.engine for v in list_clones(s, user)] == ["xtts"]
        row = s.execute(
            select(ClonedVoiceRow).where(ClonedVoiceRow.id == voice.id)
        ).scalars().one()
        assert row.ref_audio.startswith("voices/") and row.ref_audio.endswith(".wav")
        path = settings.media_dir / row.ref_audio
    # Reference WAV normalized to 22kHz mono on disk.
    assert path.is_file()
    with _wave.open(str(path), "rb") as fh:
        assert (fh.getframerate(), fh.getnchannels()) == (22050, 1)


def _padded_wav() -> bytes:
    """1s silence + 0.5s loud tone + 1s silence: cleaning must trim the padding."""
    import struct

    buf = io.BytesIO()
    with wave.open(buf, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(8000)
        fh.writeframes(b"\x00\x00" * 8000)
        fh.writeframes(struct.pack("<" + "h" * 4000, *([20000] * 4000)))
        fh.writeframes(b"\x00\x00" * 8000)
    return buf.getvalue()


def test_clone_xtts_cleans_reference(factory, user):
    """Ref is denoised/normalized/silence-trimmed (free-tts chain): the
    2.5s padded sample lands well under a second of real speech."""
    import wave as _wave

    with factory() as s:
        voice = clone_voice(
            "Local",
            [UploadFile(filename="ref.wav", file=io.BytesIO(_padded_wav()))],
            s,
            user,
            engine="xtts",
        )
    with factory() as s:
        from sqlalchemy import select

        from komaverse.db_models import ClonedVoiceRow

        row = s.execute(
            select(ClonedVoiceRow).where(ClonedVoiceRow.id == voice.id)
        ).scalars().one()
        path = settings.media_dir / row.ref_audio
    with _wave.open(str(path), "rb") as fh:
        assert (fh.getframerate(), fh.getnchannels()) == (22050, 1)
        assert fh.getnframes() < 22050, fh.getnframes()


def test_clone_xtts_validation(factory, user):
    with factory() as s, pytest.raises(HTTPException) as exc:
        clone_voice("Nope", [], s, user, engine="xtts")
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        clone_voice("Two", [_sample(), _sample("b.wav")], s, user, engine="xtts")
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        clone_voice("Bad", [_sample()], s, user, engine="nope")
    assert exc.value.status_code == 400
    with factory() as s, pytest.raises(HTTPException) as exc:
        clone_voice("Garbage", [_sample("x.wav", b"not audio")], s, user, engine="xtts")
    assert exc.value.status_code == 400


def test_clone_xtts_missing_package_loud(factory, user, monkeypatch):
    """Without coqui-tts importable, model load fails loudly (not silently)."""
    import sys

    import komaverse.ai.voice_local as vl

    monkeypatch.setitem(sys.modules, "TTS", None)
    monkeypatch.setitem(sys.modules, "TTS.api", None)
    monkeypatch.setattr(vl, "_xtts_ready", False)
    monkeypatch.setattr(vl, "_xtts", None)
    with pytest.raises(Exception) as exc:
        vl._get_xtts()
    assert "coqui-tts" in str(exc.value)


class _FakeTTS:
    def tts_to_file(self, text, speaker_wav, language, file_path):
        assert speaker_wav and language
        with wave.open(file_path, "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(22050)
            fh.writeframes(b"\x01\x00" * 2205)  # 0.1s of tone


def test_synthesize_xtts_take(factory, user, project_id, monkeypatch, no_stream):
    import komaverse.ai.voice_local as vl

    monkeypatch.setattr(vl, "_get_xtts", lambda: _FakeTTS())
    monkeypatch.setattr(settings, "voice_provider", "free-tts")
    with factory() as s:
        voice = clone_voice("Local", [_sample()], s, user, engine="xtts")
    with factory() as s:
        take = synthesize_take(
            project_id, SynthCreate(text="hello village", voice=f"xtts:{voice.id}"), s, user
        )
    assert take.name.startswith("Voice: hello")
    with factory() as s:
        other = create_user(s, email="xtts2@test.local")
    with factory() as s, pytest.raises(HTTPException) as exc:
        synthesize_take(
            project_id, SynthCreate(text="hi", voice=f"xtts:{voice.id}"), s, other
        )
    assert exc.value.status_code == 404


def test_catalog_lists_xtts_under_freetts(factory, user):
    with factory() as s:
        voice = clone_voice("Local", [_sample()], s, user, engine="xtts")
    with factory() as s:
        cat = ai_voices("free-tts", user, s)
    mine = [v for v in cat.voices if v.maker == "cloned"]
    assert mine and mine[0].id == f"xtts:{voice.id}"


def test_delete_xtts_removes_ref(factory, user):
    from komaverse.db_models import ClonedVoiceRow
    from sqlalchemy import select

    with factory() as s:
        voice = clone_voice("Local", [_sample()], s, user, engine="xtts")
        row = s.execute(
            select(ClonedVoiceRow).where(ClonedVoiceRow.id == voice.id)
        ).scalars().one()
        ref = settings.media_dir / row.ref_audio
    assert ref.is_file()
    with factory() as s:
        delete_clone(voice.id, s, user)
    assert not ref.exists()
    with factory() as s:
        assert list_clones(s, user) == []
