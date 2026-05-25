"""
Phase 5 — Full Pipeline Orchestration.

End-to-end runner that chains all phases:
  1. Parse PDF via Azure Document Intelligence
  2. Chunk & embed into ChromaDB
  3. Build two-stage retriever (cosine + CohereRerank)
  4. Retrieve context & extract with Gemini 1.5 Pro
  5. Validate & export JSON

Includes robust error handling with exponential backoff retry for
API failures and Pydantic auto-retry for malformed LLM output.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from .models import ExtractionResult
from .embeddings import build_vectorstore
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from .retriever import build_retriever
from .extractor import build_extraction_chain

logger = logging.getLogger(__name__)

# ── Retrieval query for financial data extraction ─────────────────────────────
_EXTRACTION_QUERY = (
    "Extract all financial statements: income statement, balance sheet, "
    "cash flow statement, EPS, margins for all companies."
)


def _retry_with_backoff(func, max_retries: int = 3, base_delay: float = 2.0):
    """
    Execute a function with exponential backoff retry.

    Args:
        func:        Callable to execute.
        max_retries: Maximum number of retry attempts.
        base_delay:  Base delay in seconds (doubles each retry).

    Returns:
        Result of the function call.

    Raises:
        Exception: The last exception if all retries are exhausted.
    """
    last_exception = None
    for attempt in range(1, max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt,
                    max_retries,
                    str(e),
                    delay,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "All %d attempts failed. Last error: %s",
                    max_retries,
                    str(e),
                )
    raise last_exception  # type: ignore[misc]


def run_pipeline(
    pdf_path: str,
    output_path: str = "financials_output.json",
    chroma_dir: str = "./chroma_db",
    max_llm_retries: int = 3,
    max_pages: Optional[int] = None,
    skip_parse: bool = False,
) -> Optional[list[dict]]:
    """
    Run the complete Financial RAG Pipeline end-to-end.

    Phases:
      1. Parse PDF with Azure Document Intelligence
      2. Chunk and embed into ChromaDB
      3. Build two-stage retriever (cosine + CohereRerank)
      4. Retrieve context chunks and extract with Gemini 3.1 Flash-Lite
      5. Validate Pydantic output and export JSON

    Args:
        pdf_path:         Path to the PDF file to process.
        output_path:      Path for the output JSON file.
        chroma_dir:       Directory for ChromaDB persistence.
        max_llm_retries:  Max retries for LLM extraction on validation errors.
        max_pages:        Optional limit on the number of pages to parse.

    Returns:
        List of company financial dicts, or None if extraction fails.
    """
    # ── Step 1 & 2: Parse and Embed (or Skip) ─────────────────────────────
    if skip_parse:
        print("[1-2/5] Skipping parsing & embedding (using existing ChromaDB)...")
        logger.info("Skipping Phases 1 and 2, loading existing ChromaDB at '%s'", chroma_dir)
        
        embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        vectorstore = Chroma(persist_directory=chroma_dir, embedding_function=embeddings)
    else:
        # Import parse_document here to avoid circular dependencies if any, though it was at top before
        from .parser import parse_document
        
        print("[1/5] Parsing with IBM Docling...")
        logger.info("Phase 1: Parsing document '%s'", pdf_path)
    
        pages = _retry_with_backoff(
            lambda: parse_document(pdf_path, max_pages=max_pages),
            max_retries=3,
            base_delay=2.0,
        )
        print(f"       Parsed {len(pages)} pages.")
    
        print("[2/5] Chunking and embedding...")
        logger.info("Phase 2: Chunking and embedding into ChromaDB")
    
        vectorstore = _retry_with_backoff(
            lambda: build_vectorstore(pages, persist_dir=chroma_dir),
            max_retries=3,
            base_delay=2.0,
        )

    # ── Step 3: Build retriever ───────────────────────────────────────────
    print("[3/5] Building two-stage retriever...")
    logger.info("Phase 3: Building two-stage retriever")

    retriever = build_retriever(vectorstore)

    # ── Step 4: Retrieve and extract ──────────────────────────────────────
    print("[4/5] Retrieving and extracting with Gemini 3.1 Flash-Lite...")
    logger.info("Phase 4: Retrieval + LLM extraction")

    # Retrieve relevant chunks
    chunks = _retry_with_backoff(
        lambda: retriever.invoke(_EXTRACTION_QUERY),
        max_retries=3,
        base_delay=2.0,
    )
    logger.info("Retrieved %d chunks after reranking.", len(chunks))
    print(f"       Retrieved {len(chunks)} chunks after reranking.")

    # Join chunks into context string
    context = "\n\n---\n\n".join([c.page_content for c in chunks])

    # Build extraction chain
    chain = build_extraction_chain()

    # Extract with auto-retry on Pydantic ValidationError
    result: Optional[ExtractionResult] = None
    for attempt in range(1, max_llm_retries + 1):
        try:
            result = chain.invoke({"context": context})
            if result is not None:
                logger.info(
                    "LLM extraction succeeded on attempt %d. "
                    "Extracted %d companies/periods.",
                    attempt,
                    len(result.companies),
                )
                break
        except ValidationError as e:
            logger.warning(
                "LLM extraction attempt %d/%d: Pydantic validation failed: %s",
                attempt,
                max_llm_retries,
                str(e),
            )
            if attempt == max_llm_retries:
                logger.error(
                    "Extraction failed after %d retries. "
                    "Falling back to manual review.",
                    max_llm_retries,
                )
                print(
                    f"  ⚠  Extraction failed after {max_llm_retries} retries. "
                    "See logs for details."
                )
                return None
        except Exception as e:
            logger.error("Unexpected error during extraction: %s", str(e))
            if attempt == max_llm_retries:
                print(f"  ⚠  Extraction failed: {e}")
                return None
            delay = 2.0 * (2 ** (attempt - 1))
            logger.info("Retrying in %.1fs...", delay)
            time.sleep(delay)

    if result is None:
        print("  ⚠  No extraction result obtained.")
        return None

    # ── Step 5: Validate and export ───────────────────────────────────────
    print("[5/5] Validating and exporting JSON...")
    logger.info("Phase 5: Validation and export")

    output = [c.model_dump() for c in result.companies]

    # Count null fields for quality assessment
    total_fields = 0
    null_fields = 0
    for company in output:
        for section_key in ("income_statement", "balance_sheet", "cash_flow"):
            section = company.get(section_key, {})
            if isinstance(section, dict):
                for value in section.values():
                    total_fields += 1
                    if value is None:
                        null_fields += 1

    null_pct = (null_fields / total_fields * 100) if total_fields > 0 else 0

    # Write output JSON
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"       Extracted data for {len(output)} companies/periods.")
    print(f"       Null fields: {null_fields}/{total_fields} ({null_pct:.1f}%)")
    print(f"       Output saved to: {output_file}")

    if null_pct > 50:
        logger.warning(
            "High null rate (%.1f%%). Consider increasing rerank_top_n "
            "or reviewing the source document quality.",
            null_pct,
        )
        print(
            f"  ⚠  High null rate ({null_pct:.1f}%). "
            "Some data may be missing from the source document."
        )

    logger.info(
        "Pipeline complete. %d companies, %d/%d fields populated (%.1f%% null).",
        len(output),
        total_fields - null_fields,
        total_fields,
        null_pct,
    )

    return output
