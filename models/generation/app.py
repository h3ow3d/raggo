"""FastAPI service exposing the generation model.

Contract (internal-only, see docker-compose.yml):

    POST /generate
        request:  {"prompt": "...",
                   "max_new_tokens": 512,
                   "temperature": 0.2}
        response: {"text": "...",
                   "model": "...",
                   "finish_reason": "stop|length"}

If generation exceeds the per-request wall-clock budget the service
responds with HTTP 504 ("generation timed out after Ns") rather than a
``GenerateResponse`` body — clients should treat 504 as the timeout
signal.

The model is loaded **once** at startup from a local directory baked into
the image (default ``/models/generation``). The container has
``HF_HUB_OFFLINE=1`` / ``TRANSFORMERS_OFFLINE=1`` set, and is attached
only to the internal Docker ``model_net``, so no outbound network calls
occur at runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModelForCausalLM, AutoTokenizer


LOG = logging.getLogger("generation-model")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


MODEL_DIR = os.environ.get("GENERATION_MODEL_DIR", "/models/generation")
METADATA_FILENAME = "rag_flight_lab_model.json"
ENV_MODEL_NAME = os.environ.get(
    "GENERATION_MODEL_NAME", "Qwen/Qwen2.5-0.5B-Instruct"
)

# Defaults / caps for generation parameters. Caps bound CPU/memory work
# per request even on the internal network.
DEFAULT_MAX_NEW_TOKENS = int(os.environ.get("GEN_DEFAULT_MAX_NEW_TOKENS", "512"))
MAX_MAX_NEW_TOKENS = int(os.environ.get("GEN_MAX_MAX_NEW_TOKENS", "1024"))
DEFAULT_TEMPERATURE = float(os.environ.get("GEN_DEFAULT_TEMPERATURE", "0.2"))
MAX_PROMPT_CHARS = int(os.environ.get("GEN_MAX_PROMPT_CHARS", "32000"))
# Per-request wall-clock cap. Generation that exceeds this is cancelled
# and the client receives a 504 with finish_reason="timeout".
GENERATE_TIMEOUT_SECONDS = float(
    os.environ.get("GEN_TIMEOUT_SECONDS", "120")
)
# Bound concurrency so a stampede can't pin all CPU cores at once.
MAX_CONCURRENCY = int(os.environ.get("GEN_MAX_CONCURRENCY", "1"))


class GenerateRequest(BaseModel):
    prompt: str = Field(..., description="Prompt to generate from.")
    max_new_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        description="Maximum number of new tokens to generate.",
    )
    temperature: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Sampling temperature. 0 = greedy.",
    )


class GenerateResponse(BaseModel):
    text: str
    model: str
    finish_reason: str


_tokenizer = None
_model = None
_model_name: str = ENV_MODEL_NAME
# Bounded executor: requests are dispatched onto it via
# `loop.run_in_executor(_executor, ...)`. With `max_workers=MAX_CONCURRENCY`
# the executor itself caps in-flight `model.generate` calls — even when an
# `asyncio.wait_for(...)` times out the underlying worker thread keeps
# running and continues to occupy a slot, so timed-out requests cannot
# pile up additional concurrent generations. This is intentionally
# stricter than a semaphore released on request exit.
_executor: Optional[ThreadPoolExecutor] = None


def _load_baked_metadata() -> Optional[dict]:
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
    global _tokenizer, _model, _model_name, _executor

    if not os.path.isdir(MODEL_DIR):
        raise RuntimeError(
            f"Generation model directory '{MODEL_DIR}' not found. The image "
            "must be built so weights are baked in via the multi-stage build."
        )

    # Resolve provenance from the baked metadata before loading so that a
    # mismatched env var fails fast and loudly.
    metadata = _load_baked_metadata()
    if metadata and metadata.get("model_name"):
        baked_name = str(metadata["model_name"])
        env_name = ENV_MODEL_NAME.strip() if ENV_MODEL_NAME else ""
        if env_name and env_name != baked_name:
            raise RuntimeError(
                f"GENERATION_MODEL_NAME='{ENV_MODEL_NAME}' does not match "
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

    LOG.info("Loading generation model from %s", MODEL_DIR)
    # `local_files_only=True` is belt-and-braces alongside the offline env
    # vars: even if those were unset, transformers would refuse to fetch.
    _tokenizer = AutoTokenizer.from_pretrained(
        MODEL_DIR, local_files_only=True
    )
    # Use float32 on CPU for stability; if a GPU is available (GPU
    # override compose) prefer float16 and `device_map="auto"`.
    if torch.cuda.is_available():
        LOG.info("CUDA available — loading model with device_map='auto'")
        _model = AutoModelForCausalLM.from_pretrained(
            MODEL_DIR,
            local_files_only=True,
            torch_dtype=torch.float16,
            device_map="auto",
        )
    else:
        LOG.info("CUDA not available — loading model on CPU (float32)")
        _model = AutoModelForCausalLM.from_pretrained(
            MODEL_DIR,
            local_files_only=True,
            torch_dtype=torch.float32,
        )

    _model.eval()

    # Some tokenizers (e.g. Qwen) ship without a pad token; reuse EOS so
    # batched / padded generation paths don't crash.
    if _tokenizer.pad_token_id is None and _tokenizer.eos_token_id is not None:
        _tokenizer.pad_token_id = _tokenizer.eos_token_id

    _executor = ThreadPoolExecutor(
        max_workers=MAX_CONCURRENCY,
        thread_name_prefix="generate",
    )
    LOG.info(
        "Loaded generation model '%s' (max_workers=%d)",
        _model_name,
        MAX_CONCURRENCY,
    )
    try:
        yield
    finally:
        # Don't wait for in-flight generations on shutdown — the
        # container is going away. cancel_futures is best-effort but the
        # threads themselves can't be cancelled in CPython.
        _executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(
    title="rag-flight-lab generation model",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if _model is not None else "loading",
        "model": _model_name,
    }


def _generate_sync(
    prompt: str, max_new_tokens: int, temperature: float
) -> tuple[str, str]:
    """Run blocking generation. Returns (text, finish_reason)."""
    assert _tokenizer is not None and _model is not None

    inputs = _tokenizer(prompt, return_tensors="pt", truncation=False)
    input_ids = inputs["input_ids"].to(_model.device)
    attention_mask = inputs.get("attention_mask")
    if attention_mask is not None:
        attention_mask = attention_mask.to(_model.device)

    do_sample = temperature > 0.0
    gen_kwargs = {
        "max_new_tokens": max_new_tokens,
        "do_sample": do_sample,
        "pad_token_id": _tokenizer.pad_token_id,
    }
    if do_sample:
        gen_kwargs["temperature"] = temperature

    with torch.inference_mode():
        output_ids = _model.generate(
            input_ids,
            attention_mask=attention_mask,
            **gen_kwargs,
        )

    # Strip the prompt tokens so we only return generated text.
    new_tokens = output_ids[0, input_ids.shape[1]:]
    text = _tokenizer.decode(new_tokens, skip_special_tokens=True)

    finish_reason = "length" if new_tokens.shape[0] >= max_new_tokens else "stop"
    return text, finish_reason


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    if _model is None or _tokenizer is None or _executor is None:
        raise HTTPException(status_code=503, detail="model not loaded")

    prompt = req.prompt
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt must be non-empty")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"prompt too large: {len(prompt)} > {MAX_PROMPT_CHARS} chars",
        )

    max_new_tokens = req.max_new_tokens or DEFAULT_MAX_NEW_TOKENS
    if max_new_tokens > MAX_MAX_NEW_TOKENS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"max_new_tokens too large: {max_new_tokens} > "
                f"{MAX_MAX_NEW_TOKENS}"
            ),
        )

    temperature = (
        req.temperature if req.temperature is not None else DEFAULT_TEMPERATURE
    )

    # Apply a wall-clock timeout. The blocking `model.generate` runs on
    # the bounded `_executor` (max_workers=MAX_CONCURRENCY), so the event
    # loop stays responsive and `asyncio.wait_for` can enforce the
    # deadline. Concurrency is capped by the executor itself: when a
    # request times out we cannot stop the underlying CPython thread,
    # but it keeps occupying its worker slot until it finishes — so
    # timed-out requests cannot pile up additional concurrent
    # generations beyond `MAX_CONCURRENCY`.
    loop = asyncio.get_running_loop()
    try:
        text, finish_reason = await asyncio.wait_for(
            loop.run_in_executor(
                _executor,
                _generate_sync,
                prompt,
                max_new_tokens,
                temperature,
            ),
            timeout=GENERATE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        LOG.warning(
            "generation timed out after %.1fs (max_new_tokens=%d)",
            GENERATE_TIMEOUT_SECONDS,
            max_new_tokens,
        )
        raise HTTPException(
            status_code=504,
            detail=(
                f"generation timed out after "
                f"{GENERATE_TIMEOUT_SECONDS:.0f}s"
            ),
        )

    return GenerateResponse(
        text=text,
        model=_model_name,
        finish_reason=finish_reason,
    )
