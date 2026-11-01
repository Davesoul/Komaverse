"""Pluggable AI providers: cloud, local ComfyUI, Meta Muse family, OpenRouter, fake.

One interface for image / video / panel-detection, plus a shared
OpenAI-compatible chat helper for the agent brain and shared Meta Responses
/ ASR helpers. No vendor types may leak outside this module — callers use
names only.
"""

from __future__ import annotations

import base64
import io
import json
import math
import re
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin

import httpx
from PIL import Image

from komaverse.config import settings
from komaverse.studio_settings import provider_for


class ProviderError(Exception):
    pass


class UnsupportedOp(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class AIProvider(Protocol):
    name: str

    def generate_image(
        self,
        prompt: str,
        width: int,
        height: int,
        model: str | None = None,
        *,
        seed: int | None = None,
        negative_prompt: str = "",
        reference_images: tuple[bytes, ...] = (),
    ) -> bytes:
        ...

    def generate_video(
        self,
        prompt: str,
        seconds: float,
        model: str | None = None,
        *,
        seed: int | None = None,
        start_frame: bytes | None = None,
        reference_images: tuple[bytes, ...] = (),
    ) -> bytes:
        ...

    def detect_panels(self, image: Path) -> list[dict]:
        ...


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _num_0_1000(value) -> float | None:
    """Model coordinate in 0-1000 space, or None when missing/not numeric."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return min(max(f, 0.0), 1000.0)


def _boxes_from_text(text: str, w: int, h: int) -> list[dict]:
    boxes: list[dict] = []
    for m in re.finditer(r"\{[^{}]*\}", text):
        try:
            o = json.loads(m.group())
        except ValueError:
            continue
        if not isinstance(o, dict):
            continue
        x, y = _num_0_1000(o.get("x")), _num_0_1000(o.get("y"))
        bw, bh = _num_0_1000(o.get("w")), _num_0_1000(o.get("h"))
        if x is None or y is None or bw is None or bh is None:
            continue
        if bw < 1 or bh < 1:  # degenerate box in 0-1000 units
            continue
        boxes.append(
            {
                "x": x / 1000 * w,
                "y": y / 1000 * h,
                "w": bw / 1000 * w,
                "h": bh / 1000 * h,
            }
        )
    return boxes


class CloudProvider:
    """Any OpenAI-compatible endpoint (OpenRouter etc.).

    Settings indirection (_base_url/_api_key/...) lets LocalProvider
    reuse every call shape against on-device open-weight servers.
    """

    name = "cloud"
    _detect_prompt = (
        "Detect manga panels. Reply ONLY with JSON: "
        '[{"x":0-1000,"y":0-1000,"w":0-1000,"h":0-1000}] in reading order.'
    )

    def _base_url(self) -> str | None:
        return settings.ai_base_url

    def _api_key(self) -> str | None:
        return settings.ai_api_key

    def _image_model(self) -> str:
        return settings.ai_image_model

    def _vision_model(self) -> str:
        return settings.ai_vision_model or settings.ai_chat_model

    # Last image-endpoint usage payload, set by generate_image for telemetry.
    last_usage: dict = {}

    def _check(self) -> None:
        if not self._api_key() or not self._base_url():
            raise ProviderUnavailable("AI_BASE_URL / AI_API_KEY not configured")

    def generate_image(
        self,
        prompt: str,
        width: int,
        height: int,
        model: str | None = None,
        *,
        seed: int | None = None,
        negative_prompt: str = "",
        reference_images: tuple[bytes, ...] = (),
    ) -> bytes:
        # OpenAI-style /images/generations has no seed/negative/reference
        # fields: seed and references are intentionally not sent (the call
        # would 400). Negatives already ride in the prompt text ("Avoid: …").
        self._check()
        try:
            res = httpx.post(
                f"{self._base_url()}/images/generations",
                headers={"Authorization": f"Bearer {self._api_key()}"},
                json={
                    "model": model or self._image_model(),
                    "prompt": prompt,
                    "size": f"{width}x{height}",
                    "response_format": "b64_json",
                },
                timeout=120,
            )
            if res.status_code >= 400:
                raise ProviderError(
                    f"image call failed ({res.status_code}): {res.text[:200]}"
                )
            try:
                payload = res.json()
                self.last_usage = _normalize_usage(payload.get("usage"))
                return base64.b64decode(payload["data"][0]["b64_json"])
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise ProviderError(
                    f"image returned an unreadable response: {exc}"
                ) from exc
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderUnavailable(f"cloud image call failed: {exc}") from exc

    def generate_video(
        self,
        prompt: str,
        seconds: float,
        model: str | None = None,
        *,
        seed: int | None = None,
        start_frame: bytes | None = None,
        reference_images: tuple[bytes, ...] = (),
    ) -> bytes:
        raise UnsupportedOp("generic cloud endpoint has no video model configured")

    def detect_panels(self, image: Path) -> list[dict]:
        self._check()
        with Image.open(image) as img:
            # Scale to ORIGINAL pixels: the model returns 0-1000 coords
            # relative to the sent (thumbnail) image, which keeps aspect.
            orig_w, orig_h = img.size
            img.thumbnail((1024, 1024))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        try:
            res = httpx.post(
                f"{self._base_url()}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key()}"},
                json={
                    "model": self._vision_model(),
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": self._detect_prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                                },
                            ],
                        }
                    ],
                    "max_tokens": 2000,
                },
                timeout=60,
            )
            res.raise_for_status()
            text = res.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            raise ProviderUnavailable(f"cloud call failed: {exc}") from exc
        boxes = _boxes_from_text(text, orig_w, orig_h)
        if not boxes:
            raise ProviderUnavailable("cloud returned no parseable boxes")
        return boxes


class LocalProvider(CloudProvider):
    """On-device / LAN open-weight server (Ollama-compatible OpenAI API).

    The desktop app (and a phone on the same network) can point the
    studio at a local server instead of a cloud account: set
    LOCAL_BASE_URL (+ LOCAL_CHAT_MODEL / LOCAL_VISION_MODEL for the
    models you pulled). Same call shapes as cloud; a server without an
    image endpoint answers its own error.
    """

    name = "local"

    def _base_url(self) -> str | None:
        return settings.local_base_url

    def _api_key(self) -> str | None:
        return settings.local_api_key or "local"

    def _image_model(self) -> str:
        return settings.local_image_model or settings.local_chat_model

    def _vision_model(self) -> str:
        return settings.local_vision_model or settings.local_chat_model

    def _check(self) -> None:
        if not self._base_url():
            raise ProviderUnavailable("LOCAL_BASE_URL not configured")
        if not self._vision_model():
            raise ProviderUnavailable(
                "local chat needs LOCAL_CHAT_MODEL "
                "(the model name you pulled locally)"
            )


class ComfyUIProvider:
    """Local ComfyUI (text-to-image via the /prompt queue API)."""

    name = "comfyui"

    def _post(self, path: str, payload: dict | None = None) -> httpx.Response:
        try:
            if payload is None:
                res = httpx.get(f"{settings.comfyui_url}{path}", timeout=10)
            else:
                res = httpx.post(f"{settings.comfyui_url}{path}", json=payload, timeout=10)
            res.raise_for_status()
            return res
        except Exception as exc:
            raise ProviderUnavailable(f"ComfyUI unreachable: {exc}") from exc

    def generate_image(
        self,
        prompt: str,
        width: int,
        height: int,
        model: str | None = None,
        *,
        seed: int | None = None,
        negative_prompt: str = "",
        reference_images: tuple[bytes, ...] = (),
    ) -> bytes:
        # Txt2img workflow: seed + negative wire into the sampler nodes.
        # References need an img2img workflow (not configured) — ignored.
        workflow = {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": settings.comfyui_ckpt},
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["4", 1]},
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": negative_prompt.strip() or "blurry, low quality",
                    "clip": ["4", 1],
                },
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed if seed is not None else settings.comfyui_seed,
                    "steps": 20,
                    "cfg": 8,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                },
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {"images": ["8", 0], "filename_prefix": "komaverse"},
            },
        }
        prompt_id = self._post("/prompt", {"prompt": workflow}).json()["prompt_id"]
        deadline = time.time() + settings.comfyui_timeout_sec
        while time.time() < deadline:
            history = self._post(f"/history/{prompt_id}").json()
            if prompt_id in history:
                outputs = history[prompt_id].get("outputs", {})
                for node in outputs.values():
                    for img in node.get("images", []):
                        view = httpx.get(
                            f"{settings.comfyui_url}/view",
                            params={
                                "filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": "output",
                            },
                            timeout=30,
                        )
                        view.raise_for_status()
                        return view.content
                raise ProviderUnavailable("ComfyUI produced no images")
            time.sleep(2)
        raise ProviderUnavailable("ComfyUI render timed out")

    def generate_video(
        self,
        prompt: str,
        seconds: float,
        model: str | None = None,
        *,
        seed: int | None = None,
        start_frame: bytes | None = None,
        reference_images: tuple[bytes, ...] = (),
    ) -> bytes:
        raise UnsupportedOp("ComfyUI video workflow not configured")

    def detect_panels(self, image: Path) -> list[dict]:
        raise UnsupportedOp("ComfyUI panel detection not configured")


class FakeProvider:
    """Deterministic test/demo provider (non-production only)."""

    name = "fake"

    def __init__(self) -> None:
        self.last_image: dict | None = None
        self.last_video: dict | None = None

    def generate_image(
        self,
        prompt: str,
        width: int,
        height: int,
        model: str | None = None,
        *,
        seed: int | None = None,
        negative_prompt: str = "",
        reference_images: tuple[bytes, ...] = (),
    ) -> bytes:
        self.last_image = {
            "prompt": prompt,
            "seed": seed,
            "negative_prompt": negative_prompt,
            "references": len(reference_images),
        }
        img = Image.new("RGB", (max(width, 8), max(height, 8)), (32, 32, 48))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def generate_video(
        self,
        prompt: str,
        seconds: float,
        model: str | None = None,
        *,
        seed: int | None = None,
        start_frame: bytes | None = None,
        reference_images: tuple[bytes, ...] = (),
    ) -> bytes:
        self.last_video = {
            "prompt": prompt,
            "seed": seed,
            "start_frame": start_frame is not None,
            "references": len(reference_images),
        }
        raise UnsupportedOp("fake provider has no video")

    def detect_panels(self, image: Path) -> list[dict]:
        with Image.open(image) as img:
            w, h = img.size
        return [{"x": 0.0, "y": 0.0, "w": float(w), "h": float(h)}]


def get_provider(name: str) -> AIProvider:
    if name == "cloud":
        return CloudProvider()
    if name == "comfyui":
        return ComfyUIProvider()
    if name == "local":
        return LocalProvider()
    if name == "meta":
        return MetaProvider()
    if name == "openrouter":
        return OpenRouterProvider()
    if name == "fake" and not settings.is_production:
        return FakeProvider()
    raise ValueError(f"unknown provider: {name}")


# ---------------------------------------------------------------------------
# Shared OpenAI-compatible chat (agent brain + vision panel detection).
# ---------------------------------------------------------------------------

_CHAT_TIMEOUT = 60.0


def _normalize_usage(raw) -> dict:
    """Provider usage payloads (chat or Responses) into input/output/total.

    Chat completions report prompt/completion/total_tokens; the Responses
    API reports input/output/total_tokens. Missing or partial payloads
    yield zeros — callers fall back to estimates.
    """
    raw = raw if isinstance(raw, dict) else {}
    if "prompt_tokens" in raw or "total_tokens" in raw and "input_tokens" not in raw:
        prompt = raw.get("prompt_tokens", 0) or 0
        done = raw.get("completion_tokens", 0) or 0
        total = raw.get("total_tokens", prompt + done) or 0
    else:
        prompt = raw.get("input_tokens", 0) or 0
        done = raw.get("output_tokens", 0) or 0
        total = raw.get("total_tokens", prompt + done) or 0
    return {"input": int(prompt), "output": int(done), "total": int(total)}


def chat_complete_with_usage(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[dict, dict]:
    """POST one chat-completions call; return (message dict, usage dict).

    Works against any OpenAI-compatible endpoint (generic cloud, Meta Model
    API, OpenRouter). Transport errors propagate as httpx.HTTPError so the
    agent can fall back to its demo brain; answered HTTP errors and
    malformed payloads raise ProviderError with an actionable message.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    if extra_headers:
        headers.update(extra_headers)
    body: dict = {"model": model, "messages": messages, "temperature": 0.2}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    res = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        json=body,
        headers=headers,
        timeout=_CHAT_TIMEOUT,
    )
    if res.status_code == 401:
        raise ProviderError("chat rejected the API key (401) — check the key")
    if res.status_code == 429:
        raise ProviderError("chat rate limit exceeded (429) — retry later")
    if res.status_code >= 400:
        raise ProviderError(f"chat failed ({res.status_code}): {res.text[:200]}")
    try:
        payload = res.json()
        message = payload["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(
            f"chat returned an unreadable response: {exc}"
        ) from exc
    if not isinstance(message, dict):
        raise ProviderError("chat returned an unreadable response")
    return message, _normalize_usage(payload.get("usage"))


