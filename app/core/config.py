from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_CORS_ALLOW_ORIGINS = (
    "http://localhost:3000",
    "https://frontend-production-fb7e.up.railway.app",
)


class Settings(BaseSettings):
    """Application settings."""

    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Medical Intelligence Extractive Service"
    LLAMA_CLOUD_API_KEY: str
    OPENAI_API_KEY: str | None = None
    NEXTAUTH_SECRET: str | None = None

    EXTRACTION_MODE: Literal["FAST", "BALANCED", "MULTIMODAL", "PREMIUM"] = "MULTIMODAL"
    SUMMARY_MODE: Literal["FAST", "BALANCED", "MULTIMODAL", "PREMIUM"] = "FAST"
    EXTRACTION_CHUNK_MODE: Literal["PAGE", "SECTION"] = "PAGE"
    HIGH_RESOLUTION_MODE: bool = True
    ENABLE_CONFIDENCE_SCORES: bool = True
    ENABLE_DOCS: bool = True

    CORS_ALLOW_ORIGINS: str = ",".join(DEFAULT_CORS_ALLOW_ORIGINS)
    JOB_STORAGE_DIR: Path = Path("storage/jobs")
    JOB_RETENTION_HOURS: int = 72
    MAX_CONCURRENT_SUMMARIES: int = 3
    ARTIFACT_ENCRYPTION_KEY: str | None = None
    OPENAI_PAGE_MODEL: str = "gpt-5.2"
    OPENAI_BUNDLE_MODEL: str = "gpt-5.2"
    OPENAI_PAGE_BATCH_SIZE: int = 2
    OPENAI_PAGE_RENDER_DPI: int = 144

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    @property
    def cors_allow_origins_list(self) -> list[str]:
        normalized_origins: list[str] = []

        for raw_origin in self.CORS_ALLOW_ORIGINS.split(","):
            origin = raw_origin.strip().rstrip("/")
            if origin and origin not in normalized_origins:
                normalized_origins.append(origin)

        for origin in DEFAULT_CORS_ALLOW_ORIGINS:
            if origin not in normalized_origins:
                normalized_origins.append(origin)

        return normalized_origins


settings = Settings()
