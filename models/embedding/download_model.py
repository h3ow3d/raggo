"""Build-time model downloader for the embedding service.

This script runs **only** in the `weights` stage of the multi-stage Docker
build, where outbound network access to Hugging Face is available. It
downloads the configured sentence-transformers model and saves it to
``/models/embedding`` so the runtime stage can ``COPY --from=weights`` the
weights into the final image. The runtime container itself never reaches
out to the network.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sentence_transformers import SentenceTransformer


DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_OUTPUT_DIR = "/models/embedding"
# Filename used to persist build-time provenance metadata next to the
# weights. The runtime reads this to report the *baked* model identifier
# rather than trusting an environment variable that may have drifted from
# what was actually downloaded.
METADATA_FILENAME = "rag_flight_lab_model.json"


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

    # Record build-time provenance so the runtime can report the *actual*
    # baked model identifier (and dimension) regardless of what env vars
    # the operator passes at runtime.
    metadata = {
        "model_name": model_name,
        "embedding_dim": int(model.get_sentence_embedding_dimension()),
    }
    (output_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2))

    print(f"[download_model] Done. metadata={metadata}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