def chat_complete(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict:
    """POST one chat-completions call; return the first choice message dict.

    Usage is discarded — telemetry that needs token counts uses
    chat_complete_with_usage instead.
    """
    message, _ = chat_complete_with_usage(
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=messages,
        tools=tools,
        extra_headers=extra_headers,
    )
    return message


def get_chat_config(*, vision: bool = False) -> tuple[str, str, str]:
    """(base_url, api_key, model) for the configured agent chat provider.

    AI_PROVIDER selects the brain: "meta" (Meta Model API / Muse Spark),
    "openrouter", or legacy "cloud" (AI_* settings). Raises ProviderError
    naming the missing key when unconfigured.
    """
    name = provider_for("vision" if vision else "chat")
    if name == "meta":
        model = settings.meta_vision_model or settings.meta_chat_model
        if not vision:
            model = settings.meta_chat_model
        if not settings.meta_api_key:
            raise ProviderError("meta chat needs META_API_KEY (dev.meta.ai)")
        return settings.meta_base_url, settings.meta_api_key, model
    if name == "openrouter":
        model = (
            settings.openrouter_vision_model
            if vision
            else settings.openrouter_chat_model
        )
        if not settings.openrouter_api_key:
            raise ProviderError("openrouter chat needs OPENROUTER_API_KEY")
        return (
            settings.openrouter_base_url,
            settings.openrouter_api_key,
            model,
        )
    if name == "cloud":
        if not settings.ai_api_key or not settings.ai_base_url:
            raise ProviderError("cloud chat needs AI_BASE_URL and AI_API_KEY")
        model = settings.ai_chat_model
        if vision and settings.ai_vision_model:
            model = settings.ai_vision_model
        return settings.ai_base_url, settings.ai_api_key, model
    if name == "local":
        model = (
            settings.local_vision_model
            if vision
            else settings.local_chat_model
        ) or settings.local_chat_model
        if not settings.local_base_url or not model:
            raise ProviderError(
                "local chat needs LOCAL_BASE_URL and LOCAL_CHAT_MODEL"
            )
        return (
            settings.local_base_url,
            settings.local_api_key or "local",
            model,
        )
    raise ProviderError(
        f'unknown AI_PROVIDER "{name}" (want meta, openrouter, cloud, '
        "local, comfyui, fake)"
    )


def chat_configured() -> bool:
    """True when the configured chat provider has its key."""
    try:
        get_chat_config()
    except ProviderError:
        return False
    return True


def _vision_boxes(
    image: Path,
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    extra_headers: dict[str, str] | None = None,
) -> list[dict]:
    """Ask a vision model for panel boxes; parse into pixel-space dicts."""
    with Image.open(image) as img:
        # Scale to ORIGINAL pixels: the model returns 0-1000 coords relative
        # to the sent (thumbnail) image, which keeps aspect.
        orig_w, orig_h = img.size
        img.thumbnail((1024, 1024))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    try:
        message = chat_complete(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            extra_headers=extra_headers,
        )
        text = message.get("content") or ""
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderUnavailable(f"vision call failed: {exc}") from exc
    if isinstance(text, list):  # some gateways return content parts
        text = " ".join(
            part.get("text", "") for part in text if isinstance(part, dict)
        )
    boxes = _boxes_from_text(str(text), orig_w, orig_h)
    if not boxes:
        raise ProviderUnavailable("vision model returned no parseable boxes")
    return boxes


# ---------------------------------------------------------------------------
# Meta Model API (Muse family): chat + vision (Muse Spark/Wonder),
# image (Muse Image), speech-to-text (Muse Voice Transcribe), segmentation
# (SAM 3.1). Shapes follow dev.meta.ai capability guides + the
# meta-model-cookbook recipes (05_muse_image, 06_muse_voice, 07_sam).
# There is no Meta video-generation model, so video raises with a routing
# hint instead of silently faking output.
# ---------------------------------------------------------------------------


def _post_responses(
    *, base_url: str, api_key: str, payload: dict
) -> tuple[dict, dict]:
    """POST one Responses API turn; return (response dict, usage dict).

    Shared by Muse Image generation and SAM segmentation. Status codes are
    checked manually (no raise_for_status) to match module convention.
    Usage carries model-reported input/output/total tokens for telemetry.
    """
    try:
        res = httpx.post(
            f"{base_url.rstrip('/')}/responses",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=180.0,
        )
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"meta responses call failed: {exc}") from exc
    if res.status_code == 401:
        raise ProviderError("meta rejected the API key (401) — check META_API_KEY")
    if res.status_code == 429:
        raise ProviderError("meta rate limit exceeded (429) — retry later")
    if res.status_code >= 400:
        raise ProviderError(
            f"meta responses failed ({res.status_code}): {res.text[:200]}"
        )
    try:
        data = res.json()
    except ValueError as exc:
        raise ProviderError(f"meta responses unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise ProviderError("meta responses returned an unreadable response")
    return data, _normalize_usage(data.get("usage"))


def _responses_create(*, base_url: str, api_key: str, payload: dict) -> dict:
    """POST one Responses API turn; return the parsed response dict.

    Usage is discarded — telemetry that needs token counts uses
    _post_responses instead.
    """
    data, _ = _post_responses(base_url=base_url, api_key=api_key, payload=payload)
    return data


class MetaProvider:
    """Meta Model API (https://api.meta.ai/v1). Chat, vision, image, voice, SAM."""

    name = "meta"
    # Last Responses-API usage payload (input/output/total tokens), set by
    # generate_image/segment for telemetry. Read via getattr (absent = {}).
    last_usage: dict = {}

    def generate_image(
        self,
        prompt: str,
        width: int,
        height: int,
        model: str | None = None,
        *,
        seed: int | None = None,
        negative_prompt: str = "",
        reference_images: tuple[bytes, ...] = (),
    ) -> bytes:
        # Muse Image via the Responses API (cookbook 05_muse_image): turn 1
        # takes a text input; reference images ride as input_image parts in
        # a user message (steer/compose). The image is the base64 `result`
        # on the image_generation_call output item.
        if not settings.meta_api_key:
            raise ProviderError("meta image needs META_API_KEY (dev.meta.ai)")
        model = model or settings.meta_image_model
        text = prompt.strip()
        if negative_prompt.strip():
            text = f"{text} (Avoid: {negative_prompt.strip()})"
        if reference_images:
            content: list[dict] = [{"type": "input_text", "text": text}]
            content.extend(
                {"type": "input_image", "image_url": _data_url(png)}
                for png in reference_images
            )
            turn: str | list[dict] = [{"role": "user", "content": content}]
        else:
            turn = text
        data, self.last_usage = _post_responses(
            base_url=settings.meta_base_url,
            api_key=settings.meta_api_key,
            payload={"model": model, "input": turn},
        )
        for item in data.get("output") or []:
            if (
                isinstance(item, dict)
                and item.get("type") == "image_generation_call"
            ):
                result = item.get("result")
                if isinstance(result, str) and result:
                    try:
                        return base64.b64decode(result)
                    except ValueError as exc:
                        raise ProviderError(
                            "meta image payload is not valid base64: "
                            f"{exc}"
                        ) from exc
        raise ProviderError("meta image returned no image_generation_call result")

    def generate_video(
        self,
        prompt: str,
        seconds: float,
        model: str | None = None,
        *,
        seed: int | None = None,
        start_frame: bytes | None = None,
        reference_images: tuple[bytes, ...] = (),
    ) -> bytes:
        raise UnsupportedOp(
            "meta has no video-generation model: use provider "
            '"openrouter" for video jobs'
        )

    def transcribe_audio(
        self,
        audio: bytes,
        filename: str,
        *,
        mode: str = "ENDPOINTING",
        model: str | None = None,
    ) -> dict:
        """One-shot speech-to-text (cookbook 06_muse_voice).

        Returns the raw JSON: {transcript, audioDurationMs, turns?} where
        each turn carries transcript + startMs/endMs (+speaker in
        DIARIZATION). ENDPOINTING is the default: turn boundaries without
        a speaker rig — exactly what subtitle drafting wants.
        """
        if not settings.meta_api_key:
            raise ProviderError("meta transcribe needs META_API_KEY (dev.meta.ai)")
        if mode not in ("PUSH_TO_TALK", "ENDPOINTING", "DIARIZATION"):
            raise ProviderError(f"unknown transcribe mode {mode!r}")
        if len(audio) > 32 * 1024 * 1024:
            raise ProviderError(
                "audio over the 32 MB one-shot cap — split it first"
            )
        request = {
            "mode": mode,
            "model": model or settings.meta_transcribe_model,
            "audioEncoding": "WAV",
        }
        try:
            res = httpx.post(
                f"{settings.meta_base_url.rstrip('/')}/asr/transcribe",
                headers={"Authorization": f"Bearer {settings.meta_api_key}"},
                files={
                    # Params travel as a JSON part, not as form fields.
                    "request": (None, json.dumps(request), "application/json"),
                    "audio": (filename or "take", audio),
                },
                timeout=300.0,
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"meta transcribe request failed: {exc}"
            ) from exc
        if res.status_code == 401:
            raise ProviderError(
                "meta rejected the API key (401) — check META_API_KEY"
            )
        if res.status_code == 429:
            raise ProviderError("meta rate limit exceeded (429) — retry later")
        if res.status_code >= 400:
            raise ProviderError(
                f"meta transcribe failed ({res.status_code}): {res.text[:200]}"
            )
        try:
            data = res.json()
        except ValueError as exc:
            raise ProviderError(f"meta transcribe unreadable: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(
            data.get("transcript"), str
        ):
            raise ProviderError("meta transcribe returned an unreadable response")
        return data

    def segment(self, image: Path, concept: str) -> list[dict]:
        """Object boxes via SAM 3.1 (cookbook 07_sam): text prompt + image.

        The model returns geometry as text; boxes are parsed 0-1000 like
        vision detection and scaled to pixels. Unparseable output raises
        ProviderUnavailable so detector callers fall back to heuristic.
        """
        if not settings.meta_api_key:
            raise ProviderError("meta segment needs META_API_KEY (dev.meta.ai)")
        with Image.open(image) as img:
            orig_w, orig_h = img.size
            img.thumbnail((1024, 1024))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        data, self.last_usage = _post_responses(
            base_url=settings.meta_base_url,
            api_key=settings.meta_api_key,
            payload={
                "model": settings.meta_sam_model,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Segment every instance of: "
                                    f"{concept}. Reply ONLY with JSON "
                                    'like [{"x":0-1000,"y":0-1000,'
                                    '"w":0-1000,"h":0-1000}] in reading order.'
                                ),
                            },
                            {
                                "type": "input_image",
                                "image_url": (
                                    f"data:image/png;base64,{b64}"
                                ),
                            },
                        ],
                    }
                ],
            },
        )
        texts: list[str] = []
        for item in data.get("output") or []:
            if isinstance(item, dict) and item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and isinstance(
                        part.get("text"), str
                    ):
                        texts.append(part["text"])
        boxes = _boxes_from_text(" ".join(texts), orig_w, orig_h)
        if not boxes:
            raise ProviderUnavailable("sam returned no parseable geometry")
        return boxes

    def detect_panels(self, image: Path) -> list[dict]:
        if not settings.meta_api_key:
            raise ProviderError("meta vision needs META_API_KEY (dev.meta.ai)")
        base_url = settings.meta_base_url
        model = settings.meta_vision_model or settings.meta_chat_model
        return _vision_boxes(
            image,
            base_url=base_url,
            api_key=settings.meta_api_key,
            model=model,
            prompt=CloudProvider._detect_prompt,
        )


