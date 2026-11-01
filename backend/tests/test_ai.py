"""M4 tests: AI jobs (fake provider), credits, failure/refund paths. All sync."""

from __future__ import annotations

import pytest
from conftest import create_user
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.api.ai import (
    GenerateRequest,
    GrantRequest,
    generate,
    get_balance,
    get_job,
    grant,
    run_ai_job,
    transcribe_voice,
)


def _voice_file(name="note.webm", data=b"RIFF....fake-audio"):
    import io

    from starlette.datastructures import Headers, UploadFile

    return UploadFile(
        filename=name,
        file=io.BytesIO(data),
        headers=Headers({"content-type": "audio/webm"}),
    )


def test_transcode_to_wav_normalizes():
    """Bundled ffmpeg turns decodable audio into 16k mono WAV (real run)."""
    import io
    import wave

    from komaverse.renderer import transcode_to_wav

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00\x00" * 8000)
    out = io.BytesIO(transcode_to_wav(buf.getvalue(), ".wav"))
    with wave.open(out, "rb") as wf:
        assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, 16000)
        assert wf.getnframes() > 0


def test_transcode_to_wav_rejects_garbage():
    """Undecodable bytes fail loudly instead of reaching Meta."""
    from komaverse.renderer import transcode_to_wav

    with pytest.raises(RuntimeError):
        transcode_to_wav(b"not audio at all", ".webm")


def test_transcribe_voice_returns_text(factory, user, monkeypatch):
    """Voice input: decodable audio -> Meta transcript text (mocked)."""
    import komaverse.api.ai as ai_mod

    monkeypatch.setattr(ai_mod, "audio_has_stream", lambda path: True)
    monkeypatch.setattr(ai_mod, "transcode_to_wav", lambda data, ext: b"RIFF....wav")
    monkeypatch.setattr(
        ai_mod.MetaProvider, "transcribe_audio",
        lambda self, audio, filename, **kw: {
            "transcript": "hello studio",
            "audioDurationMs": 1500,
        },
    )
    with factory() as s:
        out = transcribe_voice(_voice_file(), s, user)
    assert out.text == "hello studio"
    assert out.duration_sec == 1.5


def test_transcribe_voice_validates_input(factory, user, monkeypatch):
    """Voice input rejects bad extensions, empty files, and undecodable audio."""
    import komaverse.api.ai as ai_mod

    monkeypatch.setattr(ai_mod, "audio_has_stream", lambda path: False)
    with factory() as s:
        with pytest.raises(HTTPException):
            transcribe_voice(_voice_file("note.txt", b"nope"), s, user)
        with pytest.raises(HTTPException):
            transcribe_voice(_voice_file("note.webm", b""), s, user)
        with pytest.raises(HTTPException):
            transcribe_voice(_voice_file("note.webm", b"bytes"), s, user)


def test_transcribe_voice_missing_key_is_400(factory, user, monkeypatch):
    """Without META_API_KEY the endpoint names the missing key (no hang)."""
    import komaverse.api.ai as ai_mod

    from komaverse.config import settings as _settings

    monkeypatch.setattr(ai_mod, "audio_has_stream", lambda path: True)
    monkeypatch.setattr(ai_mod, "transcode_to_wav", lambda data, ext: b"RIFF....wav")
    monkeypatch.setattr(_settings, "meta_api_key", None)
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            transcribe_voice(_voice_file(), s, user)
    assert "META_API_KEY" in str(exc.value.detail)
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, JobRow, UserRow


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


def test_grant_and_balance(factory, user):
    with factory() as s:
        out = grant(GrantRequest(amount=50, reason="test"), s, user)
    assert out.balance == 50
    with factory() as s:
        assert get_balance(s, user.id) == 50


def test_generate_rejects_without_credits(factory, user):
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            generate(
                GenerateRequest(kind="image", prompt="a robot", provider="fake"),
                BackgroundTasks(),
                s,
                user,
            )
    assert exc.value.status_code == 402


def test_fake_image_job_with_frame_attach(factory, user):
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
        project = create_project(ProjectCreate(name="AI"), s, user)
    with factory() as s:
        job = generate(
            GenerateRequest(
                kind="image",
                prompt="a robot",
                project_id=project.id,
                provider="fake",
                attach_frame=True,
                width=64,
                height=64,
            ),
            BackgroundTasks(),
            s,
            user,
        )
    assert job.status == "queued"
    run_ai_job(job.id, factory)  # what the background task would do
    with factory() as s:
        done = get_job(job.id, s, user)
    assert done.status == "done"
    assert done.result_url and done.result_url.endswith(".png")
    assert (settings.media_dir / done.result_url.removeprefix("/media/")).exists()
    with factory() as s:
        assert get_balance(s, user.id) == 95  # 100 - 5 image cost, no refund
    from komaverse.api.animate import list_frames

    with factory() as s:
        frames = list_frames(project.id, s, user)
    assert len(frames) == 1 and len(frames[0].layers) == 1


def test_generate_defaults_to_gallery_no_frame(factory, user):
    """Gallery-first: a plain generation creates no frame and no sequence."""
    from komaverse.api.animate import list_frames, list_sequences

    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
        project = create_project(ProjectCreate(name="AI"), s, user)
        job = generate(
            GenerateRequest(
                kind="image",
                prompt="a robot",
                project_id=project.id,
                provider="fake",
                background=False,
            ),
            BackgroundTasks(),
            s,
            user,
        )
    run_ai_job(job.id, factory)
    with factory() as s:
        assert get_job(job.id, s, user).status == "done"
    with factory() as s:
        assert list_frames(project.id, s, user) == []
        assert [q.name for q in list_sequences(project.id, s, user)] == []


