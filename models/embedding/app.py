"""FastAPI service exposing the embedding model.

Contract (internal-only, see docker-compose.yml):

    POST /embed
        request:  {"texts": ["...", "..."]}
        response: {"embeddings": [[...], [...]],
                   "model": "...", "dim": 384}

The model is loaded **once** at startup from a local directory baked into
the image (default ``/models/embedding``). The container has
``HF_HUB_OFFLINE=1`` / ``TRANSFORMERS_OFFLINE=1`` set, and is attached only
to the internal Docker ``model_net``, so no outbound network calls occur
at runtime.
"""

from __future__ import annotations

import logging
import os
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer


LOG = logging.getLogger("embedding-model")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


MODEL_DIR = os.environ.get("EMBEDDING_MODEL_DIR", "/models/embedding")
# Reported alongside results so callers can record provenance with the vector.
MODEL_NAME = os.environ.get(
    "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"
)
EXPECTED_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))
# Cap batch size to keep memory bounded for a CPU PoC. Callers can still
# embed larger collections by issuing multiple requests.
MAX_BATCH = int(os.environ.get("EMBEDDING_MAX_BATCH", "128"))


class EmbedRequest(BaseModel):
    texts: List[str] = Field(..., description="Texts to embed.")


class EmbedResponse(BaseModel):
    embeddings: List[List[float]]
    model: str
    dim: int


app = FastAPI(title="rag-flight-lab embedding model", docs_url=None, redoc_url=None)
_model: SentenceTransformer | None = None
_actual_dim: int | None = None


@app.on_event("startup")
def _load_model() -> None:
    """Load the model from the local image path. Never hits the network."""
    global _model, _actual_dim

    if not os.path.isdir(MODEL_DIR):
        raise RuntimeError(
            f"Embedding model directory '{MODEL_DIR}' not found. The image "
            "must be built so weights are baked in via the multi-stage build."
        )

    LOG.info("Loading embedding model from %s", MODEL_DIR)
    _model = SentenceTransformer(MODEL_DIR, device="cpu")
    _actual_dim = int(_model.get_sentence_embedding_dimension())
    LOG.info("Loaded model. dim=%s expected=%s", _actual_dim, EXPECTED_DIM)

    if _actual_dim != EXPECTED_DIM:
        # Don't crash here — the backend's schema check is the source of
        # truth for dimension matching — but make the mismatch loud.
        LOG.warning(
            "Embedding dim mismatch: model=%s EMBEDDING_DIM=%s",
            _actual_dim,
            EXPECTED_DIM,
        )


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if _model is not None else "loading",
        "model": MODEL_NAME,
        "dim": _actual_dim,
    }


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    if _model is None or _actual_dim is None:
        raise HTTPException(status_code=503, detail="model not loaded")

    if not req.texts:
        return EmbedResponse(embeddings=[], model=MODEL_NAME, dim=_actual_dim)

    if len(req.texts) > MAX_BATCH:
        raise HTTPException(
            status_code=413,
            detail=f"batch too large: {len(req.texts)} > {MAX_BATCH}",
        )

    # `convert_to_numpy=True` then `.tolist()` gives plain JSON-serialisable
    # Python floats. `normalize_embeddings=False` keeps the raw vectors;
    # callers (e.g. pgvector cosine ops) can normalise if needed.
    vectors = _model.encode(
        req.texts,
        batch_size=min(32, len(req.texts)),
        convert_to_numpy=True,
        normalize_embeddings=False,
        show_progress_bar=False,
    )
    return EmbedResponse(
        embeddings=vectors.tolist(),
        model=MODEL_NAME,
        dim=_actual_dim,
    )