# ---------------------------------------------------------------------------
# OpenRouter: chat, image models, async video models behind one key.
# ---------------------------------------------------------------------------

# Curated per-kind model catalog served to the UI (GET /ai/models) and
# enforced at POST /ai/generate. Badges: "cheap" (budget/fast) and
# "popular" (best-known quality). Keep this short and verified — an
# unknown id fails loudly at the provider, billed or not.
# IDs verified against the live OpenRouter catalogs
# (GET /api/v1/images/models, GET /api/v1/videos/models): unknown ids
# fail loudly at the provider, billed or not — never guess new ones.
OPENROUTER_IMAGE_MODELS = (
    {
        "id": "black-forest-labs/flux-3-image-klein",
        "label": "Flux 3 Klein",
        "badge": "cheap",
        "maker": "Black Forest Labs",
    },
    {
        "id": "qwen/qwen-image-2.1-turbo",
        "label": "Qwen Image 2.1 Turbo",
        "badge": "cheap",
        "maker": "Qwen",
    },
    {
        "id": "google/gemini-nano-banana-2.1",
        "label": "Nano Banana 2.1",
        "badge": "popular",
        "maker": "Google",
    },
    {"id": "meta/muse-image", "label": "Meta Muse Image", "badge": "popular", "maker": "Meta"},
)
OPENROUTER_VIDEO_MODELS = (
    {"id": "bytedance/seedance-2.0", "label": "Seedance 2.0", "badge": "popular", "maker": "ByteDance"},
    {"id": "google/veo-3.1", "label": "Veo 3.1", "badge": "popular", "maker": "Google"},
    {"id": "alibaba/wan-2.6", "label": "Wan 2.6", "badge": "cheap", "maker": "Alibaba"},
)

