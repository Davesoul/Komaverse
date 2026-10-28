"""Pluggable AI providers (M4): cloud-first, local ComfyUI, fake for tests/demo.

One interface for image / video / panel-detection. No vendor types may leak
outside this module — callers use names ("cloud", "comfyui", "fake") only.
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

import httpx
from PIL import Image

from komaverse.config import settings


class ProviderError(Exception):
    pass


class UnsupportedOp(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class AIProvider(Protocol):
    name: str

    def generate_image(self, prompt: str, width: int, height: int) -> bytes:
        ...

    def generate_video(self, prompt: str, seconds: float) -> bytes:
        ...

    def detect_panels(self, image: Path) -> list[dict]:
        ...


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
    """Any OpenAI-compatible endpoint (OpenRouter etc.)."""

    name = "cloud"
    _detect_prompt = (
        "Detect manga panels. Reply ONLY with JSON: "
        '[{"x":0-1000,"y":0-1000,"w":0-1000,"h":0-1000}] in reading order.'
    )

    def _check(self) -> None:
        if not settings.ai_api_key or not settings.ai_base_url:
            raise ProviderUnavailable("AI_BASE_URL / AI_API_KEY not configured")

    def generate_image(self, prompt: str, width: int, height: int) -> bytes:
        self._check()
        try:
            res = httpx.post(
                f"{settings.ai_base_url}/images/generations",
                headers={"Authorization": f"Bearer {settings.ai_api_key}"},
                json={
                    "model": settings.ai_image_model,
                    "prompt": prompt,
                    "size": f"{width}x{height}",
                    "response_format": "b64_json",
                },
                timeout=120,
            )
            res.raise_for_status()
            return base64.b64decode(res.json()["data"][0]["b64_json"])
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderUnavailable(f"cloud image call failed: {exc}") from exc

    def generate_video(self, prompt: str, seconds: float) -> bytes:
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
                f"{settings.ai_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {settings.ai_api_key}"},
                json={
                    "model": settings.ai_vision_model or settings.ai_chat_model,
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

    def generate_image(self, prompt: str, width: int, height: int) -> bytes:
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
                "inputs": {"text": "blurry, low quality", "clip": ["4", 1]},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": settings.comfyui_seed,
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

    def generate_video(self, prompt: str, seconds: float) -> bytes:
        raise UnsupportedOp("ComfyUI video workflow not configured")

    def detect_panels(self, image: Path) -> list[dict]:
        raise UnsupportedOp("ComfyUI panel detection not configured")


class FakeProvider:
    """Deterministic test/demo provider (non-production only)."""

    name = "fake"

    def generate_image(self, prompt: str, width: int, height: int) -> bytes:
        img = Image.new("RGB", (max(width, 8), max(height, 8)), (32, 32, 48))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def generate_video(self, prompt: str, seconds: float) -> bytes:
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
    if name == "fake" and not settings.is_production:
        return FakeProvider()
    raise ValueError(f"unknown provider: {name}")
