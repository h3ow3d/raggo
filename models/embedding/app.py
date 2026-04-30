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

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer

LOG = logging.getLogger("embedding-model")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


MODEL_DIR = os.environ.get("EMBEDDING_MODEL_DIR", "/models/embedding")
# Filename produced by the build-time `download_model.py`. We trust this
# (baked into the image) over any runtime env var when reporting which
# model produced a given embedding — operators that change
# `EMBEDDING_MODEL_NAME` without rebuilding would otherwise cause a silent
# provenance mismatch.
METADATA_FILENAME = "rag_flight_lab_model.json"
# Falls back to the env var only if metadata is missing (older builds).
ENV_MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
EXPECTED_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))
# Cap batch size to keep memory bounded for a CPU PoC. Callers can still
# embed larger collections by issuing multiple requests.
MAX_BATCH = int(os.environ.get("EMBEDDING_MAX_BATCH", "128"))
# Per-text and total payload size caps. These bound CPU/memory work per
# request even on the internal network — callers should never need to push
# arbitrarily large blobs through the embedding service.
MAX_TEXT_CHARS = int(os.environ.get("EMBEDDING_MAX_TEXT_CHARS", "10000"))
MAX_TOTAL_CHARS = int(os.environ.get("EMBEDDING_MAX_TOTAL_CHARS", "200000"))


class EmbedRequest(BaseModel):
    texts: List[str] = Field(..., description="Texts to embed.")


class EmbedResponse(BaseModel):
    embeddings: List[List[float]]
    model: str
    dim: int


_model: SentenceTransformer | None = None
_actual_dim: int | None = None
# Reported alongside results. Resolved at startup from the baked metadata
# file (preferred) or falls back to the env var.
_model_name: str = ENV_MODEL_NAME


def _load_baked_metadata() -> dict | None:
    """Return build-time metadata written next to the weights, if present."""
    meta_path = Path(MODEL_DIR) / METADATA_FILENAME
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("Failed to read %s: %s", meta_path, exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the model from the local image path. Never hits the network."""
    global _model, _actual_dim, _model_name

    if not os.path.isdir(MODEL_DIR):
        raise RuntimeError(
            f"Embedding model directory '{MODEL_DIR}' not found. The image "
            "must be built so weights are baked in via the multi-stage build."
        )

    LOG.info("Loading embedding model from %s", MODEL_DIR)
    _model = SentenceTransformer(MODEL_DIR, device="cpu")
    _actual_dim = int(_model.get_sentence_embedding_dimension())

    metadata = _load_baked_metadata()
    if metadata and metadata.get("model_name"):
        baked_name = str(metadata["model_name"])
        env_name = ENV_MODEL_NAME.strip() if ENV_MODEL_NAME else ""
        if env_name and env_name != baked_name:
            # Refuse to start rather than report an incorrect provenance:
            # the env var has drifted from the actually-baked weights and
            # the operator must rebuild (or align the env) to fix it.
            raise RuntimeError(
                f"EMBEDDING_MODEL_NAME='{ENV_MODEL_NAME}' does not match "
                f"the baked model '{baked_name}' in {MODEL_DIR}. Rebuild "
                "the image or align the env var with the baked weights."
            )
        _model_name = baked_name
    else:
        LOG.warning(
            "No baked metadata at %s/%s; reporting model name from env var.",
            MODEL_DIR,
            METADATA_FILENAME,
        )
        _model_name = ENV_MODEL_NAME

    LOG.info(
        "Loaded model '%s'. dim=%s expected=%s",
        _model_name,
        _actual_dim,
        EXPECTED_DIM,
    )

    if _actual_dim != EXPECTED_DIM:
        # Don't crash here — the backend's schema check is the source of
        # truth for dimension matching — but make the mismatch loud.
        LOG.warning(
            "Embedding dim mismatch: model=%s EMBEDDING_DIM=%s",
            _actual_dim,
            EXPECTED_DIM,
        )

    yield


app = FastAPI(
    title="rag-flight-lab embedding model",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if _model is not None else "loading",
        "model": _model_name,
        "dim": _actual_dim,
    }


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    if _model is None or _actual_dim is None:
        raise HTTPException(status_code=503, detail="model not loaded")

    if not req.texts:
        return EmbedResponse(embeddings=[], model=_model_name, dim=_actual_dim)

    if len(req.texts) > MAX_BATCH:
        raise HTTPException(
            status_code=413,
            detail=f"batch too large: {len(req.texts)} > {MAX_BATCH}",
        )

    # Bound per-text and total payload size so a single request can't pin
    # the CPU or balloon memory. These run before encoding.
    total_chars = 0
    for i, text in enumerate(req.texts):
        text_len = len(text)
        if text_len > MAX_TEXT_CHARS:
            raise HTTPException(
                status_code=413,
                detail=(f"text at index {i} too large: {text_len} > {MAX_TEXT_CHARS} chars"),
            )
        total_chars += text_len
        if total_chars > MAX_TOTAL_CHARS:
            raise HTTPException(
                status_code=413,
                detail=(f"total payload too large: > {MAX_TOTAL_CHARS} chars"),
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
        model=_model_name,
        dim=_actual_dim,
    )