# Verified chat/vision options per provider: ids confirmed against the live
# OpenRouter catalog (existence, tool support, input modalities, pricing)
# or already used in code/env. Never guess new ones — free text still
# accepts anything and unknown ids fail loudly at the provider.
# `maker` names who built the model; `via` is resolved per row in the UI
# from the selected provider (direct vs aggregator).
META_CHAT_MODELS = (
    {"id": "muse-spark-1.3", "label": "Muse Spark 1.3", "badge": "spark", "maker": "Meta"},
    {
        "id": "muse-spark-1.3-contributor",
        "label": "Muse Spark 1.3 Contributor",
        "badge": "spark",
        "maker": "Meta",
    },
)
OPENROUTER_CHAT_MODELS = (
    {"id": "openai/gpt-4o-mini", "label": "GPT-4o-mini", "badge": "cheap", "maker": "OpenAI"},
    {"id": "google/gemini-2.5-flash", "label": "Gemini 2.5 Flash", "badge": "popular", "maker": "Google"},
    {"id": "google/gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash-Lite", "badge": "cheap", "maker": "Google"},
    {"id": "anthropic/claude-haiku-4.5", "label": "Claude Haiku 4.5", "badge": "popular", "maker": "Anthropic"},
    {"id": "meta-llama/llama-4-maverick", "label": "Llama 4 Maverick", "badge": "cheap", "maker": "Meta"},
    {"id": "deepseek/deepseek-chat-v3.1", "label": "DeepSeek V3.1", "badge": "cheap", "maker": "DeepSeek"},
)
OPENROUTER_VISION_MODELS = (
    {"id": "google/gemini-2.5-flash", "label": "Gemini 2.5 Flash", "badge": "popular", "maker": "Google"},
    {"id": "google/gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash-Lite", "badge": "cheap", "maker": "Google"},
    {"id": "anthropic/claude-haiku-4.5", "label": "Claude Haiku 4.5", "badge": "popular", "maker": "Anthropic"},
)
META_IMAGE_MODELS = (
    {"id": "muse-image-1.0", "label": "Muse Image 1.0", "badge": "spark", "maker": "Meta"},
)


