"""Studio-wide AI brain overrides: persisted, hot, no restart.

Only model/provider *selection* lives here — never secrets. Rows overlay
the process environment via setattr on the shared settings object, the
same object the test-suite already mutates, so every existing reader
(env defaults, chat, vision, generation) honors overrides with no
plumbing changes. Deleting a row falls back to the env default.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from komaverse.config import settings
from komaverse.db_models import StudioSettingRow

# Settings fields the widget may override (provider + per-provider models).
AI_OVERRIDE_KEYS = (
    "ai_provider",
    "chat_provider",
    "vision_provider",
    "image_provider",
    "meta_chat_model",
    "meta_vision_model",
    "meta_image_model",
    "meta_transcribe_model",
    "openrouter_chat_model",
    "openrouter_vision_model",
    "openrouter_image_model",
    "openrouter_video_model",
    "ai_chat_model",
    "ai_vision_model",
    "ai_image_model",
    "local_chat_model",
    "local_vision_model",
    "local_image_model",
)

# Provider -> settings-field prefix for its selectable models.
PROVIDER_PREFIX = {
    "meta": "meta_",
    "openrouter": "openrouter_",
    "cloud": "ai_",
    "local": "local_",
}

# Honest per-kind provider menus (mirrors backend capabilities: chat and
# vision run on LLM providers, image on all image-capable ones, video is
# OpenRouter-only end to end).
CHAT_PROVIDERS = ("meta", "openrouter", "cloud", "local")
VISION_PROVIDERS = ("meta", "openrouter", "cloud", "local")
IMAGE_PROVIDERS = ("meta", "openrouter", "cloud", "local", "comfyui")
KIND_PROVIDERS = {
    "chat": CHAT_PROVIDERS,
    "vision": VISION_PROVIDERS,
    "image": IMAGE_PROVIDERS,
}


def provider_for(kind: str) -> str:
    """Effective provider for chat/vision/image/video.

    A per-kind studio override wins; otherwise the global default. Video
    has no provider override (OpenRouter-only), so it always resolves
    through the global default chain.
    """
    if kind == "video":
        return "openrouter"
    if kind == "transcribe":
        return "meta"
    specific = getattr(settings, f"{kind}_provider", None)
    return specific or settings.ai_provider


def model_field(provider: str, kind: str) -> str | None:
    """Settings field for provider+kind (chat/vision/image/video)."""
    prefix = PROVIDER_PREFIX.get(provider)
    if prefix is None:
        return None
    return f"{prefix}{kind}_model"


def get_ai_overrides(session: Session) -> dict[str, str]:
    rows = (
        session.execute(
            select(StudioSettingRow).where(StudioSettingRow.key.in_(AI_OVERRIDE_KEYS))
        )
        .scalars()
        .all()
    )
    return {r.key: r.value for r in rows}


def set_ai_override(session: Session, key: str, value: str | None) -> None:
    """Store an override; None/blank deletes it (env default wins)."""
    if key not in AI_OVERRIDE_KEYS:
        raise KeyError(f"unknown AI setting {key!r}")
    session.execute(delete(StudioSettingRow).where(StudioSettingRow.key == key))
    if value is not None and str(value).strip():
        session.add(StudioSettingRow(key=key, value=str(value).strip()))
    session.commit()


_baseline: dict[str, str | None] | None = None


def apply_ai_overrides(session: Session) -> dict[str, str]:
    """Overlay stored rows onto the shared settings object. Returns them.

    Deleted rows fall back: the first call per process snapshots the
    ambient values (env in prod, hermetic pins in tests) as the baseline,
    and every apply resets to baseline before overlaying.
    """
    global _baseline
    if _baseline is None:
        _baseline = snapshot_ai_settings()
    restore_ai_settings(_baseline)
    applied = get_ai_overrides(session)
    for key, value in applied.items():
        setattr(settings, key, value)
    return applied


def snapshot_ai_settings() -> dict[str, str | None]:
    """Current effective values (for tests to restore after mutating)."""
    return {k: getattr(settings, k, None) for k in AI_OVERRIDE_KEYS}


def restore_ai_settings(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        setattr(settings, key, value)
