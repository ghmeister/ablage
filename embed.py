"""
Embedding helpers for semantic document search.

Uses OpenAI text-embedding-3-small (1536 dimensions).
Vectors are stored as raw float32 blobs compatible with sqlite-vec.
"""
from __future__ import annotations

import struct

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMS = 1536


def build_document_text(doc: dict) -> str:
    """Concatenate the searchable fields of a document into one string for embedding."""
    parts = [
        doc.get("new_filename") or "",
        doc.get("document_type") or "",
        doc.get("sender") or "",
        doc.get("company") or "",
        doc.get("recipient") or "",
        doc.get("keywords") or "",
    ]
    return " ".join(p for p in parts if p).strip()


def get_embedding(text: str, api_key: str) -> list[float]:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text[:8000],
    )
    return response.data[0].embedding


def serialize(vector: list[float]) -> bytes:
    """Pack a float list into a float32 blob for sqlite-vec."""
    return struct.pack(f"{len(vector)}f", *vector)
