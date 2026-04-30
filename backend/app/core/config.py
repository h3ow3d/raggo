"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Runtime configuration for the backend service.

    Values come from environment variables (see `.env.example`).
    """

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # --- Domain selection -------------------------------------------------
    raggo_domain: str = Field(default="flights", alias="RAGGO_DOMAIN")

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

    # --- Seed data sizes (kept for backward compatibility with flights domain)
    seed_flight_count: int = Field(default=5000, alias="SEED_FLIGHT_COUNT")
    seed_log_count: int = Field(default=50000, alias="SEED_LOG_COUNT")
    seed_incident_count: int = Field(default=500, alias="SEED_INCIDENT_COUNT")

    # --- Ingestion / model service ---------------------------------------
    # URL of the internal embedding model service (only reachable from the
    # backend over `model_net`).
    embedding_service_url: str = Field(
        default="http://embedding-model:8000",
        alias="EMBEDDING_SERVICE_URL",
    )
    # Number of logs sent to the embedding service per HTTP call. Must stay
    # at or below the embedding service's MAX_BATCH (default 128).
    ingest_batch_size: int = Field(default=128, ge=1, le=128, alias="INGEST_BATCH_SIZE")
    # Cap on how many logs the startup ingestion task will embed before
    # yielding. Keeps API/UI responsive on first boot with 50k seed logs.
    startup_ingest_limit: int = Field(default=2000, ge=0, alias="STARTUP_INGEST_LIMIT")
    # Per-request HTTP timeout for embedding calls (seconds).
    embedding_request_timeout: float = Field(default=60.0, gt=0, alias="EMBEDDING_REQUEST_TIMEOUT")

    # --- Generation / agent ----------------------------------------------
    # URL of the internal generation model service (only reachable from
    # the backend over `model_net`).
    generation_service_url: str = Field(
        default="http://generation-model:8000",
        alias="GENERATION_SERVICE_URL",
    )
    # Per-request HTTP timeout for generation calls (seconds). Generation
    # is much slower than embedding, especially on CPU. Should be larger
    # than the generation service's own GEN_TIMEOUT_SECONDS so the inner
    # timeout fires first and the client receives a clean 504.
    generation_request_timeout: float = Field(
        default=360.0, gt=0, alias="GENERATION_REQUEST_TIMEOUT"
    )
    # Caps on agent retrieval. Bounds evidence size sent to the LLM and
    # the work done per query.
    agent_max_vector_results: int = Field(default=8, ge=1, le=50, alias="AGENT_MAX_VECTOR_RESULTS")
    agent_max_sql_results: int = Field(default=10, ge=1, le=50, alias="AGENT_MAX_SQL_RESULTS")
    agent_max_new_tokens: int = Field(default=256, ge=16, le=1024, alias="AGENT_MAX_NEW_TOKENS")
    generation_model_name: str = Field(
        default="Qwen/Qwen2.5-0.5B-Instruct",
        alias="GENERATION_MODEL_NAME",
    )

    # --- CORS ------------------------------------------------------------
    # Comma-separated list of origins allowed to call the backend. The
    # default covers the bundled production frontend (same-origin via
    # nginx on FRONTEND_PORT 3000) and the Vite dev server. The wildcard
    # value "*" is honoured but discouraged: the local-first stack is
    # designed to be reachable only from a developer's machine, and a
    # wildcard would let any web page they visit read backend responses.
    cors_allow_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ALLOW_ORIGINS",
    )

    @property
    def cors_allow_origin_list(self) -> list[str]:
        raw = (self.cors_allow_origins or "").strip()
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    @property
    def database_url(self) -> str:
        # Use SQLAlchemy's URL builder so credentials containing reserved
        # characters (e.g. `@`, `:`, `/`, `#`) are escaped correctly instead
        # of producing an invalid DSN.
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
