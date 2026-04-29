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
    GEMINI_API_KEY: str | None = None
    NEXTAUTH_SECRET: str | None = None

    AI_PROVIDER: Literal["openai", "gemini"] = "gemini"
    EXTRACTION_MODE: Literal["FAST", "BALANCED", "MULTIMODAL", "PREMIUM"] = "MULTIMODAL"
    SUMMARY_MODE: Literal["FAST", "BALANCED", "MULTIMODAL", "PREMIUM"] = "FAST"
    EXTRACTION_CHUNK_MODE: Literal["PAGE", "SECTION"] = "PAGE"
    HIGH_RESOLUTION_MODE: bool = True
    ENABLE_CONFIDENCE_SCORES: bool = True
    ENABLE_DOCS: bool = True

    CORS_ALLOW_ORIGINS: str = ",".join(DEFAULT_CORS_ALLOW_ORIGINS)
    DATABASE_URL: str = "mysql+pymysql://medicalai:medicalai@mysql:3306/medical_ai_bot"
    LEGACY_JOB_STORAGE_DIR: Path = Path("storage/jobs")
    MAX_CONCURRENT_SUMMARIES: int = 3
    ARTIFACT_ENCRYPTION_KEY: str | None = None
    S3_ENDPOINT_URL: str = "http://minio:9000"
    S3_ACCESS_KEY_ID: str = "minioadmin"
    S3_SECRET_ACCESS_KEY: str = "minioadmin"
    S3_BUCKET_NAME: str = "medical-ai-vault"
    S3_REGION: str = "us-east-1"
    S3_USE_SSL: bool = False
    ALLOW_LOCAL_FALLBACK: bool = False
    ENABLE_LEGACY_JOB_IMPORT: bool = True
    OPENAI_PAGE_MODEL: str = "gpt-4.1-mini"
    OPENAI_BUNDLE_MODEL: str = "gpt-5.2"
    GEMINI_PAGE_MODEL: str = "gemini-2.5-flash-lite"
    GEMINI_BUNDLE_MODEL: str = "gemini-2.5-flash"
    OPENAI_PAGE_BATCH_SIZE: int = 4
    OPENAI_PAGE_RENDER_DPI: int = 144
    AI_PAGE_CONCURRENCY: int = 3

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
