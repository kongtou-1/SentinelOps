import hashlib
import json
from functools import lru_cache

from fastapi import HTTPException

import app.repositories.knowledge.index as _repository
from app.core.config import get_settings
from app.models import KnowledgeChunk

MODEL = "BAAI/bge-small-zh-v1.5"
MODEL_REVISION = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"


@lru_cache(maxsize=1)
def encoder():
    from fastembed import TextEmbedding

    path = get_settings().embedding_model_dir
    try:
        manifest = json.loads((path / "manifest.json").read_text())
        if manifest["revision"] != MODEL_REVISION:
            raise ValueError
        for name, digest in manifest["sha256"].items():
            if hashlib.sha256((path / name).read_bytes()).hexdigest() != digest:
                raise ValueError
        return TextEmbedding(
            MODEL,
            specific_model_path=str(path),
            local_files_only=True,
            threads=1,
            providers=["CPUExecutionProvider"],
        )
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, "Embedding model is unavailable; run prepare_ai.py") from None


def split_content(title, content):
    # Token offsets preserve original Markdown, including headings and paragraph boundaries.
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(get_settings().embedding_model_dir / "tokenizer.json"))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    text = title + "\n\n" + content
    offsets = tokenizer.encode(text, add_special_tokens=False).offsets
    chunks, position = [], 0
    while position < len(offsets):
        end = min(position + 400, len(offsets))
        if end < len(offsets):
            # Prefer a paragraph/heading break in the final 80 tokens of a full chunk.
            candidates = [
                i
                for i in range(max(position + 320, position + 1), end)
                if "\n" in text[offsets[i - 1][1] : offsets[i][0]]
            ]
            if candidates:
                end = candidates[-1]
        chunks.append(text[offsets[position][0] : offsets[end - 1][1]])
        if end == len(offsets):
            break
        position = end - 50
    return chunks


def withdraw(db, document_id):
    _repository.withdraw_query(db, document_id)


def build_index(db, document_id, version, chunks, vectors):
    document = _repository.build_index_query(db, document_id)
    if document.deleted_at or document.version != version:
        db.rollback()
        return False
    withdraw(db, document_id)
    for position, (content, vector) in enumerate(zip(chunks, vectors, strict=True)):
        _repository.build_index_query_2(content, db, document_id, position, vector, version)
    document.index_status, document.error_code = "ready", None
    db.commit()
    return True


def search(db, query, tag=None, limit=5, application_ids=None, versions=None):
    vector = next(encoder().query_embed(query)).tolist()
    distance = KnowledgeChunk.embedding.cosine_distance(vector)
    statement = _repository.search_query(distance, application_ids, versions)
    if tag:
        # JSON tags are small; this keeps the existing generic JSON column convention.
        statement = _repository.search_query_2(statement, tag)
    rows = _repository.search_query_3(db, distance, limit, statement)
    return [
        {
            "id": str(chunk.id),
            "document_id": str(document.id),
            "version": chunk.version,
            "title": document.title,
            "source": document.source,
            "content": chunk.content,
            "similarity": 1 - float(score),
        }
        for chunk, document, score in rows
    ]
