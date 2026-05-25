"""
Phase 2 — Semantic Chunking & Embedding into ChromaDB.

Uses MarkdownHeaderTextSplitter as the primary strategy (splits on
# / ## / ### boundaries from Azure DI output), with a character-based
RecursiveCharacterTextSplitter as fallback for table-dense pages.

Embeds with a LOCAL sentence-transformers model (all-MiniLM-L6-v2,
384 dims) that runs entirely on CPU — no API key required.
"""

import logging

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document

logger = logging.getLogger(__name__)

# ── Primary: semantic splitter using Markdown headers (from Azure DI output) ──
_HEADERS_TO_SPLIT_ON = [
    ("#", "Header 1"),    # e.g. 'Financial Statements'
    ("##", "Header 2"),   # e.g. 'Income Statement'
    ("###", "Header 3"),  # e.g. 'Revenue Breakdown'
]

_md_splitter = MarkdownHeaderTextSplitter(
    headers_to_split_on=_HEADERS_TO_SPLIT_ON,
)

# ── Fallback: character-based splitter for table-dense pages without headers ──
_fallback_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=100,
    length_function=len,
)

# ── Local embedding model (runs on CPU, no API key needed) ──
_embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2",
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)


def build_vectorstore(
    pages: list[dict],
    persist_dir: str = "./chroma_db",
) -> Chroma:
    """
    Chunk pages and embed into a persistent ChromaDB vectorstore.

    Strategy:
      1. Try MarkdownHeaderTextSplitter first (semantic, header-based).
      2. Fall back to RecursiveCharacterTextSplitter for pages with
         no Markdown headers (data-dense tables).

    Each chunk preserves metadata: page_no, company_name, chunk_id,
    split_type (markdown_header | fallback_fixed).

    Args:
        pages:       List of page dicts from parser.parse_document().
        persist_dir: Directory for ChromaDB persistence.

    Returns:
        Chroma vectorstore instance.
    """
    all_docs: list[Document] = []

    for page in pages:
        md_text = page["text"]  # Already Markdown from Azure DI
        page_no = page["page_no"]
        company = page["company_name"]

        # Try semantic splitting first
        md_chunks = _md_splitter.split_text(md_text)

        if md_chunks:
            for i, chunk in enumerate(md_chunks):
                # MarkdownHeaderTextSplitter returns Document objects
                content = chunk.page_content if hasattr(chunk, "page_content") else str(chunk)
                all_docs.append(
                    Document(
                        page_content=content,
                        metadata={
                            "page_no": page_no,
                            "company_name": company,
                            "chunk_id": f"p{page_no}_md{i}",
                            "split_type": "markdown_header",
                        },
                    )
                )
        else:
            # Fallback for table-only pages (no Markdown headers)
            fb_chunks = _fallback_splitter.split_text(md_text)
            for i, chunk_text in enumerate(fb_chunks):
                all_docs.append(
                    Document(
                        page_content=chunk_text,
                        metadata={
                            "page_no": page_no,
                            "company_name": company,
                            "chunk_id": f"p{page_no}_fb{i}",
                            "split_type": "fallback_fixed",
                        },
                    )
                )

    logger.info(
        "Chunked %d pages into %d documents. Embedding with all-MiniLM-L6-v2 (local CPU)...",
        len(pages),
        len(all_docs),
    )

    vectorstore = Chroma.from_documents(
        all_docs,
        _embeddings,
        persist_directory=persist_dir,
    )

    logger.info(
        "Indexed %d chunks from %d pages into ChromaDB at '%s'.",
        len(all_docs),
        len(pages),
        persist_dir,
    )
    return vectorstore
