"""
Phase 1 — Document Parsing via IBM Docling.

Uses Docling's DocumentConverter to parse PDFs locally into Markdown output,
preserving text, tables, figures, and document structure.
"""

import logging
import re
from pathlib import Path

from docling.document_converter import DocumentConverter

logger = logging.getLogger(__name__)


def parse_document(pdf_path: str, max_pages: int | None = None) -> list[dict]:
    """
    Load a PDF via IBM Docling DocumentConverter.

    Returns a list of per-page metadata dicts with:
      - page_no:      page number
      - text:         full Markdown text (headings, paragraphs, tables)
      - tables:       list of Markdown table blocks
      - charts:       list of figure/chart captions
      - company_name: regex-detected company name

    Args:
        pdf_path: Path to the PDF file to parse.

    Returns:
        List of page dictionaries.
    """
    import pypdf
    import gc

    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        raise FileNotFoundError(f"File not found: {pdf_path}")

    logger.info("Loading document with IBM Docling: %s", pdf_path)

    # Get total pages first to enable batching
    reader = pypdf.PdfReader(str(pdf_file))
    num_pages = len(reader.pages)
    if max_pages:
        num_pages = min(num_pages, max_pages)
        logger.info("Limiting parsing to first %d pages for testing.", num_pages)
    else:
        logger.info("Document has %d pages. Processing in batches to conserve memory.", num_pages)

    converter = DocumentConverter()
    pages_data = []

    batch_size = 10
    for batch_start in range(1, num_pages + 1, batch_size):
        batch_end = min(batch_start + batch_size - 1, num_pages)
        logger.info("Processing pages %d to %d...", batch_start, batch_end)
        
        try:
            # page_range expects a tuple (start_page, end_page) inclusive
            result = converter.convert(source=str(pdf_file), page_range=(batch_start, batch_end))
            
            # The result.document.pages dictionary contains the processed pages
            for i in range(batch_start, batch_end + 1):
                try:
                    # Some Docling versions index from 1, others might skip empty pages
                    md_text = result.document.export_to_markdown(page_no=i)
                except Exception as e:
                    logger.warning("Failed to export page %d to markdown: %s", i, e)
                    md_text = ""

                pages_data.append(
                    {
                        "page_no": i,
                        "text": md_text,
                        "tables": extract_md_tables(md_text),
                        "charts": extract_figure_captions(md_text),
                        "company_name": detect_company(md_text),
                    }
                )
        except Exception as e:
            logger.error("Failed to process batch %d-%d: %s", batch_start, batch_end, e)
            
        # Force garbage collection after each batch
        del result
        gc.collect()

    logger.info(
        "Extracted data for %d pages. Companies detected: %s",
        len(pages_data),
        {p["company_name"] for p in pages_data} - {"Unknown"},
    )
    return pages_data


def extract_md_tables(markdown: str) -> list[str]:
    """
    Extract Markdown table blocks (| col | col | rows) from text.

    Args:
        markdown: Raw Markdown text from a single page.

    Returns:
        List of Markdown table strings.
    """
    table_pattern = r"((?:\|.*\|\n)+)"
    return re.findall(table_pattern, markdown)


def extract_figure_captions(markdown: str) -> list[str]:
    """
    Extract figure/chart captions from Markdown output.

    Args:
        markdown: Raw Markdown text from a single page.

    Returns:
        List of caption strings.
    """
    caption_pattern = r"(?:Figure|Chart|Exhibit|Graph)\s*\d*[:\.]?\s*.+"
    return re.findall(caption_pattern, markdown, re.IGNORECASE)


def detect_company(text: str) -> str:
    """
    Basic company name extraction via regex.

    Args:
        text: Raw text to search for company names.

    Returns:
        Detected company name, or 'Unknown'.
    """
    match = re.search(
        r"([A-Z][a-zA-Z\s]+(?:Ltd|Limited|Inc|Corp|Corporation|PLC)\.?)", text
    )
    return match.group(1).strip() if match else "Unknown"