def list_openrouter_models() -> dict:
    """Catalog + server defaults for the model picker UI."""
    return {
        "image": [dict(m) for m in OPENROUTER_IMAGE_MODELS],
        "video": [dict(m) for m in OPENROUTER_VIDEO_MODELS],
        "defaults": {
            "image": settings.openrouter_image_model,
            "video": settings.openrouter_video_model,
        },
        "chat": {
            "meta": [dict(m) for m in META_CHAT_MODELS],
            "openrouter": [dict(m) for m in OPENROUTER_CHAT_MODELS],
        },
        "vision": {
            # Meta vision falls back to the chat model when unset, so the
            # Spark pair is the honest vision list too.
            "meta": [dict(m) for m in META_CHAT_MODELS],
            "openrouter": [dict(m) for m in OPENROUTER_VISION_MODELS],
        },
        "meta_image": [dict(m) for m in META_IMAGE_MODELS],
    }


def check_openrouter_model(kind: str, model: str) -> None:
    """Raise ProviderError unless model is catalogued for kind."""
    catalog = (
        OPENROUTER_IMAGE_MODELS if kind == "image" else OPENROUTER_VIDEO_MODELS
    )
    if model not in [m["id"] for m in catalog]:
        want = ", ".join(m["id"] for m in catalog)
        raise ProviderError(f'unknown {kind} model "{model}" (want: {want})')


