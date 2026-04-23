"""
Embedding helpers for semantic document search.

Uses OpenAI text-embedding-3-small (1536 dimensions).
Vectors are stored as raw float32 blobs compatible with sqlite-vec.
"""
from __future__ import annotations

import struct

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMS = 1536


CHUNK_SIZE    = 1800  # characters per chunk (~450 tokens)
CHUNK_OVERLAP = 200   # overlap between consecutive chunks


def build_document_text(doc: dict) -> str:
    """Metadata string for doc-level embedding (used for find-by-attribute queries)."""
    parts = [
        doc.get("new_filename") or "",
        doc.get("document_type") or "",
        doc.get("sender") or "",
        doc.get("company") or "",
        doc.get("recipient") or "",
        doc.get("keywords") or "",
    ]
    return " ".join(p for p in parts if p).strip()


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks, breaking at whitespace boundaries."""
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        # extend to next whitespace so we don't cut mid-word
        if end < len(text):
            ws = text.find(" ", end)
            if ws != -1 and ws - end < 100:
                end = ws
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - overlap
    return [c for c in chunks if c]


def get_embedding(text: str, api_key: str, max_retries: int = 3) -> list[float]:
    import time
    from openai import OpenAI, RateLimitError, APIError
    client = OpenAI(api_key=api_key)
    delay = 2.0
    for attempt in range(max_retries):
        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=text[:8000],
            )
            import cost_tracker
            cost_tracker.log(EMBEDDING_MODEL, "embedding", response.usage)
            vector = response.data[0].embedding
            assert len(vector) == EMBEDDING_DIMS, (
                f"Unexpected embedding dimensions: got {len(vector)}, expected {EMBEDDING_DIMS}"
            )
            return vector
        except (RateLimitError, APIError) as exc:
            if attempt == max_retries - 1:
                raise
            print(f"Embedding : retry {attempt + 1}/{max_retries} after error: {exc}")
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("get_embedding: exhausted retries")


def serialize(vector: list[float]) -> bytes:
    """Pack a float list into a float32 blob for sqlite-vec."""
    return struct.pack(f"{len(vector)}f", *vector)