def test_generated_frame_lands_outside_hand_sequences(factory, user):
    """Explicit attach still works: the frame lands in a dedicated
    'AI generations' sequence, never in the user's draw sequences."""
    from komaverse.api.animate import (
        FrameCreate,
        SequenceCreate,
        create_frame,
        create_sequence,
        list_frames,
        list_sequences,
    )

    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
        project = create_project(ProjectCreate(name="AI"), s, user)
        seq = create_sequence(project.id, SequenceCreate(name="Sequence 1"), s, user)
        create_frame(project.id, FrameCreate(name="hand", sequence_id=seq.id), s, user)
        job = generate(
            GenerateRequest(
                kind="image",
                prompt="a robot",
                project_id=project.id,
                provider="fake",
                background=False,
                attach_frame=True,
            ),
            BackgroundTasks(),
            s,
            user,
        )
    run_ai_job(job.id, factory)
    with factory() as s:
        seqs = list_sequences(project.id, s, user)
        by_name = {q.name: q for q in seqs}
        assert "AI generations" in by_name
        ai_frames = list_frames(project.id, s, user)
        ai_ids = {f.id for f in ai_frames if f.sequence_id == by_name["AI generations"].id}
        hand_ids = {f.id for f in ai_frames if f.sequence_id == seq.id}
    assert len(ai_ids) == 1
    assert len(hand_ids) == 1  # Sequence 1 keeps only the hand-drawn frame


def test_video_rejected_upfront_without_debit(factory, user):
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            generate(
                GenerateRequest(kind="video", prompt="a robot", provider="cloud"),
                BackgroundTasks(),
                s,
                user,
            )
    assert exc.value.status_code == 400
    assert "video" in exc.value.detail.lower()
    with factory() as s:
        assert get_balance(s, user.id) == 100  # nothing debited


def test_failed_job_with_paywall_off_mints_no_credits(factory, user, monkeypatch):
    """Regression: refunds must only return debited credits (paywall off = 0)."""
    import komaverse.api.ai as ai_mod
    from komaverse.ai.providers import ProviderError

    def _boom(_name):
        raise ProviderError("no model configured")

    monkeypatch.setattr(ai_mod, "get_provider", _boom)
    monkeypatch.setattr(settings, "paywall_enabled", False)
    with factory() as s:
        job = generate(
            GenerateRequest(kind="image", prompt="x", provider="cloud"),
            BackgroundTasks(),
            s,
            user,
        )
    run_ai_job(job.id, factory)
    with factory() as s:
        failed = get_job(job.id, s, user)
    assert failed.status == "failed"
    with factory() as s:
        assert get_balance(s, user.id) == 0


def test_failed_job_with_paywall_on_still_refunds(factory, user, monkeypatch):
    import komaverse.api.ai as ai_mod
    from komaverse.ai.providers import ProviderError

    def _boom(_name):
        raise ProviderError("no model configured")

    monkeypatch.setattr(ai_mod, "get_provider", _boom)
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
    with factory() as s:
        job = generate(
            GenerateRequest(kind="image", prompt="x", provider="cloud"),
            BackgroundTasks(),
            s,
            user,
        )
    run_ai_job(job.id, factory)
    with factory() as s:
        assert get_job(job.id, s, user).status == "failed"
    with factory() as s:
        assert get_balance(s, user.id) == 100  # 5 deducted, 5 refunded


def test_generate_defaults_to_configured_provider(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "fake")
    assert GenerateRequest(kind="image", prompt="x").provider == "fake"
    monkeypatch.setattr(settings, "ai_provider", "nope")
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            generate(GenerateRequest(kind="image", prompt="x"), BackgroundTasks(), s, user)
    assert exc.value.status_code == 400


def test_comfyui_uses_configured_ckpt_and_seed(monkeypatch):
    import httpx
    from komaverse.ai.providers import ComfyUIProvider

    monkeypatch.setattr(settings, "comfyui_ckpt", "anime.safetensors")
    monkeypatch.setattr(settings, "comfyui_seed", 42)
    seen = {}

    def _post(self, path, payload=None):
        if path == "/prompt":
            seen["workflow"] = payload["prompt"]
            return _Resp({"prompt_id": "p1"})
        return _Resp({"p1": {"outputs": {"9": {"images": [{"filename": "a.png"}]}}}})

    class _Resp:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    def _get(*a, **k):
        class _View:
            content = b"PNG"

            def raise_for_status(self):
                pass

        return _View()

    monkeypatch.setattr(ComfyUIProvider, "_post", _post)
    monkeypatch.setattr(httpx, "get", _get)
    assert ComfyUIProvider().generate_image("x", 64, 64) == b"PNG"
    assert seen["workflow"]["4"]["inputs"]["ckpt_name"] == "anime.safetensors"
    assert seen["workflow"]["3"]["inputs"]["seed"] == 42


def test_unknown_provider_rejected(factory, user):
    with factory() as s:
        grant(GrantRequest(amount=100), s, user)
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            generate(
                GenerateRequest(kind="image", prompt="x", provider="nope"),
                BackgroundTasks(),
                s,
                user,
            )
    assert exc.value.status_code == 400


def test_legacy_job_without_enhance_serializes():
    """Pre-enhance job rows hold NULL (ALTER-added column on old DBs):
    to_schema must emit False instead of 500ing the jobs list."""
    row = JobRow(
        id="j1",
        user_id="u1",
        kind="export",
        prompt="",
        status="done",
        progress=1.0,
        seconds=4.5,
        enhance=None,
    )
    job = row.to_schema()
    assert job.enhance is False