def _openrouter_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": settings.openrouter_referer,
        "X-Title": "Komaverse Studio",
    }


def _fetch_bytes(url: str) -> bytes:
    try:
        resp = httpx.get(url, headers=_openrouter_headers(), timeout=_CHAT_TIMEOUT)
    except httpx.HTTPError as exc:
        raise ProviderError(f"download failed: {exc}") from exc
    if resp.status_code >= 400:
        raise ProviderError(f"download failed ({resp.status_code})")
    return resp.content


def _image_bytes_from_result(data: dict) -> bytes:
    """Unwrap b64_json / data-URL / remote URL from an image result payload."""
    b64 = data.get("b64_json") or data.get("b64")
    if isinstance(b64, str):
        try:
            return base64.b64decode(b64)
        except ValueError as exc:
            raise ProviderError(f"image payload is not valid base64: {exc}") from exc
    url = data.get("url")
    if isinstance(url, str) and url:
        if url.startswith("data:") and "," in url:
            try:
                return base64.b64decode(url.split(",", 1)[1])
            except ValueError as exc:
                raise ProviderError(
                    f"image data-URL is not valid base64: {exc}"
                ) from exc
        return _fetch_bytes(url)
    raise ProviderError("image response carried no b64_json or url")


class OpenRouterProvider:
    """OpenRouter gateway: chat models, image models, async video models."""

    name = "openrouter"
    # Last image-endpoint usage payload, set by generate_image for telemetry.
    last_usage: dict = {}

    def _check(self) -> None:
        if not settings.openrouter_api_key:
            raise ProviderUnavailable("OPENROUTER_API_KEY not configured")

    def generate_image(
        self,
        prompt: str,
        width: int,
        height: int,
        model: str | None = None,
        *,
        seed: int | None = None,
        negative_prompt: str = "",
        reference_images: tuple[bytes, ...] = (),
    ) -> bytes:
        # Dedicated Image API (docs/guides/overview/multimodal/image-generation):
        # POST /images {model, prompt, resolution, aspect_ratio, seed?,
        # input_references?} -> {"data": [{"b64_json": ...}]}. All catalogued
        # models live here. No negative field exists — negatives ride in the
        # prompt text ("Avoid: …") via build_final_prompt.
        self._check()
        model = model or settings.openrouter_image_model
        longest = max(width, height)
        resolution = "512" if longest <= 512 else "768" if longest <= 768 else "1K"
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "resolution": resolution,
            "aspect_ratio": "1:1",
        }
        if seed is not None:
            payload["seed"] = seed
        if reference_images:
            payload["input_references"] = [
                {"type": "image_url", "image_url": {"url": _data_url(png)}}
                for png in reference_images
            ]
        try:
            resp = httpx.post(
                f"{settings.openrouter_base_url.rstrip('/')}/images",
                json=payload,
                headers=_openrouter_headers(),
                timeout=180.0,
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"openrouter image request failed: {exc}"
            ) from exc
        if resp.status_code >= 400:
            raise ProviderError(
                f"openrouter image failed ({resp.status_code}): {resp.text[:200]}"
            )
        try:
            payload = resp.json()
            self.last_usage = _normalize_usage(payload.get("usage"))
            first = payload["data"][0]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"openrouter image returned an unreadable response: {exc}"
            ) from exc
        return _image_bytes_from_result(first)

    def generate_video(
        self,
        prompt: str,
        seconds: float,
        model: str | None = None,
        *,
        seed: int | None = None,
        start_frame: bytes | None = None,
        reference_images: tuple[bytes, ...] = (),
    ) -> bytes:
        # Async Video API (docs/.../multimodal/video-generation): POST
        # /videos {model, prompt, duration, seed?, frame_images?,
        # input_references?}; frame_images entries carry frame_type
        # first_frame/last_frame (image-to-video), input_references guide
        # style (reference-to-video). No negative field — see images.
        self._check()
        base = settings.openrouter_base_url.rstrip("/")
        headers = _openrouter_headers()
        payload: dict = {
            "model": model or settings.openrouter_video_model,
            "prompt": prompt,
            "duration": int(seconds),
        }
        if seed is not None:
            payload["seed"] = seed
        if start_frame is not None:
            payload["frame_images"] = [
                {
                    "type": "image_url",
                    "image_url": {"url": _data_url(start_frame)},
                    "frame_type": "first_frame",
                }
            ]
        if reference_images:
            payload["input_references"] = [
                {"type": "image_url", "image_url": {"url": _data_url(png)}}
                for png in reference_images
            ]
        try:
            submit = httpx.post(
                f"{base}/videos",
                json=payload,
                headers=headers,
                timeout=_CHAT_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"openrouter video submit failed: {exc}"
            ) from exc
        if submit.status_code >= 400:
            raise ProviderError(
                f"openrouter video submit failed "
                f"({submit.status_code}): {submit.text[:200]}"
            )
        try:
            job = submit.json()
            job_id = job.get("id")
            polling_url = job.get("polling_url") or f"{base}/videos/{job_id}"
        except ValueError as exc:
            raise ProviderError(f"openrouter video gave no job: {exc}") from exc
        if not job_id:
            raise ProviderError("openrouter video gave no job id")
        polling_url = urljoin(base + "/", str(polling_url))
        # Sync waiting is fine: run_ai_job already runs in a background task
        # while POST /ai/generate returned 202 at once.
        deadline = time.monotonic() + 600.0
        while time.monotonic() < deadline:
            try:
                poll = httpx.get(polling_url, headers=headers, timeout=30.0)
            except httpx.HTTPError:
                # A failed status *request* is not a failed *job*: retry it.
                time.sleep(10.0)
                continue
            if poll.status_code == 429:
                time.sleep(10.0)
                continue
            if poll.status_code >= 400:
                raise ProviderError(
                    f"openrouter video poll failed ({poll.status_code})"
                )
            try:
                state = poll.json()
            except ValueError as exc:
                raise ProviderError(
                    f"openrouter video poll unreadable: {exc}"
                ) from exc
            status = str(state.get("status", "")).lower()
            if status == "completed":
                urls = state.get("unsigned_urls") or []
                first = urls[0] if urls else ""
                url = (
                    first.get("url", "")
                    if isinstance(first, dict)
                    else str(first or "")
                )
                url = url or f"{base}/videos/{job_id}/content?index=0"
                try:
                    resp = httpx.get(url, headers=headers, timeout=120.0)
                except httpx.HTTPError as exc:
                    raise ProviderError(
                        f"video download failed: {exc}"
                    ) from exc
                if resp.status_code >= 400:
                    raise ProviderError(
                        f"video download failed ({resp.status_code})"
                    )
                return resp.content
            if status in ("failed", "cancelled", "expired", "error"):
                raise ProviderError(
                    f"openrouter video {status}: "
                    f"{str(state.get('error'))[:200]}"
                )
            time.sleep(10.0)
        raise ProviderError("openrouter video timed out after 10 minutes")

    def detect_panels(self, image: Path) -> list[dict]:
        self._check()
        return _vision_boxes(
            image,
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key or "",
            model=settings.openrouter_vision_model,
            prompt=CloudProvider._detect_prompt,
            extra_headers=_openrouter_headers(),
        )


