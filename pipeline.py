import os
import fitz  # PyMuPDF
import pdfplumber
import cv2
import numpy as np
import json
import re
from PIL import Image
import io
from datetime import datetime
import difflib
import google.generativeai as genai
from dotenv import load_dotenv
import time

try:
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
    from azure.core.credentials import AzureKeyCredential
    AZURE_DI_AVAILABLE = True
except ImportError:
    AZURE_DI_AVAILABLE = False

# Load environment variables from .env file
load_dotenv()

# Import database helpers
import database

def call_llm_with_retry(fn, *args, max_retries=3, initial_delay=8, **kwargs):
    """
    Retries LLM function calls with exponential backoff if a 429 rate limit 
    or quota exceeded error is encountered.
    """
    delay = initial_delay
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "quota" in err_str or "limit" in err_str or "resource_exhausted" in err_str:
                print(f"    [Rate Limit] Quota exceeded. Backing off for {delay}s (Attempt {attempt+1}/{max_retries})...")
                time.sleep(delay)
                delay *= 2.5
            else:
                # Other exceptions raise immediately
                raise e
    # Final attempt before letting it raise
    return fn(*args, **kwargs)

def classify_pdf(pdf_path):
    """
    Stage 1: PDF Classification
    Uses PyMuPDF to inspect text on the first few pages and classify the PDF as:
    - 'digital': has embedded extractable text (> 150 characters)
    - 'scanned': image-only PDF pages (no text, but has image elements or blank)
    """
    print(f"[Stage 1] Classifying PDF: {os.path.basename(pdf_path)}")
    try:
        doc = fitz.open(pdf_path)
        num_pages = len(doc)
        pages_to_check = min(5, num_pages)
        
        total_text = ""
        has_images = False
        
        for i in range(pages_to_check):
            page = doc[i]
            text = page.get_text()
            total_text += text
            
            if len(page.get_images()) > 0:
                has_images = True
                
        doc.close()
        
        text_length = len(total_text.strip())
        print(f"  Extracted text length from first {pages_to_check} pages: {text_length} chars")
        
        if text_length > 150:
            print("  Classification: DIGITAL")
            return "digital"
        else:
            print("  Classification: SCANNED / PHOTOGRAPH")
            return "scanned"
            
    except Exception as e:
        print(f"  Error during classification, defaulting to scanned: {e}")
        return "scanned"

def preprocess_photo_cv2(pil_img):
    """
    Preprocesses a photographed document page using OpenCV to remove shadows,
    normalize contrast, and binarize. Returns a PIL Image.
    """
    # Convert PIL Image to CV2 format (BGR)
    open_cv_image = np.array(pil_img)
    if len(open_cv_image.shape) == 2:
        # Grayscale
        gray = open_cv_image
    else:
        # Convert RGB to BGR
        img_bgr = open_cv_image[:, :, ::-1].copy()
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    
    # 1. Shadow removal / background normalization
    # Dilate to get background mask, then median blur
    dilated = cv2.dilate(gray, np.ones((7,7), np.uint8))
    bg = cv2.medianBlur(dilated, 21)
    
    # Compute difference
    diff = cv2.absdiff(gray, bg)
    normalized = cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX)
    
    # 2. Adaptive thresholding / Binarization
    binary = cv2.adaptiveThreshold(
        normalized, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
        cv2.THRESH_BINARY, 15, 8
    )
    
    # Convert back to PIL Image
    return Image.fromarray(binary)

def render_pdf_page_as_image(pdf_path, page_num, zoom=2.0):
    """Renders a PDF page to a PIL Image using PyMuPDF (no Poppler required!)."""
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    
    # Set matrix for high resolution zoom
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    
    img_data = pix.tobytes("png")
    doc.close()
    
    return Image.open(io.BytesIO(img_data))

def detect_statement_pages(pdf_path, pdf_type):
    """
    Stage 2: Filter and Detect Statement Pages
    Scans the PDF to find candidate pages for:
    - 'income_statement'
    - 'balance_sheet'
    - 'cash_flow'
    Returns a dictionary mapping statement types to lists of 0-indexed page numbers.
    """
    print("[Stage 2 Filter] Detecting Financial Statement Pages...")
    
    # Strong document title identifiers
    pl_identifiers = [
        "statement of profit and loss", "statement of profit & loss", 
        "profit and loss account", "income statement", "statement of operations",
        "consolidated statement of income", "statement of profit or loss"
    ]
    bs_identifiers = [
        "balance sheet", "statement of assets and liabilities", "statement of financial position"
    ]
    cf_identifiers = [
        "cash flow statement", "statement of cash flows", "cash flow"
    ]
    
    # Typical line items
    pl_items = ["employee benefits expense", "employee benefit expense", "finance costs", "depreciation", "tax expense"]
    bs_items = ["fixed assets", "property, plant and equipment", "share capital", "reserves", "trade payables", "inventories", "total assets"]
    cf_items = ["operating activities", "investing activities", "financing activities", "working capital changes"]
    
    candidates = {
        "income_statement": [],
        "balance_sheet": [],
        "cash_flow": []
    }
    
    try:
        doc = fitz.open(pdf_path)
        num_pages = len(doc)
        
        for i in range(num_pages):
            text = doc[i].get_text().lower()
            
            # P&L Check
            has_pl_title = any(title in text for title in pl_identifiers)
            has_pl_items = any(item in text for item in pl_items)
            if has_pl_title and (has_pl_items or "particulars" in text):
                if len(text) < 4500:
                    candidates["income_statement"].append(i)
                    continue
            
            # Balance Sheet Check
            has_bs_title = any(title in text for title in bs_identifiers)
            has_bs_items = any(item in text for item in bs_items)
            if has_bs_title and (has_bs_items or "particulars" in text):
                if len(text) < 4500:
                    candidates["balance_sheet"].append(i)
                    continue
                    
            # Cash Flow Check
            has_cf_title = any(title in text for title in cf_identifiers)
            has_cf_items = any(item in text for item in cf_items)
            if has_cf_title and (has_cf_items or "profit before tax" in text):
                if len(text) < 5000:
                    candidates["cash_flow"].append(i)
                    continue
                    
        doc.close()
        
        # If empty and short doc, guess sensible ranges
        for statement, pages in candidates.items():
            if not pages:
                if num_pages <= 15:
                    candidates[statement] = list(range(num_pages))
                else:
                    # Fallback to defaults
                    if statement == "balance_sheet":
                        candidates[statement] = [min(12, num_pages - 1)]
                    elif statement == "income_statement":
                        candidates[statement] = [min(13, num_pages - 1)]
                    elif statement == "cash_flow":
                        candidates[statement] = [min(15, num_pages - 1)]
                        
        print(f"  Detected P&L pages: {[p+1 for p in candidates['income_statement']]}")
        print(f"  Detected BS pages: {[p+1 for p in candidates['balance_sheet']]}")
        print(f"  Detected CF pages: {[p+1 for p in candidates['cash_flow']]}")
        return candidates
    except Exception as e:
        print(f"  Error detecting statement pages: {e}")
        return {"income_statement": [], "balance_sheet": [], "cash_flow": []}

