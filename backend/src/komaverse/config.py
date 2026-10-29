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

    ai_provider: str = Field(default="cloud", validation_alias="AI_PROVIDER")
    ai_base_url: str | None = Field(default=None, validation_alias="AI_BASE_URL")
    ai_api_key: str | None = Field(default=None, validation_alias="AI_API_KEY")
    ai_image_model: str = Field(default="image", validation_alias="AI_IMAGE_MODEL")
    ai_chat_model: str = Field(default="gpt-4o-mini", validation_alias="AI_CHAT_MODEL")
    ai_vision_model: str | None = Field(
        default=None, validation_alias="AI_VISION_MODEL"
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
