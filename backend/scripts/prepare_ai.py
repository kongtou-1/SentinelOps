"""Explicit, pinned download. Runtime paths only ever load local files."""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from huggingface_hub import snapshot_download

from app.core.config import get_settings
from app.services.knowledge.index import MODEL, MODEL_REVISION


def main():
    path = get_settings().embedding_model_dir
    get_settings().ai_key_file.parent.mkdir(parents=True, exist_ok=True)
    names = [
        "config.json",
        "model_optimized.onnx",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.txt",
    ]
    snapshot_download(
        "Qdrant/bge-small-zh-v1.5", revision=MODEL_REVISION, allow_patterns=names, local_dir=path
    )
    manifest = {
        "model": MODEL,
        "revision": MODEL_REVISION,
        "sha256": {name: hashlib.sha256((path / name).read_bytes()).hexdigest() for name in names},
    }
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Pinned Chinese embedding model prepared")


if __name__ == "__main__":
    main()