def clean_numeric_value(val_str):
    """
    Converts a string representation of a financial value into a float/int.
    Handles commas, parentheses for negative numbers, spaces, and currency symbols.
    Returns None for empty, dash, or non-parseable strings (not 0.0).
    """
    if val_str is None:
        return None
    val_str = str(val_str).strip().lower()
    if not val_str or val_str in ("-", "nil", "null", "none", "n.a.", "na", "—", ""):
        return None
        
    # Check if negative denoted by parenthesis (e.g. (1,234.56)) or minus sign
    is_negative = False
    if (val_str.startswith("(") and val_str.endswith(")")) or val_str.startswith("-") or val_str.endswith("-"):
        is_negative = True
        
    # Remove all non-numeric characters except dots and minus
    cleaned = re.sub(r"[^\d.]", "", val_str)
    
    if not cleaned:
        return None
        
    try:
        value = float(cleaned)
        if is_negative:
            value = -value
        return value
    except ValueError:
        return None

def detect_page_unit(page_text):
    """
    Inspects page text to detect the reporting unit (e.g. lakhs, crores, millions).
    Returns a standard canonical string (e.g., 'INR_lakhs', 'INR_crores', 'INR_raw').
    """
    text = page_text.lower()
    if "lakh" in text:
        return "INR_lakhs"
    elif "crore" in text:
        return "INR_crores"
    elif "million" in text or "millions" in text:
        return "millions"
    elif "billion" in text or "billions" in text:
        return "billions"
    elif "thousand" in text or "thousands" in text:
        return "thousands"
    return "INR_raw"

def extract_tables_digital(pdf_path, page_num):
    """
    Extracts tables from a single page of a digital PDF using pdfplumber.
    Returns a list of structured tables: [[row1_cols], [row2_cols], ...]
    """
    tables = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[page_num]
            extracted = page.extract_tables()
            for table in extracted:
                # Clean rows (remove None, strip strings)
                cleaned_table = []
                for row in table:
                    cleaned_row = [str(cell).strip() if cell is not None else "" for cell in row]
                    # Filter out completely empty rows
                    if any(cleaned_row):
                        cleaned_table.append(cleaned_row)
                if cleaned_table:
                    tables.append(cleaned_table)
    except Exception as e:
        print(f"  Error extracting digital table on page {page_num+1}: {e}")
    return tables

def extract_tables_text_fallback(pdf_path, page_num):
    """
    Fallback text-based parser for XBRL/MCA-format PDFs where data appears as:
      Label line
      Value1 (current year)
      Value2 (previous year)
    instead of in proper PDF table structures.
    
    Returns rows as [{"label": str, "values": [str, str]}]
    """
    rows = []
    try:
        doc = fitz.open(pdf_path)
        raw_text = doc[page_num].get_text()
        doc.close()
        
        lines = raw_text.strip().split("\n")
        # Clean lines
        lines = [l.strip() for l in lines]
        
        # Skip patterns - these are section headers, not data rows
        skip_patterns = [
            "[abstract]", "[textblock]", "[text block]", 
            "textual information", "[see below]", 
            "whether company", "[axis]", "[member]",
            "[line items]"
        ]
        
        def is_numeric_line(text):
            """Check if a line is primarily a numeric value."""
            cleaned = text.strip()
            # Remove common prefixes like (A), (B), (C), (D) etc.
            cleaned = re.sub(r'^\([A-Z]\)\s*', '', cleaned)
            # Remove INR/shares prefix
            cleaned = re.sub(r'^\[INR/shares\]\s*', '', cleaned)
            # Check if what remains is a number (with commas, dots, minus, parentheses)
            cleaned = cleaned.replace(",", "").replace(" ", "")
            if not cleaned:
                return False
            # Match patterns: 123, -123, (123), 123.45, 0.50%, etc.
            return bool(re.match(r'^[\-\(]?\d+[\.\d]*\%?\)?$', cleaned))
        
        def is_skip_line(text):
            lower = text.lower()
            return any(p in lower for p in skip_patterns)
        
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # Skip empty, page numbers, headers, abstracts
            if not line or is_skip_line(line) or is_numeric_line(line):
                i += 1
                continue
                
            # Skip pure date lines like "01/04/2023"
            if re.match(r'^\d{2}/\d{2}/\d{4}$', line):
                i += 1
                continue
                
            # Skip "to" connectors between dates
            if line.lower() == "to":
                i += 1
                continue
            
            # This might be a label. Check if it's followed by label continuation
            # (multi-line labels) and then numeric values.
            label_parts = [line]
            j = i + 1
            
            # Collect continuation lines that are NOT numeric (multi-line labels)
            while j < len(lines) and lines[j] and not is_numeric_line(lines[j]) and not is_skip_line(lines[j]):
                # Stop if next line looks like a new distinct label (has its own values after)
                # Simple heuristic: if we've collected more than 3 continuation lines, stop
                if len(label_parts) >= 3:
                    break
                label_parts.append(lines[j])
                j += 1
            
            # Now collect numeric values that follow
            values = []
            while j < len(lines) and is_numeric_line(lines[j]):
                val = lines[j].strip()
                # Clean prefixes like (A), (B)
                val = re.sub(r'^\([A-Z]\)\s*', '', val)
                val = re.sub(r'^\[INR/shares\]\s*', '', val)
                values.append(val)
                j += 1
                
            full_label = " ".join(label_parts).strip()
            
            # Only include rows that have at least one numeric value
            if values and len(full_label) > 2:
                rows.append({"label": full_label, "values": values})
                
            # Advance pointer
            if j > i + 1:
                i = j
            else:
                i += 1
                
    except Exception as e:
        print(f"  Error in text fallback extraction on page {page_num+1}: {e}")
    
    return rows