def chat_complete_stream(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    extra_headers: dict[str, str] | None = None,
):
    """Stream one chat-completions call as (kind, payload) events.

    Yields ("token", text) deltas as they arrive, then a final
    ("message", (message dict, usage dict)) shaped exactly like
    chat_complete_with_usage returns. Works against any OpenAI-compatible
    endpoint (generic cloud, Meta Model API, OpenRouter).

    Answered HTTP errors raise ProviderError like the non-streaming call;
    transport errors propagate as httpx.HTTPError so the agent can fall
    back to its demo brain.
    """
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"}
    if extra_headers:
        headers.update(extra_headers)
    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    timeout = httpx.Timeout(10.0, read=300.0, write=60.0, pool=60.0)
    with httpx.stream(
        "POST",
        f"{base_url.rstrip('/')}/chat/completions",
        json=body,
        headers=headers,
        timeout=timeout,
    ) as res:
        if res.status_code == 401:
            raise ProviderError("chat rejected the API key (401) — check the key")
        if res.status_code == 429:
            raise ProviderError("chat rate limit exceeded (429) — retry later")
        if res.status_code >= 400:
            raise ProviderError(f"chat failed ({res.status_code})")
        content = ""
        tool_calls: dict[int, dict] = {}
        usage = {"input": 0, "output": 0, "total": 0}
        for line in res.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if not isinstance(chunk, dict):
                continue
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if isinstance(piece, list):
                    piece = "".join(
                        p.get("text", "") for p in piece if isinstance(p, dict)
                    )
                if piece:
                    content += piece
                    yield ("token", piece)
                for tc in delta.get("tool_calls") or []:
                    slot = tool_calls.setdefault(
                        int(tc.get("index", 0)),
                        {"id": "", "function": {"name": "", "arguments": ""}},
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
            if isinstance(chunk.get("usage"), dict):
                usage = _normalize_usage(chunk["usage"])
        calls = [
            {
                "id": slot["id"] or f"stream-call-{idx}",
                "type": "function",
                "function": slot["function"],
            }
            for idx, slot in sorted(tool_calls.items())
        ]
        message = {"role": "assistant", "content": content or None}
        if calls:
            message["tool_calls"] = calls
        yield ("message", (message, usage))
