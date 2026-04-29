"""Build-time model downloader for the embedding service.

This script runs **only** in the `weights` stage of the multi-stage Docker
build, where outbound network access to Hugging Face is available. It
downloads the configured sentence-transformers model and saves it to
``/models/embedding`` so the runtime stage can ``COPY --from=weights`` the
weights into the final image. The runtime container itself never reaches
out to the network.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sentence_transformers import SentenceTransformer


DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_OUTPUT_DIR = "/models/embedding"


def main() -> int:
    model_name = os.environ.get("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME)
    output_dir = Path(os.environ.get("EMBEDDING_MODEL_DIR", DEFAULT_OUTPUT_DIR))

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[download_model] Fetching '{model_name}' -> {output_dir}", flush=True)

    # Loading then saving via sentence-transformers writes the full model
    # bundle (config, tokenizer, weights, modules.json, ...) into a single
    # directory that the runtime can later load offline by path.
    model = SentenceTransformer(model_name)
    model.save(str(output_dir))

    # Sanity check: a saved sentence-transformers model always contains a
    # modules.json describing the pipeline.
    if not (output_dir / "modules.json").exists():
        print(
            f"[download_model] ERROR: modules.json not found in {output_dir}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print("[download_model] Done.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