def detect_fiscal_period_from_headers(header_texts):
    """
    Scans column header strings for a 4-digit year (e.g. '31 March 2024', '2023-24')
    and returns (current_period, previous_period) as ('FY2024', 'FY2023').
    Returns (None, None) if no year found.
    """
    years = []
    for h in header_texts:
        matches = re.findall(r'20\d{2}', str(h))
        for m in matches:
            yr = int(m)
            if yr not in years:
                years.append(yr)
    years.sort(reverse=True)
    if len(years) >= 2:
        return f"FY{years[0]}", f"FY{years[1]}"
    elif len(years) == 1:
        return f"FY{years[0]}", f"FY{years[0]-1}"
    return None, None


def extract_tables_azure_di(pdf_path, az_endpoint, az_key):
    """
    Stage 2 (Azure DI path): Calls the Azure Document Intelligence Layout model
    on the ENTIRE document in one API call.
    Returns a dict: {page_number (0-indexed): [{"label": str, "values": [str, ...]}]}
    Also returns detected column headers per page for fiscal year inference.
    """
    print("  [Azure DI] Submitting document to Layout model (whole-document, single call)...")
    page_tables = {}   # {page_0idx: [{label, values}]}
    page_headers = {}  # {page_0idx: [header strings]}
    
    if not AZURE_DI_AVAILABLE:
        print("  [Azure DI] azure-ai-documentintelligence package not installed. Falling back.")
        return page_tables, page_headers
    
    try:
        client = DocumentIntelligenceClient(
            endpoint=az_endpoint,
            credential=AzureKeyCredential(az_key)
        )
        
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        
        poller = client.begin_analyze_document(
            "prebuilt-layout",
            body=pdf_bytes,
            content_type="application/pdf"
        )
        result = poller.result()
        
        print(f"  [Azure DI] Received {len(result.tables) if result.tables else 0} tables from document.")
        
        for table in (result.tables or []):
            # Get page number (Azure DI is 1-indexed, we use 0-indexed)
            page_1idx = table.bounding_regions[0].page_number if table.bounding_regions else 1
            page_0idx = page_1idx - 1
            
            if page_0idx not in page_tables:
                page_tables[page_0idx] = []
                page_headers[page_0idx] = []
            
            # Build a row_index -> col_index -> content grid
            grid = {}
            for cell in (table.cells or []):
                r = cell.row_index
                c = cell.column_index
                if r not in grid:
                    grid[r] = {}
                grid[r][c] = cell.content.strip() if cell.content else ""
                
                # Collect header row content for fiscal year detection
                if hasattr(cell, 'kind') and cell.kind == 'columnHeader':
                    if cell.content and re.search(r'20\d{2}', cell.content):
                        page_headers[page_0idx].append(cell.content)
            
            if not grid:
                continue
            
            num_cols = max(max(row.keys()) for row in grid.values()) + 1
            
            # Identify header rows (row 0 usually) vs data rows
            # Header detection: scan first 3 rows for year patterns
            header_rows = set()
            for r_idx in sorted(grid.keys())[:3]:
                row_text = " ".join(grid[r_idx].values())
                if re.search(r'20\d{2}', row_text) or any(
                    kw in row_text.lower() for kw in ["particulars", "current year", "previous year", "march"]
                ):
                    header_rows.add(r_idx)
                    # Extract years for fiscal period detection
                    for cell_text in grid[r_idx].values():
                        if re.search(r'20\d{2}', cell_text):
                            page_headers[page_0idx].append(cell_text)
            
            # Build rows: first non-header column = label, rest = values
            for r_idx in sorted(grid.keys()):
                if r_idx in header_rows:
                    continue
                row = grid[r_idx]
                if not row:
                    continue
                
                label = row.get(0, "").strip()
                if not label or len(label) < 2:
                    continue
                
                # Skip purely numeric labels (note numbers, page numbers)
                if re.match(r'^[\d\s\.]+$', label):
                    continue
                
                values = [row.get(c, "") for c in range(1, num_cols)]
                # Keep only non-empty value columns
                values = [v for v in values if v.strip()]
                
                if values:  # Only add rows that have at least one value
                    page_tables[page_0idx].append({"label": label, "values": values})
        
        print(f"  [Azure DI] Parsed tables across {len(page_tables)} pages.")
        return page_tables, page_headers
        
    except Exception as e:
        print(f"  [Azure DI] Error: {e}")
        return {}, {}


def extract_tables_scanned(pdf_path, page_num, api_key, provider="gemini", model_name=None, preprocess=False):
    """
    Stage 2B & 2C: Scanned/Photo Extraction.
    Renders a PDF page as an image and uses the selected provider (Gemini or Nvidia)
    to extract the tables into a structured JSON format.
    """
    print(f"  [OCR/Vision] Processing page {page_num+1} using {provider.upper()}...")
    try:
        pil_img = render_pdf_page_as_image(pdf_path, page_num, zoom=2.0)
        
        if preprocess:
            print("    Applying OpenCV shadow normalization and binarization...")
            pil_img = preprocess_photo_cv2(pil_img)
            
        prompt = """
        You are an expert financial document intelligence system.
        Analyze this financial statement page image and extract all tabular structures.
        
        Format the extracted table into a JSON format:
        {
          "page_unit": "INR_lakhs" | "INR_crores" | "millions" | "billions" | "INR_raw",
          "tables": [
            {
              "headers": ["Particulars", "Note No.", "Current Year", "Previous Year"],
              "rows": [
                {
                  "label": "Revenue from operations",
                  "values": ["12,500.00", "42", "10,200.00"]
                }
              ]
            }
          ]
        }
        
        Rules:
        - "page_unit": Look closely at the top of the table/page. Look for labels like "Rs. in Lakhs", "in Crores", "in Millions", "in Thousands", "(Rs. in lakhs)". Output the appropriate standard string.
        - "label": The row description/particulars exactly as shown.
        - "values": List all numeric values in subsequent columns. Maintain formatting (commas, parentheses for negative numbers).
        - Keep the cell values completely true to the document. Do not calculate, estimate, or modify any numbers.
        """
        
        if provider.lower() == "gemini":
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name or "gemini-2.0-flash")
            response = call_llm_with_retry(model.generate_content, [prompt, pil_img])
            response_text = response.text.strip()
        else:
            from openai import OpenAI
            import base64
            
            client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key)
            model = model_name or "meta/llama-3.2-11b-vision-instruct"
            
            # Encode image to base64 jpeg
            buffered = io.BytesIO()
            pil_img.save(buffered, format="JPEG")
            img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                        ]
                    }
                ],
                temperature=0.1
            )
            response_text = response.choices[0].message.content.strip()
            
        # Clean JSON block output
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
            
        data = json.loads(response_text)
        return data
        
    except Exception as e:
        print(f"  Error extracting scanned table on page {page_num+1}: {e}")
        return {"page_unit": "INR_raw", "tables": []}

