"""Meta Model API coverage: Muse Image, Voice Transcribe, SAM 3.1.

All network calls are stubbed at the httpx boundary; no key ever leaves
the test process (conftest strips ambient keys).
"""

from __future__ import annotations

import base64
import io

import httpx
import pytest
from conftest import create_user
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.ai.providers import (
    MetaProvider,
    ProviderError,
    ProviderUnavailable,
)
from komaverse.api.audio import transcribe_take
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import AudioTakeRow, Base, UserRow

from test_audio import upload, wav_bytes


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


def _png_bytes(w=200, h=100) -> bytes:
    img = Image.new("RGB", (w, h), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload
        self.text = "" if payload is None else str(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _image_payload(b64: str) -> dict:
    return {
        "id": "resp_img1",
        "status": "completed",
        "output": [
            {"type": "reasoning", "id": "rs_1"},
            {
                "type": "image_generation_call",
                "id": "ig_1",
                "status": "completed",
                "result": b64,
            },
        ],
        "usage": {"total_tokens": 10904},
    }


# ------------------------------------------------------------- defaults ---


def test_meta_model_defaults():
    assert settings.meta_chat_model == "muse-spark-1.3"
    assert settings.meta_image_model == "muse-image-1.0"
    assert settings.meta_transcribe_model == "muse-voice-transcribe-1.0"
    assert settings.meta_sam_model == "sam-3.1"


# ---------------------------------------------------------- muse image ---


def test_meta_image_generates_bytes(monkeypatch):
    monkeypatch.setattr(settings, "meta_api_key", "mk-test")
    seen = {}
    png = _png_bytes()

    def _post(url, **kwargs):
        seen["url"] = url
        seen["body"] = kwargs.get("json")
        return _Resp(200, _image_payload(base64.b64encode(png).decode()))

    monkeypatch.setattr(httpx, "post", _post)
    out = MetaProvider().generate_image("a fox", 512, 512, seed=7)
    assert out == png
    assert seen["url"].endswith("/responses")
    assert seen["body"]["model"] == "muse-image-1.0"
    assert seen["body"]["input"] == "a fox"  # no refs: plain string turn


def test_meta_image_with_references_uses_input_images(monkeypatch):
    monkeypatch.setattr(settings, "meta_api_key", "mk-test")
    seen = {}
    png = _png_bytes()

    def _post(url, **kwargs):
        seen["body"] = kwargs.get("json")
        return _Resp(200, _image_payload(base64.b64encode(png).decode()))

    monkeypatch.setattr(httpx, "post", _post)
    ref = _png_bytes(64, 64)
    out = MetaProvider().generate_image("restyle", 512, 512, reference_images=(ref,))
    assert out == png
    parts = seen["body"]["input"][0]["content"]
    kinds = [p.get("type") for p in parts]
    assert "input_text" in kinds and "input_image" in kinds
    img_part = next(p for p in parts if p.get("type") == "input_image")
    assert img_part["image_url"].startswith("data:image/png;base64,")


def test_meta_image_auth_error(monkeypatch):
    monkeypatch.setattr(settings, "meta_api_key", "mk-bad")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(401, {"error": "no"}))
    with pytest.raises(ProviderError):
        MetaProvider().generate_image("a fox", 64, 64)
    monkeypatch.setattr(settings, "meta_api_key", None)
    with pytest.raises(ProviderError):
        MetaProvider().generate_image("a fox", 64, 64)


# ------------------------------------------------------------ transcribe ---


def _transcribe_payload() -> dict:
    return {
        "sessionId": "sess_1",
        "transcript": "hello world",
        "audioDurationMs": 1500,
        "turns": [
            {"transcript": "hello", "startMs": 0, "endMs": 700},
            {"transcript": "world", "startMs": 700, "endMs": 1500},
        ],
    }


def test_meta_transcribe_returns_turns(monkeypatch):
    monkeypatch.setattr(settings, "meta_api_key", "mk-test")
    seen = {}

    def _post(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers")
        seen["files"] = kwargs.get("files")
        return _Resp(200, _transcribe_payload())

    monkeypatch.setattr(httpx, "post", _post)
    res = MetaProvider().transcribe_audio(wav_bytes(), "take.wav")
    assert res["transcript"] == "hello world"
    assert len(res["turns"]) == 2
    assert seen["url"].endswith("/asr/transcribe")
    assert seen["headers"]["Authorization"] == "Bearer mk-test"
    assert set(seen["files"]) == {"request", "audio"}


def test_meta_transcribe_no_key(monkeypatch):
    monkeypatch.setattr(settings, "meta_api_key", None)
    with pytest.raises(ProviderError):
        MetaProvider().transcribe_audio(b"RIFF", "take.wav")


def test_transcribe_endpoint_needs_key(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Vox"), s, user)
    with factory() as s:
        take = upload(s, user, project.id)
    with factory() as s:
        with pytest.raises(Exception) as exc:
            transcribe_take(take.id, s, user)
    assert "META_API_KEY" in str(exc.value)


def test_transcribe_endpoint_returns_turns(factory, user, monkeypatch):
    monkeypatch.setattr(settings, "meta_api_key", "mk-test")
    monkeypatch.setattr(
        MetaProvider, "transcribe_audio", lambda self, *a, **k: _transcribe_payload()
    )
    with factory() as s:
        project = create_project(ProjectCreate(name="Vox"), s, user)
    with factory() as s:
        take = upload(s, user, project.id)
    with factory() as s:
        res = transcribe_take(take.id, s, user)
    assert res.transcript == "hello world"
    assert len(res.turns) == 2
    assert res.turns[0].start_sec == 0.0


# ------------------------------------------------------------------- sam ---


def _segment_payload(text: str) -> dict:
    return {
        "id": "resp_sam1",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


def test_meta_segment_parses_boxes(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "meta_api_key", "mk-test")
    seen = {}

    def _post(url, **kwargs):
        seen["body"] = kwargs.get("json")
        return _Resp(200, _segment_payload('[{"x":100,"y":50,"w":800,"h":500}]'))

    monkeypatch.setattr(httpx, "post", _post)
    img = tmp_path / "page.png"
    img.write_bytes(_png_bytes())
    boxes = MetaProvider().segment(img, "comic panel")
    assert seen["body"]["model"] == "sam-3.1"
    assert len(boxes) == 1
    assert (boxes[0]["x"], boxes[0]["y"]) == (20.0, 5.0)
    assert (boxes[0]["w"], boxes[0]["h"]) == (160.0, 50.0)


def test_meta_segment_unparseable(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "meta_api_key", "mk-test")
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _Resp(200, _segment_payload("no geometry here"))
    )
    img = tmp_path / "page.png"
    img.write_bytes(_png_bytes())
    with pytest.raises(ProviderUnavailable):
        MetaProvider().segment(img, "comic panel")


def test_sam_detector_routes(tmp_path, monkeypatch):
    from komaverse.detect import run_detector

    monkeypatch.setattr(settings, "meta_api_key", "mk-test")
    monkeypatch.setattr(
        MetaProvider, "segment", lambda self, image, concept: [{"x": 1.0, "y": 2.0, "w": 3.0, "h": 4.0}]
    )
    img = tmp_path / "page.png"
    img.write_bytes(_png_bytes())
    boxes, used, fell_back = run_detector("sam", img, "rtl")
    assert used == "sam" and fell_back is False
    assert boxes[0]["x"] == 1.0


def test_sam_detector_falls_back_without_key(tmp_path, monkeypatch):
    from komaverse.detect import run_detector

    monkeypatch.setattr(settings, "meta_api_key", None)
    img = tmp_path / "page.png"
    img.write_bytes(_png_bytes())
    boxes, used, fell_back = run_detector("sam", img)
    assert used == "heuristic" and fell_back is True
    assert len(boxes) >= 1
