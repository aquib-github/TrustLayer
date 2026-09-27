"""
KB Embedding Population Script for TrustLayer (Module 1).

Reads all documents from kb_documents, chunks them, embeds each chunk using
BGE-M3, and stores the results in kb_embeddings via pgvector.

Usage:
    uv run python -m trustlayer.grounding.populate_embeddings

NOTE: This script operates on SYNTHETIC knowledge base documents created by
db/seed_kb.py. Run that script first if kb_documents is empty.
"""

import asyncio
import logging
import sys
import time

from sqlalchemy import select, func

from trustlayer.db.models import KBDocument, KBEmbedding
from trustlayer.db.session import async_session
from trustlayer.grounding.retrieve import store_embeddings_for_document

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def populate_embeddings() -> None:
    """
    For every kb_document, chunk its content, embed with BGE-M3, and INSERT
    into kb_embeddings (skipping docs that already have embeddings).
    """
    t0 = time.time()

    async with async_session() as session:
        # Get all documents
        result = await session.execute(select(KBDocument))
        docs = result.scalars().all()

        if not docs:
            logger.warning(
                "No documents found in kb_documents. "
                "Run `uv run python -m trustlayer.db.seed_kb` first."
            )
            return

        # Get doc_ids that already have embeddings
        existing_result = await session.execute(
            select(KBEmbedding.doc_id).distinct()
        )
        existing_doc_ids = set(existing_result.scalars().all())

    docs_to_process = [d for d in docs if d.id not in existing_doc_ids]
    logger.info(
        "Found %d documents total, %d already have embeddings, %d to process.",
        len(docs), len(existing_doc_ids), len(docs_to_process),
    )

    if not docs_to_process:
        logger.info("All documents already have embeddings. Nothing to do.")
        return

    total_chunks = 0
    for i, doc in enumerate(docs_to_process, 1):
        logger.info(
            "[%d/%d] Embedding doc '%s' (id=%s, role_scope=%s)…",
            i, len(docs_to_process), doc.title, doc.id, doc.role_scope,
        )
        n_chunks = await store_embeddings_for_document(doc.id, doc.content)
        total_chunks += n_chunks
        logger.info("  → Stored %d chunk(s).", n_chunks)

    elapsed = time.time() - t0
    logger.info(
        "Done. Embedded %d documents into %d chunks in %.1f seconds.",
        len(docs_to_process), total_chunks, elapsed,
    )

    # Verify final count
    async with async_session() as session:
        count_result = await session.execute(
            select(func.count()).select_from(KBEmbedding)
        )
        total_embeddings = count_result.scalar()
        logger.info("Total rows in kb_embeddings: %d", total_embeddings)


if __name__ == "__main__":
    asyncio.run(populate_embeddings())
