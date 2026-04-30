"""HTTP clients for the internal model services.

The model services (embedding, generation) are reachable only from the
backend over the internal `model_net` Docker network. They expose simple
JSON HTTP APIs; this module wraps them in small, typed helpers so the rest
of the backend never deals with raw HTTP details.

Phase 4 only needs the embedding client. Phase 5 adds the generation
client used by the agent.
"""

from __future__ import annotations

import logging
from typing import List, Sequence

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class EmbeddingServiceError(RuntimeError):
    """Raised when the embedding service cannot satisfy a request."""


class EmbeddingResult:
    """Embedding output bundled with provenance from the model service."""

    __slots__ = ("embeddings", "model", "dim")

    def __init__(self, embeddings: List[List[float]], model: str, dim: int) -> None:
        self.embeddings = embeddings
        self.model = model
        self.dim = dim


class EmbeddingClient:
    """Thin synchronous client for the embedding model service.

    Synchronous on purpose: ingestion runs in a worker thread and uses
    SQLAlchemy synchronously, so an async client would only complicate
    the call sites without any throughput benefit for a CPU PoC.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.embedding_service_url).rstrip("/")
        self._timeout = timeout if timeout is not None else settings.embedding_request_timeout
        # Allow injection for tests; otherwise create a client lazily so
        # construction is cheap and connections are reused per-instance.
        self._client = client
        self._owns_client = client is None

    def __enter__(self) -> EmbeddingClient:
        self._ensure_client()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def health(self) -> dict:
        """Return the embedding service's `/health` payload."""
        client = self._ensure_client()
        resp = client.get(f"{self._base_url}/health")
        resp.raise_for_status()
        return resp.json()

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        """Embed a single batch of texts.

        The caller is responsible for chunking large inputs to fit within
        the embedding service's batch and payload limits — see
        :func:`embed_batched` for an automatic chunking helper.
        """
        if not texts:
            settings = get_settings()
            return EmbeddingResult(
                embeddings=[],
                model=settings.embedding_model_name,
                dim=settings.embedding_dim,
            )

        client = self._ensure_client()
        try:
            resp = client.post(f"{self._base_url}/embed", json={"texts": list(texts)})
        except httpx.HTTPError as exc:
            raise EmbeddingServiceError(f"embedding service request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise EmbeddingServiceError(
                f"embedding service returned {resp.status_code}: {resp.text[:500]}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise EmbeddingServiceError(f"embedding service returned non-JSON body: {exc}") from exc

        embeddings = payload.get("embeddings")
        model = payload.get("model")
        dim = payload.get("dim")
        if (
            not isinstance(embeddings, list)
            or not isinstance(model, str)
            or not isinstance(dim, int)
        ):
            raise EmbeddingServiceError("embedding service returned an unexpected payload shape")

        if len(embeddings) != len(texts):
            raise EmbeddingServiceError(
                f"embedding count mismatch: got {len(embeddings)}, expected {len(texts)}"
            )

        return EmbeddingResult(embeddings=embeddings, model=model, dim=dim)

    def embed_batched(self, texts: Sequence[str], batch_size: int | None = None) -> EmbeddingResult:
        """Embed `texts` by splitting into chunks of `batch_size`.

        Returns a single :class:`EmbeddingResult` whose `model`/`dim` come
        from the first batch (the embedding service is stateless and will
        return the same model/dim for every call).
        """
        settings = get_settings()
        size = batch_size or settings.ingest_batch_size
        if size <= 0:
            raise ValueError("batch_size must be positive")

        if not texts:
            return self.embed(texts)

        all_vectors: List[List[float]] = []
        model: str | None = None
        dim: int | None = None
        for start in range(0, len(texts), size):
            chunk = list(texts[start : start + size])
            result = self.embed(chunk)
            if model is None:
                model = result.model
                dim = result.dim
            elif result.model != model or result.dim != dim:
                raise EmbeddingServiceError(
                    "embedding service returned inconsistent model/dim across batches"
                )
            all_vectors.extend(result.embeddings)

        assert model is not None and dim is not None  # for mypy
        return EmbeddingResult(embeddings=all_vectors, model=model, dim=dim)


# ---------------------------------------------------------------------------
# Generation client (Phase 5)
# ---------------------------------------------------------------------------


class GenerationServiceError(RuntimeError):
    """Raised when the generation service cannot satisfy a request."""


class GenerationResult:
    """Generation output bundled with provenance from the model service."""

    __slots__ = ("text", "model", "finish_reason")

    def __init__(self, text: str, model: str, finish_reason: str) -> None:
        self.text = text
        self.model = model
        self.finish_reason = finish_reason


class GenerationClient:
    """Thin synchronous client for the generation model service.

    The backend agent runs in a worker thread (FastAPI dispatches it via
    ``anyio.to_thread.run_sync``) and uses synchronous SQLAlchemy, so a
    synchronous HTTP client keeps the call sites simple.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.generation_service_url).rstrip("/")
        self._timeout = timeout if timeout is not None else settings.generation_request_timeout
        self._client = client
        self._owns_client = client is None

    def __enter__(self) -> GenerationClient:
        self._ensure_client()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def health(self) -> dict:
        """Return the generation service's `/health` payload."""
        client = self._ensure_client()
        resp = client.get(f"{self._base_url}/health")
        resp.raise_for_status()
        return resp.json()

    def generate(
        self,
        prompt: str,
        max_new_tokens: int | None = None,
        temperature: float | None = None,
    ) -> GenerationResult:
        """Send a single ``/generate`` request and return the result."""
        if not prompt or not prompt.strip():
            raise GenerationServiceError("prompt must be non-empty")

        body: dict[str, object] = {"prompt": prompt}
        if max_new_tokens is not None:
            body["max_new_tokens"] = max_new_tokens
        if temperature is not None:
            body["temperature"] = temperature

        client = self._ensure_client()
        try:
            resp = client.post(f"{self._base_url}/generate", json=body)
        except httpx.HTTPError as exc:
            raise GenerationServiceError(f"generation service request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise GenerationServiceError(
                f"generation service returned {resp.status_code}: {resp.text[:500]}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise GenerationServiceError(
                f"generation service returned non-JSON body: {exc}"
            ) from exc

        text = payload.get("text")
        model = payload.get("model")
        finish_reason = payload.get("finish_reason")
        if (
            not isinstance(text, str)
            or not isinstance(model, str)
            or not isinstance(finish_reason, str)
        ):
            raise GenerationServiceError("generation service returned an unexpected payload shape")

        return GenerationResult(text=text, model=model, finish_reason=finish_reason)
