"""Komaverse core configuration — everything from environment."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Komaverse Studio"
    app_env: str = Field(default="development", validation_alias="APP_ENV")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    serve_web: bool = Field(default=False, validation_alias="SERVE_WEB")
    debug: bool = Field(default=False, validation_alias="DEBUG")
    host: str = Field(default="0.0.0.0", validation_alias="HOST")
    port: int = Field(default=8000, validation_alias="PORT")

    allowed_origins: str = Field(
        default="http://localhost:5173,https://tauri.localhost",
        validation_alias="ALLOWED_ORIGINS",
    )
    upload_max_mb: int = Field(default=50, validation_alias="UPLOAD_MAX_MB")
    project_quota_mb: int = Field(
        default=500, validation_alias="PROJECT_QUOTA_MB"
    )
    rate_limit_per_minute: int = Field(
        default=1000, validation_alias="RATE_LIMIT_PER_MINUTE"
    )

    paywall_enabled: bool = Field(default=False, validation_alias="PAYWALL_ENABLED")

    mcp_user_email: str = Field(default="", validation_alias="KOMAVERSE_MCP_USER")

    ai_provider: str = Field(default="cloud", validation_alias="AI_PROVIDER")
    # Per-kind studio overrides (widget Brain panel; blank = ai_provider).
    chat_provider: str | None = Field(default=None, validation_alias="CHAT_PROVIDER")
    vision_provider: str | None = Field(
        default=None, validation_alias="VISION_PROVIDER"
    )
    image_provider: str | None = Field(
        default=None, validation_alias="IMAGE_PROVIDER"
    )
    ai_base_url: str | None = Field(default=None, validation_alias="AI_BASE_URL")
    ai_api_key: str | None = Field(default=None, validation_alias="AI_API_KEY")
    ai_image_model: str = Field(default="image", validation_alias="AI_IMAGE_MODEL")
    ai_chat_model: str = Field(default="gpt-4o-mini", validation_alias="AI_CHAT_MODEL")
    ai_vision_model: str | None = Field(
        default=None, validation_alias="AI_VISION_MODEL"
    )
    # Meta Model API (Muse family) — agent brain, vision detection,
    # image generation (Muse Image), speech-to-text (Muse Voice
    # Transcribe), segmentation (SAM 3.1). https://api.meta.ai/v1;
    # key from dev.meta.ai. See docs/models + the meta-model-cookbook.
    meta_api_key: str | None = Field(default=None, validation_alias="META_API_KEY")
    meta_base_url: str = Field(
        default="https://api.meta.ai/v1", validation_alias="META_BASE_URL"
    )
    meta_chat_model: str = Field(
        default="muse-spark-1.3", validation_alias="META_CHAT_MODEL"
    )
    meta_vision_model: str | None = Field(
        default=None, validation_alias="META_VISION_MODEL"
    )
    meta_image_model: str = Field(
        default="muse-image-1.0", validation_alias="META_IMAGE_MODEL"
    )
    meta_transcribe_model: str = Field(
        default="muse-voice-transcribe-1.0",
        validation_alias="META_TRANSCRIBE_MODEL",
    )
    meta_sam_model: str = Field(
        default="sam-3.1", validation_alias="META_SAM_MODEL"
    )
    # OpenRouter — chat, image models, async video models, one key.
    openrouter_api_key: str | None = Field(
        default=None, validation_alias="OPENROUTER_API_KEY"
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        validation_alias="OPENROUTER_BASE_URL",
    )
    openrouter_chat_model: str = Field(
        default="openai/gpt-4o-mini", validation_alias="OPENROUTER_CHAT_MODEL"
    )
    openrouter_vision_model: str = Field(
        default="google/gemini-2.5-flash", validation_alias="OPENROUTER_VISION_MODEL"
    )
    openrouter_image_model: str = Field(
        default="black-forest-labs/flux-3-image-klein",
        validation_alias="OPENROUTER_IMAGE_MODEL",
    )
    openrouter_video_model: str = Field(
        default="bytedance/seedance-2.0", validation_alias="OPENROUTER_VIDEO_MODEL"
    )
    openrouter_referer: str = Field(
        default="https://komaverse.studio", validation_alias="OPENROUTER_REFERER"
    )
    # Local open-weight server (Ollama-compatible) for desktop / LAN /
    # on-device use: the studio talks to it like any OpenAI endpoint.
    # Set the model names to what you pulled locally; no key needed.
    local_base_url: str = Field(
        default="http://127.0.0.1:11434", validation_alias="LOCAL_BASE_URL"
    )
    local_api_key: str = Field(default="ollama", validation_alias="LOCAL_API_KEY")
    local_chat_model: str = Field(default="", validation_alias="LOCAL_CHAT_MODEL")
    local_vision_model: str = Field(
        default="", validation_alias="LOCAL_VISION_MODEL"
    )
    local_image_model: str = Field(
        default="", validation_alias="LOCAL_IMAGE_MODEL"
    )
    comfyui_url: str = Field(
        default="http://127.0.0.1:8188", validation_alias="COMFYUI_URL"
    )
    comfyui_ckpt: str = Field(
        default="model.safetensors", validation_alias="COMFYUI_CKPT"
    )
    comfyui_seed: int = Field(default=1, validation_alias="COMFYUI_SEED")
    comfyui_timeout_sec: int = Field(
        default=180, validation_alias="COMFYUI_TIMEOUT_SEC"
    )

    database_url: str = Field(
        default="sqlite:///./komaverse.db",
        validation_alias="DATABASE_URL",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0", validation_alias="REDIS_URL"
    )

    media_dir: Path = Field(default=Path("./media"), validation_alias="MEDIA_DIR")

    # YOLO panel detector weights. Absolute or backend-root-relative path;
    # default ships in backend/models/. Missing file ⇒ "yolo" requests fall
    # back to heuristic (same contract as the cloud fallback).
    yolo_model: str = Field(
        default="models/best-comic-panel-detection.pt",
        validation_alias="KOMA_YOLO_MODEL",
    )
    # Optional weight download URLs (web deploys without vendored .pt files).
    # Fetched once into the paths above on first use, then cached locally.
    yolo_url: str = Field(default="", validation_alias="KOMA_YOLO_URL")
    yolo_leoxs22_url: str = Field(default="", validation_alias="KOMA_YOLO_LEOXS_URL")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()
