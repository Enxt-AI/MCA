# Financial RAG Pipeline

**End-to-end structured financial data extraction from multi-page PDF reports.**

```
IBM Docling → ChromaDB → CohereRerank → Gemini 1.5 Pro → Validated JSON
```

## Architecture

| Phase | Component | Purpose |
|-------|-----------|---------|
| 1 | IBM Docling | Parse PDFs → Markdown (text, tables, figures) |
| 2 | all-MiniLM-L6-v2 + ChromaDB | Semantic chunking & vector storage |
| 3 | Cosine Top-500 → CohereRerank v3 | Two-stage retrieval → Top 40–50 chunks |
| 4 | Gemini 1.5 Pro (LangChain) | Structured extraction with Pydantic validation |
| 5 | Validated JSON | 50+ financial fields per company/period |

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

Required API keys:
- **Google AI** — for Gemini 1.5 Pro ([Google AI Studio](https://aistudio.google.com))
- **Cohere** — for CohereRerank v3 ([Cohere Dashboard](https://dashboard.cohere.com))

### 3. Run the Pipeline

```bash
# Basic usage
python main.py path/to/annual_report.pdf

# Custom output path
python main.py path/to/annual_report.pdf --output results.json

# Custom ChromaDB directory
python main.py path/to/annual_report.pdf --chroma-dir ./my_chroma_db
```

## Output Format

The pipeline extracts 50+ financial fields per company/period into a validated JSON structure:

```json
[
  {
    "company_name": "Example Corp Ltd",
    "period": "FY2024",
    "currency_unit": "INR Crores",
    "income_statement": {
      "revenue": 15234.5,
      "ebitda": 3200.0,
      "pat": 1850.0,
      "eps_basic": 45.2,
      ...
    },
    "balance_sheet": {
      "fixed_assets": 8500.0,
      "borrowings": 2100.0,
      ...
    },
    "cash_flow": {
      "cash_flow_from_operations": 2800.0,
      "cash_at_end": 1200.0,
      ...
    }
  }
]
```

## Project Structure

```
Financial Document Analyzer/
├── main.py                 # CLI entry point
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template
├── README.md               # This file
└── src/
    ├── __init__.py
    ├── models.py           # Pydantic schemas (50+ fields)
    ├── parser.py           # IBM Docling parsing
    ├── embeddings.py       # Chunking & ChromaDB storage
    ├── retriever.py        # Two-stage retrieval + CohereRerank
    ├── extractor.py        # Gemini 1.5 Pro extraction chain
    └── pipeline.py         # Full orchestration + error handling
```

## Cost Estimation (per 500-page document)

| Service | Usage | Approx. Cost |
|---------|-------|-------------|
| IBM Docling | 500 pages (Local CPU) | $0.00 |
| all-MiniLM-L6-v2 | ~500K tokens (Local CPU) | $0.00 |
| CohereRerank v3 | 500 docs reranked | ~$0.001 |
| Gemini 1.5 Pro | ~100K tokens | ~$0.35 |
| **Total** | — | **~$0.35** |

## Known Limitations

| Limitation | Mitigation |
|------------|-----------|
| Docling misses scanned text | Pre-process with external OCR |
| Chunk boundary splits table row | Increase overlap to 200 tokens for table-heavy docs |
| Gemini hallucination on sparse context | Pydantic validation + null count thresholds |
| Multiple companies in one chunk | Post-process: validate company_name per row |
| Units vary across documents | Enforce `currency_unit` field; normalise downstream |
