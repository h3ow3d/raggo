# syntax=docker/dockerfile:1
#
# Multi-stage build for the embedding model service.
#
# Stage 1 ("weights") has network access and downloads the configured
# sentence-transformers model into /models/embedding. Stage 2 (the runtime
# image) copies those weights in and runs fully offline -- the model
# container is attached only to the internal Docker `model_net`, has no
# host port mapping, and has HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1 set
# so it cannot reach Hugging Face at runtime.

ARG EMBEDDING_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
ARG EMBEDDING_DIM=384


# ---------------------------------------------------------------------------
# Stage 1 — weights: download the model at build time only.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS weights

ARG EMBEDDING_MODEL_NAME
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EMBEDDING_MODEL_NAME=${EMBEDDING_MODEL_NAME} \
    EMBEDDING_MODEL_DIR=/models/embedding

WORKDIR /build

# Install torch from the dedicated CPU-only PyTorch index *first*, scoped
# to torch alone via --index-url. Then install the rest from PyPI without
# the torch index in scope, so unrelated packages can never be resolved
# from download.pytorch.org (supply-chain hardening).
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.6.0+cpu

COPY requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir -r /build/requirements.txt

COPY download_model.py /build/download_model.py

RUN mkdir -p /models/embedding \
 && python /build/download_model.py \
 && test -f /models/embedding/modules.json


# ---------------------------------------------------------------------------
# Stage 2 — runtime: minimal image, weights baked in, no download tooling.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ARG EMBEDDING_MODEL_NAME
ARG EMBEDDING_DIM

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EMBEDDING_MODEL_NAME=${EMBEDDING_MODEL_NAME} \
    EMBEDDING_DIM=${EMBEDDING_DIM} \
    EMBEDDING_MODEL_DIR=/models/embedding \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1

# Point HuggingFace caches at the writable tmpfs mounted on /tmp. The
# runtime user has no home directory (--no-create-home) and the container
# filesystem is read_only, so the default ~/.cache/huggingface location is
# not writable. We never actually fetch anything at runtime
# (HF_HUB_OFFLINE=1 and weights are baked in), but several HF libraries
# try to ensure the cache directory exists at import time and emit a
# noisy warning if it cannot be created.
ENV HF_HOME=/tmp/huggingface \
    TRANSFORMERS_CACHE=/tmp/huggingface/transformers \
    HF_HUB_CACHE=/tmp/huggingface/hub \
    XDG_CACHE_HOME=/tmp/cache

WORKDIR /app

# Install torch from the CPU-only PyTorch index (scoped to torch) before
# the rest of the deps, mirroring the weights stage so the runtime sees
# identical library versions and avoids cross-index resolution.
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.6.0+cpu

# Install runtime Python deps. Same requirements file as the weights stage
# so the loader sees identical library versions.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the pre-downloaded model weights from the weights stage. No network
# access is used after this point.
COPY --from=weights /models/embedding /models/embedding

# Application code.
COPY app.py /app/app.py

# Run as non-root. The model files in /models/embedding are owned by root
# and remain read-only at runtime, which is compatible with read_only: true
# on the container (only /tmp is writable via tmpfs in compose).
RUN useradd --system --uid 1001 --no-create-home appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Probe the FastAPI lifespan-loaded model. `start_period` is generous
# because cold-loading on CPU can take a while on first boot.
HEALTHCHECK --interval=15s --timeout=5s --start-period=180s --retries=20 \
    CMD python -c "import json,sys,urllib.request; \
sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)).get('status')=='ok' else 1)" \
    || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
