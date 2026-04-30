"""Build-time model downloader for the generation service.

This script runs **only** in the ``weights`` stage of the multi-stage
Docker build, where outbound network access to Hugging Face is available.
It downloads the configured generation model and saves it to
``/models/generation`` so the runtime stage can ``COPY --from=weights``
the weights into the final image. The runtime container itself never
reaches out to the network.

The default engine is ``transformers`` (Hugging Face). This downloader
fetches a snapshot of the model repository (config, tokenizer, weights)
into a single directory the runtime can later load offline by path.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

DEFAULT_MODEL_NAME = "Qwen/Qwen2.5-0.5B-Instruct"
DEFAULT_OUTPUT_DIR = "/models/generation"
# Filename used to persist build-time provenance metadata next to the
# weights. The runtime reads this to report the *baked* model identifier
# rather than trusting an environment variable that may have drifted from
# what was actually downloaded.
METADATA_FILENAME = "rag_flight_lab_model.json"

# Files that are sufficient to load a Hugging Face causal-LM model
# offline. `*.bin` and `*.safetensors` weight files are pulled by glob
# and either format is acceptable — the runtime only needs one.
ALLOW_PATTERNS = [
    "config.json",
    "generation_config.json",
    "tokenizer*",
    "vocab*",
    "merges*",
    "special_tokens_map.json",
    "added_tokens.json",
    "chat_template*",
    "*.model",
    "*.safetensors",
    "*.bin",
]


def main() -> int:
    model_name = os.environ.get("GENERATION_MODEL_NAME", DEFAULT_MODEL_NAME)
    output_dir = Path(os.environ.get("GENERATION_MODEL_DIR", DEFAULT_OUTPUT_DIR))

    output_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"[download_model] Fetching '{model_name}' -> {output_dir}",
        flush=True,
    )

    # `snapshot_download` writes the repo files directly into the target
    # directory. Disabling symlinks ensures the files are real and can be
    # `COPY --from=weights` into the runtime image without dangling links.
    snapshot_download(
        repo_id=model_name,
        local_dir=str(output_dir),
        local_dir_use_symlinks=False,
        allow_patterns=ALLOW_PATTERNS,
    )

    # Sanity check: a usable HF causal-LM snapshot must contain a
    # config.json plus at least one weight file (.safetensors or .bin).
    config_path = output_dir / "config.json"
    if not config_path.exists():
        print(
            f"[download_model] ERROR: config.json not found in {output_dir}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    weight_files = list(output_dir.glob("*.safetensors")) + list(output_dir.glob("*.bin"))
    if not weight_files:
        print(
            f"[download_model] ERROR: no weight files (*.safetensors / *.bin) "
            f"found in {output_dir}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    # Record build-time provenance so the runtime can report the *actual*
    # baked model identifier regardless of what env vars the operator
    # passes at runtime.
    metadata = {
        "model_name": model_name,
        "engine": "transformers",
    }
    (output_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2))

    print(f"[download_model] Done. metadata={metadata}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
