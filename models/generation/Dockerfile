# syntax=docker/dockerfile:1
#
# Multi-stage build for the generation model service.
#
# Stage 1 ("weights") has network access and downloads the configured
# Hugging Face generation model into /models/generation. Stage 2 (the
# runtime image) copies those weights in and runs fully offline -- the
# model container is attached only to the internal Docker `model_net`,
# has no host port mapping, and has HF_HUB_OFFLINE=1 /
# TRANSFORMERS_OFFLINE=1 set so it cannot reach Hugging Face at runtime.

ARG GENERATION_MODEL_NAME=Qwen/Qwen2.5-0.5B-Instruct
# MODEL_RUNTIME selects which torch wheel is installed in the runtime
# stage:
#   cpu  -> CPU-only wheel from download.pytorch.org/whl/cpu (default)
#   gpu  -> CUDA-enabled wheel from download.pytorch.org/whl/cu121
# The default keeps `docker compose up --build` CPU-only. The GPU
# Compose override (docker-compose.gpu.yml) sets MODEL_RUNTIME=gpu so
# the resulting image can actually use an attached NVIDIA device.
ARG MODEL_RUNTIME=cpu


# ---------------------------------------------------------------------------
# Stage 1 — weights: download the model at build time only.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS weights

ARG GENERATION_MODEL_NAME
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GENERATION_MODEL_NAME=${GENERATION_MODEL_NAME} \
    GENERATION_MODEL_DIR=/models/generation

WORKDIR /build

# The downloader does not need torch (it only fetches files via
# huggingface-hub), so we keep the weights stage slim and skip the
# PyTorch index entirely here.
COPY requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir \
        "huggingface-hub==0.27.1"

COPY download_model.py /build/download_model.py

RUN mkdir -p /models/generation \
 && python /build/download_model.py \
 && test -f /models/generation/config.json


# ---------------------------------------------------------------------------
# Stage 2 — runtime: minimal image, weights baked in, no download tooling.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ARG GENERATION_MODEL_NAME
ARG MODEL_RUNTIME

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GENERATION_MODEL_NAME=${GENERATION_MODEL_NAME} \
    MODEL_RUNTIME=${MODEL_RUNTIME} \
    GENERATION_MODEL_DIR=/models/generation \
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

# Install torch from the dedicated PyTorch index, scoped to torch alone
# via --index-url. Then install the rest from PyPI without the torch
# index in scope, so unrelated packages can never be resolved from
# download.pytorch.org (supply-chain hardening).
#
# `MODEL_RUNTIME` selects the wheel: `cpu` (default) keeps the image
# small and CPU-only; `gpu` installs the CUDA 12.1 wheel so the
# container can use an NVIDIA device when launched via the GPU Compose
# override. We pin one version per variant explicitly to avoid implicit
# resolution differences between indexes.
RUN set -eux; \
    case "${MODEL_RUNTIME}" in \
      gpu) \
        pip install --no-cache-dir \
            --index-url https://download.pytorch.org/whl/cu121 \
            torch==2.6.0 ;; \
      cpu|"") \
        pip install --no-cache-dir \
            --index-url https://download.pytorch.org/whl/cpu \
            torch==2.6.0+cpu ;; \
      *) \
        echo "unsupported MODEL_RUNTIME='${MODEL_RUNTIME}' (expected cpu|gpu)" >&2; \
        exit 1 ;; \
    esac

# Install runtime Python deps.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the pre-downloaded model weights from the weights stage. No
# network access is used after this point.
COPY --from=weights /models/generation /models/generation

# Application code.
COPY app.py /app/app.py

# Run as non-root. The model files in /models/generation are owned by
# root and remain read-only at runtime, which is compatible with
# read_only: true on the container (only /tmp is writable via tmpfs in
# compose).
RUN useradd --system --uid 1001 --no-create-home appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
