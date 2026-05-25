"""
Financial RAG Pipeline — CLI Entry Point.

Usage:
    python main.py <pdf_path> [--output <path>] [--chroma-dir <dir>] [--verbose]

Example:
    python main.py annual_report_2024.pdf --output results.json --verbose
"""

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    """Parse CLI arguments and run the Financial RAG Pipeline."""

    parser = argparse.ArgumentParser(
        prog="financial-rag-pipeline",
        description=(
            "Extract structured financial data from PDF annual reports "
            "using Azure Document Intelligence, ChromaDB, CohereRerank, "
            "and Gemini 1.5 Pro."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python main.py annual_report_2024.pdf
  python main.py report.pdf --output results.json
  python main.py report.pdf --chroma-dir ./my_vectors --verbose
        """,
    )

    parser.add_argument(
        "pdf_path",
        type=str,
        help="Path to the PDF file to process.",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="financials_output.json",
        help="Output JSON file path (default: financials_output.json).",
    )
    parser.add_argument(
        "--chroma-dir",
        type=str,
        default="./chroma_db",
        help="ChromaDB persistence directory (default: ./chroma_db).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max LLM extraction retries on validation errors (default: 3).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Maximum number of pages to process (useful for testing).",
    )
    parser.add_argument(
        "--skip-parse",
        action="store_true",
        help="Skip document parsing and chunking, loading directly from existing ChromaDB.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging.",
    )

    args = parser.parse_args()

    # ── Configure logging ─────────────────────────────────────────────────
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("financial_rag_pipeline")

    # ── Validate input ────────────────────────────────────────────────────
    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        logger.error("File not found: %s", pdf_path)
        print(f"Error: File not found: {pdf_path}")
        return 1

    if not pdf_path.suffix.lower() == ".pdf":
        logger.warning("File does not have .pdf extension: %s", pdf_path)
        print(f"Warning: File does not have .pdf extension: {pdf_path}")

    # ── Load environment variables ────────────────────────────────────────
    load_dotenv()
    logger.info("Environment variables loaded from .env")

    # ── Import and run pipeline (after dotenv is loaded) ──────────────────
    from src.pipeline import run_pipeline  # noqa: E402

    print()
    print("=" * 60)
    print("  FINANCIAL RAG PIPELINE")
    print("=" * 60)
    print(f"  Input:    {pdf_path}")
    print(f"  Output:   {args.output}")
    print(f"  ChromaDB: {args.chroma_dir}")
    print("=" * 60)
    print()

    result = run_pipeline(
        pdf_path=str(pdf_path),
        output_path=args.output,
        chroma_dir=args.chroma_dir,
        max_llm_retries=args.max_retries,
        max_pages=args.max_pages,
        skip_parse=args.skip_parse,
    )

    if result is None:
        print("\n❌ Pipeline completed with errors. See logs above.")
        return 1

    print(f"\n✅ Pipeline completed successfully. {len(result)} companies extracted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