def detect_statement_type(page_text):
    """
    Inspects text to classify statement as Standalone or Consolidated.
    """
    text = page_text.lower()
    standalone_indicators = ["standalone", "stand-alone", "individual"]
    consolidated_indicators = ["consolidated", "consol", "group"]
    
    is_standalone = any(ind in text for ind in standalone_indicators)
    is_consolidated = any(ind in text for ind in consolidated_indicators)
    
    if is_consolidated and not is_standalone:
        return "Consolidated"
    elif is_standalone and not is_consolidated:
        return "Standalone"
    
    # Check frequency count
    standalone_count = sum(text.count(ind) for ind in standalone_indicators)
    consolidated_count = sum(text.count(ind) for ind in consolidated_indicators)
    
    if consolidated_count > standalone_count:
        return "Consolidated"
    elif standalone_count > consolidated_count:
        return "Standalone"
        
    return "Consolidated" # Default fallback

def get_previous_period(current_period):
    """Computes the previous period string from current (e.g. FY2024 -> FY2023)."""
    try:
        year = int(re.search(r"\d{4}", current_period).group(0))
        prefix = current_period.replace(str(year), "")
        return f"{prefix}{year-1}"
    except Exception:
        return "FY2023"

def run_alias_matching(row_label, val_cols, alias_dict, category_fields=None):
    """
    Stage 3: Alias fuzzy matching.
    Matches normalized row label against alias dictionary.
    Optionally filters by category_fields to prevent cross-statement matching errors.
    Returns matched canonical field name and a dictionary of values:
    {'current': val_cy, 'previous': val_py}
    """
    normalized = row_label.strip().lower()
    # Strip XBRL artifacts
    normalized = re.sub(r'\[.*?\]', '', normalized).strip()
    normalized = re.sub(r'\s+', ' ', normalized)
    
    matched_field = None
    best_ratio = 0.0
    
    # Filter aliases to consider
    aliases_to_check = alias_dict
    if category_fields is not None:
        aliases_to_check = {f: alias_dict[f] for f in category_fields if f in alias_dict}
        
    for field, aliases in aliases_to_check.items():
        if normalized in aliases:
            matched_field = field
            break
        
        for alias in aliases:
            if len(alias) > 12:
                if alias in normalized:
                    matched_field = field
                    break
                if normalized in alias:
                    if len(normalized) > 12 and len(normalized) / len(alias) > 0.5:
                        matched_field = field
                        break
        if matched_field:
            break
            
        for alias in aliases:
            ratio = difflib.SequenceMatcher(None, normalized, alias).ratio()
            if ratio > 0.82 and ratio > best_ratio:
                best_ratio = ratio
                matched_field = field
                
    if matched_field:
        # Extract Current Year and Previous Year
        val_cy = None
        val_py = None
        
        clean_vals = []
        for col in val_cols:
            cleaned = clean_numeric_value(col)
            clean_vals.append(cleaned)
            
        if len(clean_vals) >= 1:
            val_cy = clean_vals[0]
        if len(clean_vals) >= 2:
            val_py = clean_vals[1]
            
        return matched_field, {"current": val_cy, "previous": val_py}
        
    return None, None

def run_exception_handler(table_json, api_key, missing_fields, statement_type="income_statement", provider="gemini", model_name=None):
    """
    Stage 4: LLM Exception Mapping.
    Sends unmapped rows along with the entire table to the selected provider (Gemini or Nvidia)
    to map fields arithmetically.
    """
    print(f"  [Stage 4] Invoking {provider.upper()} Exception Handler for {statement_type}...")
    try:
        prompt = f"""
        You are given an extracted financial statement table context in JSON format.
        This table is from a '{statement_type}' section.
        Your job is to identify and map rows to these missing target financial fields:
        {json.dumps(missing_fields, indent=2)}
        
        Use the mathematical relationships between rows (additions, subtotals, deductions) to confirm your mapping.
        
        INPUT TABLE DATA:
        {json.dumps(table_json, indent=2)}
        
        OUTPUT JSON FORMAT:
        {{
            "field_name": {{ "label": "Matched row label", "current_value": 125000.0, "previous_value": 110000.0, "reason": "why this matches arithmetically" }} or null
        }}
        
        CRITICAL RULES:
        - Return both "current_value" (from the first value column) and "previous_value" (from the second value column, if present, else null).
        - Value MUST be the actual unscaled raw float from the table (e.g. 125000.0).
        - DO NOT calculate, estimate, or interpolate any values. Only return values directly present in the input.
        - If a field is not present or cannot be arithmetically confirmed, return null for that field.
        """
        
        if provider.lower() == "gemini":
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name or "gemini-2.0-flash")
            response = call_llm_with_retry(model.generate_content, prompt)
            response_text = response.text.strip()
        else:
            from openai import OpenAI
            client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key)
            model = model_name or "meta/llama-3.1-8b-instruct"
            
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )
            response_text = response.choices[0].message.content.strip()
            
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
            
        data = json.loads(response_text)
        return data
        
    except Exception as e:
        print(f"  Error in Exception Handler: {e}")
        return {}

