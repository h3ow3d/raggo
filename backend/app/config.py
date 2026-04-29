"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the backend service.

    Values come from environment variables (see `.env.example`).
    """

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # --- Database ---------------------------------------------------------
    postgres_host: str = Field(default="postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="rag_flight_lab", alias="POSTGRES_DB")
    postgres_user: str = Field(default="rag", alias="POSTGRES_USER")
    postgres_password: str = Field(default="rag_dev_password", alias="POSTGRES_PASSWORD")

    # --- Embedding (used for the schema dimension only in Phase 1) --------
    embedding_dim: int = Field(default=384, alias="EMBEDDING_DIM")
    embedding_model_name: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        alias="EMBEDDING_MODEL_NAME",
    )

    # --- Seed data sizes --------------------------------------------------
    seed_flight_count: int = Field(default=5000, alias="SEED_FLIGHT_COUNT")
    seed_log_count: int = Field(default=50000, alias="SEED_LOG_COUNT")
    seed_incident_count: int = Field(default=500, alias="SEED_INCIDENT_COUNT")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
