"""
Phase 3 — Two-Stage Retrieval & Reranking.

Stage 1: Cosine similarity search → Top 500 candidate chunks.
Stage 2: CohereRerank v3           → Top 40 precision-filtered chunks.

Uses LangChain's ContextualCompressionRetriever to chain the two stages.
"""

import os
import logging

from langchain_cohere import CohereRerank
from langchain_classic.retrievers.contextual_compression import ContextualCompressionRetriever
from langchain_chroma import Chroma

logger = logging.getLogger(__name__)


def build_retriever(
    vectorstore: Chroma,
    initial_k: int = 500,
    rerank_top_n: int = 400,
) -> ContextualCompressionRetriever:
    """
    Build a two-stage retriever:
      1. Cosine similarity → top `initial_k` candidates (broad recall).
      2. CohereRerank v3   → top `rerank_top_n` chunks (high precision).

    Args:
        vectorstore:  ChromaDB vectorstore from embeddings.build_vectorstore().
        initial_k:    Number of candidates from the cosine similarity stage.
        rerank_top_n: Number of chunks after CohereRerank filtering.

    Returns:
        ContextualCompressionRetriever wrapping both stages.
    """
    cohere_key = os.getenv("COHERE_API_KEY")
    if not cohere_key:
        raise EnvironmentError(
            "Missing COHERE_API_KEY in environment variables. "
            "See .env.example for required keys."
        )

    # Stage 1: broad recall via cosine similarity
    base_retriever = vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": initial_k},
    )
    logger.info(
        "Stage 1 retriever: cosine similarity, k=%d", initial_k
    )

    # Stage 2: precision filtering via CohereRerank
    reranker = CohereRerank(
        model="rerank-english-v3.0",
        top_n=rerank_top_n,
        cohere_api_key=cohere_key,
    )
    logger.info(
        "Stage 2 reranker: CohereRerank v3, top_n=%d", rerank_top_n
    )

    retriever = ContextualCompressionRetriever(
        base_compressor=reranker,
        base_retriever=base_retriever,
    )

    logger.info("Two-stage retriever built: Top-%d → Rerank → Top-%d", initial_k, rerank_top_n)
    return retriever