def calculate_derived_fields(section, data):
    """
    Computes all mathematical derived fields based on the raw extracted fields.
    Updates the data dictionary in-place.
    """
    def val(field):
        return data.get(field, {}).get("value") if data.get(field) is not None else None

    def set_derived(field, value):
        if value is not None:
            data[field] = {
                "value": round(value, 2),
                "unit": "Derived",
                "confidence": "high",
                "source_pages": ["Computed"],
                "alias_matched": "Arithmetic Formula"
            }
        else:
            data[field] = None

    if section == "income_statement":
        # gross_profit = revenue - cost_of_materials - change_in_inventory
        rev = val("revenue")
        cogs = val("cost_of_materials")
        inv = val("change_in_inventory")
        
        gp = None
        if rev is not None:
            gp = rev - (cogs or 0.0) - (inv or 0.0)
            set_derived("gross_profit", gp)
            
            # gross_margin_pct
            if rev != 0:
                set_derived("gross_margin_pct", (gp / rev) * 100)
                
        # ebitda (bottom-up: revenue - cost_of_materials - change_in_inventory - employee_benefit_expenses - other_expenses)
        emp = val("employee_benefit_expenses")
        oth = val("other_expenses")
        
        ebitda_val = None
        if gp is not None and emp is not None and oth is not None:
            ebitda_val = gp - emp - oth
            set_derived("ebitda", ebitda_val)
        else:
            # fallback top-down: pat + tax + finance_cost + depreciation_amortization - other_income
            pat = val("pat")
            tax = val("tax")
            fin = val("finance_cost")
            dep = val("depreciation_amortization")
            inc = val("other_income")
            if pat is not None and tax is not None:
                ebitda_val = pat + tax + (fin or 0.0) + (dep or 0.0) - (inc or 0.0)
                set_derived("ebitda", ebitda_val)
                
        if ebitda_val is not None and rev:
            set_derived("ebitda_margin_pct", (ebitda_val / rev) * 100)
            
        # ebit = ebitda - depreciation_amortization
        dep = val("depreciation_amortization")
        ebit_val = None
        if ebitda_val is not None and dep is not None:
            ebit_val = ebitda_val - dep
            set_derived("ebit", ebit_val)
            if rev:
                set_derived("ebit_margin_pct", (ebit_val / rev) * 100)
        else:
            # fallback pbt + finance_cost - other_income
            pbt = val("pbt")
            fin = val("finance_cost")
            inc = val("other_income")
            if pbt is not None:
                ebit_val = pbt + (fin or 0.0) - (inc or 0.0)
                set_derived("ebit", ebit_val)
                if rev:
                    set_derived("ebit_margin_pct", (ebit_val / rev) * 100)

        # net_profit_margin_pct
        pat = val("pat")
        if pat is not None and rev:
            set_derived("net_profit_margin_pct", (pat / rev) * 100)

    elif section == "balance_sheet":
        # total_assets is the anchor
        total_assets = val("total_assets")
        
        # Calculate reserves & surplus or other assets
        fa = val("fixed_assets")
        cwip = val("cwip")
        inv_noncur = val("investments")
        rec = val("trade_receivables")
        invent = val("inventory")
        
        if total_assets is not None:
            # other_assets = total_assets - fixed_assets - cwip - investments - trade_receivables - inventory
            other = total_assets - (fa or 0.0) - (cwip or 0.0) - (inv_noncur or 0.0) - (rec or 0.0) - (invent or 0.0)
            set_derived("other_assets", other)
            
            # total_equity
            sc = val("share_capital")
            res = val("reserves_surplus")
            
            if sc is not None and res is not None:
                total_equity = sc + res
                total_liabilities = total_assets - total_equity
                
                # other_liabilities = total_liabilities - borrowings - trade_payables
                bor = val("borrowings")
                pay = val("trade_payables")
                other_liab = total_liabilities - (bor or 0.0) - (pay or 0.0)
                set_derived("other_liabilities", other_liab)
            else:
                data["other_liabilities"] = None
        else:
            data["other_assets"] = None
            data["other_liabilities"] = None

    elif section == "cash_flow":
        # working_capital_change
        rec_ch = val("change_in_receivables")
        inv_ch = val("change_in_inventories")
        pay_ch = val("change_in_payables")
        oth_ch = val("other_working_capital_changes")
        
        wc_ch = None
        if rec_ch is not None or inv_ch is not None or pay_ch is not None or oth_ch is not None:
            wc_ch = (rec_ch or 0.0) + (inv_ch or 0.0) + (pay_ch or 0.0) + (oth_ch or 0.0)
            set_derived("working_capital_change", wc_ch)
        else:
            data["working_capital_change"] = None

def run_arithmetic_validation(statements_dict):
    """
    Stage 5: Verification and Arithmetic cross-checks.
    Checks cross-table balances and math constraints.
    Returns (passed, list_of_errors).
    """
    errors = []
    
    def val(sec, field):
        return statements_dict.get(sec, {}).get(field, {}).get("value") if statements_dict.get(sec, {}).get(field) else None

    # --- Income Statement Checks ---
    pat = val("income_statement", "pat")
    pbt = val("income_statement", "pbt")
    tax = val("income_statement", "tax")
    if pat is not None and pbt is not None and tax is not None:
        if abs(pat - (pbt - tax)) > 5.0:  # allows ₹5 Lakhs rounding
            errors.append(f"P&L: PAT ({pat}) does not equal PBT minus tax ({pbt - tax})")
            
    ebit = val("income_statement", "ebit")
    inc = val("income_statement", "other_income")
    fin = val("income_statement", "finance_cost")
    if pbt is not None and ebit is not None:
        expected_pbt = ebit + (inc or 0.0) - (fin or 0.0)
        if abs(pbt - expected_pbt) > 5.0:
            errors.append(f"P&L: PBT ({pbt}) does not equal EBIT + other_income - finance_cost ({expected_pbt})")

    # --- Balance Sheet Checks ---
    # Total Assets equals Total Equity + Total Liabilities is mathematically guaranteed by residual, 
    # but verify asset and liability side matches printed total_assets.
    tot_assets = val("balance_sheet", "total_assets")
    sc = val("balance_sheet", "share_capital")
    res = val("balance_sheet", "reserves_surplus")
    bor = val("balance_sheet", "borrowings")
    pay = val("balance_sheet", "trade_payables")
    oth_liab = val("balance_sheet", "other_liabilities")
    
    if tot_assets is not None and sc is not None and res is not None:
        expected_eq_liab = sc + res + (bor or 0.0) + (pay or 0.0) + (oth_liab or 0.0)
        if abs(tot_assets - expected_eq_liab) > 5.0:
            errors.append(f"Balance Sheet: Total Assets ({tot_assets}) does not equal Equity & Liabilities ({expected_eq_liab})")

    # --- Cash Flow Checks ---
    cfo = val("cash_flow", "cash_flow_from_operations")
    cfi = val("cash_flow", "cash_flow_from_investing")
    cff = val("cash_flow", "cash_flow_from_financing")
    net = val("cash_flow", "net_cash_generated")
    if cfo is not None and cfi is not None and cff is not None and net is not None:
        if abs(net - (cfo + cfi + cff)) > 5.0:
            errors.append(f"Cash Flow: Net cash generated ({net}) does not equal CFO + CFI + CFF ({cfo + cfi + cff})")
            
    start = val("cash_flow", "cash_at_start")
    end = val("cash_flow", "cash_at_end")
    if start is not None and end is not None and net is not None:
        if abs(end - (start + net)) > 5.0:
            errors.append(f"Cash Flow: Closing Cash ({end}) does not equal Opening Cash + Net Generated ({start + net})")

    return len(errors) == 0, errors

