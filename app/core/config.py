from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Medical PDF Parsing Service"
    LLAMA_CLOUD_API_KEY: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )


settings = Settings()
