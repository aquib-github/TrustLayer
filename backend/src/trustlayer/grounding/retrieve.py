"""
Access-aware retrieval module for TrustLayer (Module 1, Step 3).

Embeds text using BGE-M3 (sentence-transformers, running locally) and
stores/retrieves chunks in kb_embeddings via pgvector similarity search.

Retrieval is access-aware: it filters by the requesting user's role_scope
on kb_documents *before* the vector search runs (docs/05 §2, docs/02 §3 step 3).
"""

import logging
import uuid
from typing import Any

import torch
from sentence_transformers import SentenceTransformer
from sqlalchemy import select, text as sa_text
from sqlalchemy.orm import selectinload

from trustlayer.db.models import KBDocument, KBEmbedding
from trustlayer.db.session import async_session

logger = logging.getLogger(__name__)

# ── Singleton model loader ────────────────────────────────────────────────

_embedding_model: SentenceTransformer | None = None


def get_embedding_model() -> SentenceTransformer:
    """Lazy-load BGE-M3 once and cache globally."""
    global _embedding_model
    if _embedding_model is None:
        logger.info("Loading BGE-M3 embedding model (first call — may download ~2 GB)…")
        _embedding_model = SentenceTransformer("BAAI/bge-m3")
        logger.info(
            "BGE-M3 loaded. Embedding dimension: %d",
            _embedding_model.get_sentence_embedding_dimension(),
        )
    return _embedding_model


# ── Embedding helper ──────────────────────────────────────────────────────


def embed_text(text: str) -> list[float]:
    """Embed a single text string using BGE-M3. Returns a list[float] vector."""
    model = get_embedding_model()
    # BGE-M3 benefits from the instruction prefix for retrieval queries
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch-embed multiple text strings. Returns list of vector lists."""
    model = get_embedding_model()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)
    return embeddings.tolist()


# ── Chunking ──────────────────────────────────────────────────────────────


def chunk_document(content: str, max_chunk_chars: int = 500, overlap_chars: int = 50) -> list[str]:
    """
    Split document content into overlapping chunks.

    For the synthetic KB (short policy docs ~2-4 sentences each) most docs will
    produce a single chunk, which is intentional — these are already atomic policies.
    The chunking is here for longer real-world documents in future.
    """
    if len(content) <= max_chunk_chars:
        return [content]

    chunks: list[str] = []
    start = 0
    while start < len(content):
        end = start + max_chunk_chars
        chunk = content[start:end]
        chunks.append(chunk.strip())
        start = end - overlap_chars
    return [c for c in chunks if c]


# ── Store embeddings ──────────────────────────────────────────────────────


async def store_embeddings_for_document(
    doc_id: uuid.UUID,
    content: str,
    max_chunk_chars: int = 500,
    overlap_chars: int = 50,
) -> int:
    """
    Chunk a document's content, embed each chunk with BGE-M3, and INSERT into
    kb_embeddings.  Returns the number of chunks stored.
    """
    chunks = chunk_document(content, max_chunk_chars, overlap_chars)
    if not chunks:
        return 0

    vectors = embed_texts(chunks)

    async with async_session() as session:
        for idx, (chunk_text, vec) in enumerate(zip(chunks, vectors)):
            embedding_obj = KBEmbedding(
                id=uuid.uuid4(),
                doc_id=doc_id,
                chunk_text=chunk_text,
                embedding=vec,
                chunk_index=idx,
            )
            session.add(embedding_obj)
        await session.commit()

    return len(chunks)


# ── Access-aware retrieval ────────────────────────────────────────────────


async def retrieve_evidence(
    query: str,
    role: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """
    Embed the query with BGE-M3 and retrieve top-k evidence chunks from
    kb_embeddings, filtered by role_scope on the parent kb_documents row.

    Access-aware: only documents whose role_scope array contains the given
    role are considered (docs/05 §2: "retrieval query always filters by the
    requesting user's role before the vector search runs, not after").

    Returns a list of dicts:
        [{"chunk_text": str, "doc_id": UUID, "doc_title": str, "similarity": float}, ...]
    """
    query_vec = embed_text(query)

    async with async_session() as session:
        # Use raw connection to call pgvector operator directly, avoiding
        # asyncpg parameter-style conflicts with SQLAlchemy's text() binding.
        raw_conn = await session.connection()
        conn_underlying = await raw_conn.get_raw_connection()
        asyncpg_conn = conn_underlying.driver_connection  # the actual asyncpg connection

        rows = await asyncpg_conn.fetch(
            """
            SELECT
                e.chunk_text,
                e.doc_id,
                d.title AS doc_title,
                d.role_scope AS role_scope,
                1 - (e.embedding <=> $1::vector) AS similarity
            FROM kb_embeddings e
            JOIN kb_documents d ON d.id = e.doc_id
            WHERE $2 = ANY(d.role_scope)
            ORDER BY e.embedding <=> $1::vector
            LIMIT $3
            """,
            str(query_vec),  # pgvector accepts text representation
            role,
            top_k,
        )

    return [
        {
            "chunk_text": row["chunk_text"],
            "doc_id": str(row["doc_id"]),
            "doc_title": row["doc_title"],
            "role_scope": row["role_scope"],
            "similarity": float(row["similarity"]),
        }
        for row in rows
    ]