def process_pdf(pdf_path, api_key, target_pages=None, preprocess_photos=False,
                provider="gemini", vision_model=None, text_model=None,
                az_endpoint=None, az_key=None):
    """
    Executes the entire 5-stage processing pipeline on the given PDF.
    Extracts P&L, Balance Sheet, and Cash Flow tables for Standalone and Consolidated.
    Saves outputs directly to SQLite database.
    """
    database.init_db()
    
    # Category mappings
    PL_FIELDS = [
        "revenue", "cost_of_materials", "change_in_inventory", "employee_benefit_expenses",
        "other_expenses", "other_income", "depreciation_amortization", "finance_cost", "pbt", "tax", "pat", "eps_basic", "eps_diluted"
    ]
    BS_FIELDS = [
        "fixed_assets", "cwip", "investments", "trade_receivables", "inventory", "share_capital", "face_value", "reserves_surplus", "borrowings", "trade_payables"
    ]
    CF_FIELDS = [
        "opbwc", "change_in_receivables", "change_in_inventories", "change_in_payables", "other_working_capital_changes",
        "cash_generated_from_ops", "tax_paid", "cash_flow_from_operations", "purchase_of_ppe", "sale_of_ppe", "cash_flow_from_investing",
        "borrowings_net", "dividend_paid", "equity_raised", "other_financing_activities", "cash_flow_from_financing", "net_cash_generated", "cash_at_start", "cash_at_end"
    ]
    
    # Stage 1: Classify PDF
    pdf_type = classify_pdf(pdf_path)
    
    # Stage 2: Filter and Detect Page Sections
    candidates = {}
    if target_pages is not None:
        # If user specified target pages, put them all in income_statement for extraction
        candidates = {
            "income_statement": target_pages,
            "balance_sheet": [],
            "cash_flow": []
        }
        print(f"  Using user-specified target pages: {[p+1 for p in target_pages]}")
    else:
        candidates = detect_statement_pages(pdf_path, pdf_type)
        
    alias_dict = database.get_aliases()
    
    # Data collector for all pages: [{"section": str, "page": int, "type": str, "data": dict}]
    extracted_fragments = []
    all_conflicts = []
    
    # Get metadata
    doc = fitz.open(pdf_path)
    company_name = doc.metadata.get("title") or os.path.basename(pdf_path).split("_")[0].upper()
    company_name = company_name.replace(".PDF", "").replace(".pdf", "")
    doc.close()

    # Azure DI is opt-in ONLY — only used if both args are explicitly passed in
    # (not pulled from env). This keeps the default path as pdfplumber + XBRL fallback.
    use_azure_di = bool(az_endpoint and az_key and AZURE_DI_AVAILABLE)

    azure_page_tables = {}
    azure_page_headers = {}
    if use_azure_di:
        print("[Azure DI] Running Layout analysis on full document...")
        azure_page_tables, azure_page_headers = extract_tables_azure_di(pdf_path, az_endpoint, az_key)
    else:
        print("[Extraction] Using pdfplumber + XBRL text fallback.")

    # --- Detect fiscal period from actual page text (not filename) ---
    # Scan all candidate financial statement pages for real date patterns
    fiscal_period = "FY2024"
    previous_period_override = None

    if use_azure_di and azure_page_headers:
        all_headers = [h for headers in azure_page_headers.values() for h in headers]
        fy_current, fy_previous = detect_fiscal_period_from_headers(all_headers)
        if fy_current:
            fiscal_period = fy_current
            previous_period_override = fy_previous
            print(f"[Fiscal Period] Detected from Azure DI headers: Current={fiscal_period}, Previous={previous_period_override}")
    else:
        # Scan the text of all detected financial statement pages for year patterns
        all_candidate_pages = [
            p for pages in candidates.values() for p in pages
        ]
        years_found = []
        try:
            doc = fitz.open(pdf_path)
            for pg in all_candidate_pages[:10]:  # check first 10 candidates max
                pg_text = doc[pg].get_text()
                # Look for patterns like "31 March 2024", "March 31, 2024", "31st March, 2024"
                date_matches = re.findall(
                    r'(?:31\s*(?:st|rd)?\s*march[,]?\s*|march\s+31[,]?\s*)(20\d{2})',
                    pg_text, re.IGNORECASE
                )
                # Also look for bare year in header context: "Year ended 31 March 2024"
                header_matches = re.findall(
                    r'(?:year ended|period ended|as at|as on)[^\n]{0,30}(20\d{2})',
                    pg_text, re.IGNORECASE
                )
                for m in date_matches + header_matches:
                    yr = int(m)
                    if yr not in years_found:
                        years_found.append(yr)
            doc.close()
        except Exception:
            pass

        years_found.sort(reverse=True)
        if len(years_found) >= 2:
            fiscal_period = f"FY{years_found[0]}"
            previous_period_override = f"FY{years_found[1]}"
            print(f"[Fiscal Period] Detected from page text: Current={fiscal_period}, Previous={previous_period_override}")
        elif len(years_found) == 1:
            fiscal_period = f"FY{years_found[0]}"
            previous_period_override = f"FY{years_found[0]-1}"
            print(f"[Fiscal Period] Detected from page text: Current={fiscal_period}, Previous={previous_period_override}")
        else:
            # Last resort: filename (known to be unreliable for XBRL filings)
            year_match = re.search(r"20\d{2}", os.path.basename(pdf_path))
            if year_match:
                fiscal_period = f"FY{year_match.group(0)}"
            print(f"[Fiscal Period] Fallback from filename: {fiscal_period} (may be inaccurate for XBRL filings)")
    
    # Process each section
    for section_name, pages in candidates.items():
        if not pages:
            continue
            
        category_fields = PL_FIELDS if section_name == "income_statement" else (BS_FIELDS if section_name == "balance_sheet" else CF_FIELDS)
        
        for page_num in pages:
            print(f"\n--- Extracting {section_name.upper()} from Page {page_num+1} ---")
            
            # Read text for type and unit detection
            doc = fitz.open(pdf_path)
            page_text = doc[page_num].get_text()
            doc.close()
            
            stmt_type = detect_statement_type(page_text)
            unit = detect_page_unit(page_text)
            
            table_rows = []

            if use_azure_di:
                # --- Azure DI path: look up pre-extracted tables for this page ---
                table_rows = azure_page_tables.get(page_num, [])
                if table_rows:
                    print(f"  [Azure DI] Using {len(table_rows)} pre-extracted rows for page {page_num+1}.")
                else:
                    print(f"  [Azure DI] No tables found for page {page_num+1} in Azure DI results.")
                
                # Also update unit from page headers if Azure DI found them
                page_header_text = " ".join(azure_page_headers.get(page_num, []))
                if page_header_text:
                    detected_unit = detect_page_unit(page_header_text)
                    if detected_unit != "INR_raw":
                        unit = detected_unit

            elif pdf_type == "digital":
                # --- pdfplumber path ---
                tables = extract_tables_digital(pdf_path, page_num)
                for table in tables:
                    for row in table:
                        if len(row) >= 2:
                            table_rows.append({"label": row[0], "values": row[1:]})

                # Try text fallback if needed
                if not table_rows:
                    print(f"  pdfplumber found no rows, trying text-based XBRL fallback...")
                    text_rows = extract_tables_text_fallback(pdf_path, page_num)
                    if text_rows:
                        table_rows = text_rows
                else:
                    # check if any alias matches
                    test_matches = 0
                    for row in table_rows[:20]:
                        res_f, _ = run_alias_matching(row["label"], row["values"], alias_dict, category_fields)
                        if res_f:
                            test_matches += 1
                    if test_matches == 0:
                        print(f"  pdfplumber rows did not alias-match, trying text fallback...")
                        text_rows = extract_tables_text_fallback(pdf_path, page_num)
                        if text_rows:
                            table_rows = text_rows
            else:
                # --- Scanned vision LLM path ---
                scanned_extracted = extract_tables_scanned(
                    pdf_path, page_num, api_key, provider=provider, model_name=vision_model, preprocess=preprocess_photos
                )
                unit = scanned_extracted.get("page_unit", unit)
                for table in scanned_extracted.get("tables", []):
                    for row in table.get("rows", []):
                        table_rows.append({"label": row.get("label"), "values": row.get("values", [])})
                        
            if not table_rows:
                print(f"  No tables found on page {page_num+1}.")
                continue
                
            print(f"  Extracted {len(table_rows)} rows. Performing contextual alias matching...")
            
            # Match rows using Alias Dict
            mapped_cy = {}
            mapped_py = {}
            unmapped_rows = []
            
            # Fallback to search for total assets directly on Balance Sheet pages
            direct_total_assets = None
            if section_name == "balance_sheet":
                for row in table_rows:
                    lbl = row["label"].lower()
                    if "total assets" == lbl or "total equity and liabilities" == lbl or "total equity & liabilities" == lbl:
                        clean_vals = [clean_numeric_value(c) for c in row["values"]]
                        direct_total_assets = {
                            "current": clean_vals[0] if len(clean_vals) >= 1 else None,
                            "previous": clean_vals[1] if len(clean_vals) >= 2 else None
                        }
                        break
            
            for row in table_rows:
                label = row["label"]
                values = row["values"]
                
                field, vals = run_alias_matching(label, values, alias_dict, category_fields)
                if field:
                    # Current Year
                    if field not in mapped_cy:
                        mapped_cy[field] = {
                            "value": vals["current"],
                            "unit": unit,
                            "confidence": "medium",
                            "source_pages": [page_num + 1],
                            "alias_matched": label
                        }
                    elif mapped_cy[field]["value"] != vals["current"]:
                        all_conflicts.append(f"Page {page_num+1}: Conflict for {field} CY")
                        
                    # Previous Year
                    if vals["previous"] is not None:
                        if field not in mapped_py:
                            mapped_py[field] = {
                                "value": vals["previous"],
                                "unit": unit,
                                "confidence": "medium",
                                "source_pages": [page_num + 1],
                                "alias_matched": label
                            }
                        elif mapped_py[field]["value"] != vals["previous"]:
                            all_conflicts.append(f"Page {page_num+1}: Conflict for {field} PY")
                else:
                    unmapped_rows.append(row)
                    
            if direct_total_assets:
                if "total_assets" not in mapped_cy and direct_total_assets["current"] is not None:
                    mapped_cy["total_assets"] = {
                        "value": direct_total_assets["current"],
                        "unit": unit,
                        "confidence": "high",
                        "source_pages": [page_num + 1],
                        "alias_matched": "Direct Total Assets row search"
                    }
                if "total_assets" not in mapped_py and direct_total_assets["previous"] is not None:
                    mapped_py["total_assets"] = {
                        "value": direct_total_assets["previous"],
                        "unit": unit,
                        "confidence": "high",
                        "source_pages": [page_num + 1],
                        "alias_matched": "Direct Total Assets row search"
                    }
            
            # LLM Exception mapping
            missing_fields = [f for f in category_fields if f not in mapped_cy]
            if missing_fields and api_key and unmapped_rows:
                # Check if we already found these fields from previous pages for the same statement type
                already_found = set()
                for frag in extracted_fragments:
                    if frag["section"] == section_name and frag["type"] == stmt_type:
                        already_found.update(frag["data"].keys())
                
                truly_missing = [f for f in missing_fields if f not in already_found]
                
                # Heuristics to skip unneeded LLM calls
                should_run_exception = True
                if not truly_missing:
                    print(f"  All missing fields already found on previous pages. Skipping Exception Handler.")
                    should_run_exception = False
                elif not mapped_cy:
                    # Page has ZERO alias matches — likely not the main statement page
                    print(f"  No alias matches on this page — skipping Exception Handler (not the main statement page).")
                    should_run_exception = False
                
                if should_run_exception:
                    context_table = {
                        "unit": unit,
                        "rows": table_rows
                    }
                    exception_mappings = run_exception_handler(context_table, api_key, truly_missing, section_name, provider, text_model)
                    
                    for field in truly_missing:
                        if field in exception_mappings and exception_mappings[field] is not None:
                            mapping = exception_mappings[field]
                            val_cy = clean_numeric_value(mapping.get("current_value"))
                            val_py = clean_numeric_value(mapping.get("previous_value"))
                            label = mapping.get("label", "LLM Inferred")
                            
                            if val_cy is not None and field not in mapped_cy:
                                mapped_cy[field] = {
                                    "value": val_cy,
                                    "unit": unit,
                                    "confidence": "medium",
                                    "source_pages": [page_num + 1],
                                    "alias_matched": f"LLM Mapped: {label}"
                                }
                            if val_py is not None and field not in mapped_py:
                                mapped_py[field] = {
                                    "value": val_py,
                                    "unit": unit,
                                    "confidence": "medium",
                                    "source_pages": [page_num + 1],
                                    "alias_matched": f"LLM Mapped: {label}"
                                }
                            
            if mapped_cy:
                extracted_fragments.append({
                    "section": section_name,
                    "page": page_num + 1,
                    "type": stmt_type,
                    "period_type": "current",
                    "data": mapped_cy
                })
            if mapped_py:
                extracted_fragments.append({
                    "section": section_name,
                    "page": page_num + 1,
                    "type": stmt_type,
                    "period_type": "previous",
                    "data": mapped_py
                })
                
    # --- Group & Synthesize ---
    if not extracted_fragments:
        print("\n[Pipeline Failed] No statement data could be extracted.")
        return None
        
    print("\n=== Merging & Resolving Fragments ===")
    
    # Group statements by (type, period)
    # where period is current_period or previous_period
    previous_period = previous_period_override or get_previous_period(fiscal_period)
    
    # Structured container: {(type, period): {income_statement: {}, balance_sheet: {}, cash_flow: {}}}
    synthesized = {}
    
    for frag in extracted_fragments:
        t = frag["type"]
        p = fiscal_period if frag["period_type"] == "current" else previous_period
        sec = frag["section"]
        
        key = (t, p)
        if key not in synthesized:
            synthesized[key] = {
                "income_statement": {},
                "balance_sheet": {},
                "cash_flow": {}
            }
            
        for field, item in frag["data"].items():
            current_item = synthesized[key][sec].get(field)
            if current_item is None:
                synthesized[key][sec][field] = item
            else:
                # Merge if values match, otherwise keep first
                if abs(current_item["value"] - item["value"]) > 0.01:
                    all_conflicts.append(f"Conflict for {field} in {t} {p}")
                else:
                    if frag["page"] not in current_item["source_pages"]:
                        current_item["source_pages"].append(frag["page"])
                        
    # Run derived calculations & validation for each statement group
    output_statements = []
    
    for (t, p), stmt_dict in synthesized.items():
        print(f"\nProcessing Statement Group: {t} | {p}")
        
        # Fill missing fields with None
        for f in PL_FIELDS:
            if f not in stmt_dict["income_statement"]:
                stmt_dict["income_statement"][f] = None
        for f in BS_FIELDS:
            if f not in stmt_dict["balance_sheet"]:
                stmt_dict["balance_sheet"][f] = None
        # Add total_assets as an alias/field
        if "total_assets" not in stmt_dict["balance_sheet"]:
            stmt_dict["balance_sheet"]["total_assets"] = None
        for f in CF_FIELDS:
            if f not in stmt_dict["cash_flow"]:
                stmt_dict["cash_flow"][f] = None
                
        # Calculate derived fields
        calculate_derived_fields("income_statement", stmt_dict["income_statement"])
        calculate_derived_fields("balance_sheet", stmt_dict["balance_sheet"])
        calculate_derived_fields("cash_flow", stmt_dict["cash_flow"])
        
        # Validate
        valid, errors = run_arithmetic_validation(stmt_dict)
        print(f"  Arithmetic Cross-Checks: {'PASSED' if valid else 'FAILED'}")
        for err in errors:
            print(f"    - [Error] {err}")
        # Determine status
        status = "processed"
        if not valid or all_conflicts:
            status = "flagged_for_review"
            
        # Compile missing/unresolved fields list
        unresolved = []
        for sec in ["income_statement", "balance_sheet", "cash_flow"]:
            for field, val_data in stmt_dict[sec].items():
                if val_data is None:
                    unresolved.append(f"{sec}.{field}")
                    
        # Save to database
        database.save_statement(
            company_name=company_name,
            fiscal_period=p,
            currency_unit=unit,
            statement_type=t,
            source_pdf=os.path.basename(pdf_path),
            income_statement=stmt_dict["income_statement"],
            balance_sheet=stmt_dict["balance_sheet"],
            cash_flow=stmt_dict["cash_flow"],
            arithmetic_check_passed=valid,
            conflicts=all_conflicts,
            unresolved_fields=unresolved,
            status=status
        )
        
        output_statements.append({
            "company_name": company_name,
            "period": p,
            "currency_unit": unit,
            "statement_type": t,
            "source_pdf": os.path.basename(pdf_path),
            "income_statement": stmt_dict["income_statement"],
            "balance_sheet": stmt_dict["balance_sheet"],
            "cash_flow": stmt_dict["cash_flow"],
            "arithmetic_check_passed": valid,
            "conflicts": all_conflicts,
            "unresolved_fields": unresolved,
            "status": status
        })
        
    return output_statements

