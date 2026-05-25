import streamlit as st
import os
import json
from datetime import datetime
import pandas as pd
import fitz  # PyMuPDF
from PIL import Image
import io
from dotenv import load_dotenv

load_dotenv()
import database
import pipeline

# Initialize database tables on startup
database.init_db()


st.set_page_config(
    page_title="Financial Statement Extraction Engine",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Custom premium CSS
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Outfit', sans-serif;
    }
    
    .glowing-title {
        font-size: 2.5rem;
        font-weight: 700;
        background: linear-gradient(135deg, #6c5ce7, #a29bfe, #00cec9);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        text-shadow: 0px 4px 15px rgba(108, 92, 231, 0.1);
        margin-bottom: 0.2rem;
    }
    
    .subtitle {
        font-size: 1.1rem;
        color: #b2bec3;
        margin-bottom: 2rem;
    }
    
    .metric-card {
        background-color: #1e1e24;
        border-radius: 12px;
        border-left: 5px solid #6c5ce7;
        padding: 1.2rem;
        margin-bottom: 1rem;
        transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    
    .metric-card:hover {
        transform: translateY(-4px);
        box-shadow: 0 10px 20px rgba(108, 92, 231, 0.15);
        border-left-color: #00cec9;
    }
    
    .metric-title {
        font-size: 0.9rem;
        color: #95a5a6;
        text-transform: uppercase;
        font-weight: 600;
        margin-bottom: 0.4rem;
    }
    
    .metric-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #ffffff;
        margin-bottom: 0.2rem;
    }
    
    .metric-meta {
        font-size: 0.8rem;
        color: #bdc3c7;
    }
    
    .badge-high { background-color: #27ae60; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }
    .badge-medium { background-color: #f39c12; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }
    .badge-low { background-color: #c0392b; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }
    .badge-passed { background-color: rgba(39, 174, 96, 0.2); color: #2ecc71; border: 1px solid #2ecc71; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600; }
    .badge-failed { background-color: rgba(192, 57, 43, 0.2); color: #e74c3c; border: 1px solid #e74c3c; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# Sidebar
with st.sidebar:
    st.image("https://img.icons8.com/color/120/000000/fine-print.png", width=70)
    st.markdown("### Engine Settings")
    provider = st.selectbox("API Model Provider", ["Gemini", "Nvidia"], index=0)
    
    if provider == "Gemini":
        env_api_key = os.environ.get("GEMINI_API_KEY", "")
        api_key_label = "Gemini API Key"
        env_help = "Required for scanned/photo OCR & exception label resolution."
    else:
        env_api_key = os.environ.get("NVIDIA_API_KEY", "")
        api_key_label = "Nvidia API Key"
        env_help = "Spare Nvidia API catalog or NIM endpoints key."
        
    api_key = st.text_input(api_key_label, value=env_api_key, type="password", help=env_help)
    
    # Azure DI is hidden as per user request
    az_endpoint = None
    az_key = None


    with st.expander("Advanced Model Options"):
        if provider == "Gemini":
            vision_model = st.text_input("Vision Model", value="gemini-2.0-flash")
            text_model = st.text_input("Text Model", value="gemini-2.0-flash")
        else:
            vision_model = st.text_input("Vision Model", value="meta/llama-3.2-11b-vision-instruct")
            text_model = st.text_input("Text Model", value="meta/llama-3.1-8b-instruct")
            
    st.markdown("---")
    st.markdown("#### Database Summary")
    statements = database.get_statements()
    total_records = len(statements)
    flagged_records = len([s for s in statements if s["status"] == "flagged_for_review"])
    reviewed_records = len([s for s in statements if s["status"] == "reviewed"])
    processed_records = total_records - flagged_records - reviewed_records
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total Statements", total_records)
        st.metric("Flagged", flagged_records)
    with col2:
        st.metric("Processed", processed_records)
        st.metric("Reviewed", reviewed_records)
        
    st.markdown("---")
    st.caption("Powered by Gemini 2.0 & PyMuPDF")

# Header
st.markdown('<div class="glowing-title">Comprehensive Financial Extraction Engine</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Multi-format ingestion pipeline supporting Income Statement, Balance Sheet, and Cash Flow extractions</div>', unsafe_allow_html=True)

# Dynamic lists of fields
PL_FIELDS = [
    ("revenue", "Revenue"),
    ("cost_of_materials", "Cost of Materials"),
    ("change_in_inventory", "Change in Inventory"),
    ("gross_profit", "Gross Profit (Derived)"),
    ("gross_margin_pct", "Gross Margin % (Derived)"),
    ("employee_benefit_expenses", "Employee Benefit Expenses"),
    ("other_expenses", "Other Expenses"),
    ("ebitda", "EBITDA (Derived)"),
    ("ebitda_margin_pct", "EBITDA Margin % (Derived)"),
    ("depreciation_amortization", "Depreciation & Amortization"),
    ("ebit", "EBIT (Derived)"),
    ("ebit_margin_pct", "EBIT Margin % (Derived)"),
    ("other_income", "Other Income"),
    ("finance_cost", "Finance Cost"),
    ("pbt", "Profit Before Tax (PBT)"),
    ("tax", "Tax Expense"),
    ("pat", "Profit After Tax (PAT)"),
    ("net_profit_margin_pct", "Net Profit Margin % (Derived)"),
    ("eps_basic", "Basic EPS"),
    ("eps_diluted", "Diluted EPS")
]

BS_FIELDS = [
    ("fixed_assets", "Fixed Assets"),
    ("cwip", "Capital WIP"),
    ("investments", "Investments"),
    ("trade_receivables", "Trade Receivables"),
    ("inventory", "Inventory"),
    ("other_assets", "Other Assets (Derived)"),
    ("total_assets", "Total Assets"),
    ("share_capital", "Share Capital"),
    ("face_value", "Face Value"),
    ("reserves_surplus", "Reserves & Surplus"),
    ("borrowings", "Borrowings"),
    ("trade_payables", "Trade Payables"),
    ("other_liabilities", "Other Liabilities (Derived)")
]

CF_FIELDS = [
    ("opbwc", "Operating Profit Before WC Changes"),
    ("change_in_receivables", "Change in Receivables"),
    ("change_in_inventories", "Change in Inventories"),
    ("change_in_payables", "Change in Payables"),
    ("other_working_capital_changes", "Other WC Changes"),
    ("working_capital_change", "Working Capital Change (Derived)"),
    ("cash_generated_from_ops", "Cash Generated from Ops"),
    ("tax_paid", "Tax Paid"),
    ("cash_flow_from_operations", "Cash Flow from Operations (CFO)"),
    ("purchase_of_ppe", "Purchase of PPE (CapEx)"),
    ("sale_of_ppe", "Sale of PPE"),
    ("cash_flow_from_investing", "Cash Flow from Investing (CFI)"),
    ("borrowings_net", "Net Borrowings Change"),
    ("dividend_paid", "Dividend Paid"),
    ("equity_raised", "Equity Raised"),
    ("other_financing_activities", "Other Financing Activities"),
    ("cash_flow_from_financing", "Cash Flow from Financing (CFF)"),
    ("net_cash_generated", "Net Cash Generated"),
    ("cash_at_start", "Cash at Start"),
    ("cash_at_end", "Cash at End")
]
ALL_FIELDS_MAP = {f[0]: f[1] for f in PL_FIELDS + BS_FIELDS + CF_FIELDS}

def to_canonical_json(record):
    """
    Transforms a DB record into the exact clean, structured canonical JSON format
    desired by the user (no metadata wrappers, just clean keys and float values).
    """
    def clean_fields(section_dict):
        if not section_dict:
            return {}
        result = {}
        for key, item in section_dict.items():
            if item is None:
                result[key] = None
            elif isinstance(item, dict) and "value" in item:
                result[key] = item["value"]
            else:
                result[key] = item
        return result

    return {
        "company_name": record.get("company_name"),
        "period": record.get("fiscal_period") or record.get("period"),
        "currency_unit": record.get("currency_unit"),
        "statement_type": record.get("statement_type"),
        "income_statement": clean_fields(record.get("income_statement")),
        "balance_sheet": clean_fields(record.get("balance_sheet")),
        "cash_flow": clean_fields(record.get("cash_flow"))
    }

tab_ingest, tab_explore, tab_review, tab_aliases = st.tabs([
    "📂 PDF Ingestion & Pipeline",
    "📊 Extracted Data Explorer",
    "🔎 Human-in-the-Loop Review",
    "📖 Alias Dictionary Manager"
])

# --- TAB 1: PDF INGESTION & PIPELINE ---
with tab_ingest:
    st.subheader("Process a Financial Document")
    col_input, col_config = st.columns([2, 1])
    
    with col_input:
        uploaded_file = st.file_uploader("Upload Annual Report or Financial Statement PDF", type=["pdf"])
    with col_config:
        st.markdown("#### Pipeline Controls")
        page_mode = st.radio("Page Scanning Mode", ["Auto-Detect Statement Pages", "Custom Page Range/List"])
        
        custom_pages_val = ""
        if page_mode == "Custom Page Range/List":
            custom_pages_val = st.text_input("Enter Pages (e.g. 42-45, or 12, 15, 18)", value="42-45")
            
        preprocess_photos = st.checkbox("Enable CV2 Photo Normalization", value=False)
        
    if uploaded_file is not None:
        if st.button("🚀 Run Extraction Pipeline", use_container_width=True):
            if not api_key:
                st.warning("⚠️ Please provide an API Key in the sidebar for scanned vision extractions.")
                
            pdf_path = os.path.join(UPLOAD_DIR, uploaded_file.name)
            with open(pdf_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
                
            target_pages = None
            if page_mode == "Custom Page Range/List" and custom_pages_val:
                try:
                    pages_list = []
                    parts = custom_pages_val.split(",")
                    for part in parts:
                        part = part.strip()
                        if "-" in part:
                            start, end = map(int, part.split("-"))
                            pages_list.extend(range(start - 1, end))
                        else:
                            pages_list.append(int(part) - 1)
                    target_pages = sorted(list(set(pages_list)))
                except Exception:
                    st.error("Invalid page range format.")
                    st.stop()
                    
            st.info("Pipeline started. Processing step-by-step logs:")
            log_container = st.empty()
            
            class StreamlitLogger:
                def __init__(self):
                    self.logs = []
                def log(self, msg):
                    self.logs.append(msg)
                    log_container.code("\n".join(self.logs))
                    
            logger = StreamlitLogger()
            
            import builtins
            original_print = builtins.print
            def custom_print(*args, **kwargs):
                msg = " ".join(map(str, args))
                original_print(*args, **kwargs)
                logger.log(msg)
            builtins.print = custom_print
            
            with st.spinner("Processing PDF stages..."):
                try:
                    result_list = pipeline.process_pdf(
                        pdf_path=pdf_path,
                        api_key=api_key,
                        target_pages=target_pages,
                        preprocess_photos=preprocess_photos,
                        provider=provider.lower(),
                        vision_model=vision_model,
                        text_model=text_model,
                        az_endpoint=az_endpoint or None,
                        az_key=az_key or None
                    )
                except Exception as e:
                    result_list = None
                    st.error(f"Pipeline crashed during execution: {e}")
                finally:
                    builtins.print = original_print
                    
            if result_list:
                st.success(f"🎉 Successfully extracted {len(result_list)} statements!")
                
                # Summary tabs
                summary_tabs = st.tabs([f"📄 {res['statement_type']} - {res['period']}" for res in result_list])
                for idx, res in enumerate(result_list):
                    with summary_tabs[idx]:
                        # Render quick cards
                        c1, c2, c3, c4 = st.columns(4)
                        
                        def render_quick_card(col, label, field_data):
                            with col:
                                if field_data:
                                    st.markdown(f"""
                                    <div class="metric-card">
                                        <div class="metric-title">{label}</div>
                                        <div class="metric-value">{field_data['value']:,.2f}</div>
                                        <div class="metric-meta">Unit: {field_data['unit']}</div>
                                    </div>
                                    """, unsafe_allow_html=True)
                                else:
                                    st.markdown(f"""
                                    <div class="metric-card" style="border-left-color: #bdc3c7;">
                                        <div class="metric-title">{label}</div>
                                        <div class="metric-value" style="color: #7f8c8d;">N/A</div>
                                        <div class="metric-meta">Not found</div>
                                    </div>
                                    """, unsafe_allow_html=True)
                                    
                        render_quick_card(c1, "Revenue", res["income_statement"]["revenue"])
                        render_quick_card(c2, "EBITDA", res["income_statement"]["ebitda"])
                        render_quick_card(c3, "Total Assets", res["balance_sheet"]["total_assets"])
                        render_quick_card(c4, "CFO", res["cash_flow"]["cash_flow_from_operations"])
                        
                        chk_badge = "badge-passed" if res["arithmetic_check_passed"] else "badge-failed"
                        chk_text = "PASSED" if res["arithmetic_check_passed"] else "FAILED"
                        st.markdown(f"**Arithmetic Consistency**: <span class='{chk_badge}'>{chk_text}</span>", unsafe_allow_html=True)
                        
                        st.subheader("📥 Export Canonical Structured JSON")
                        canonical_json = to_canonical_json(res)
                        st.json(canonical_json)
                        
                        st.download_button(
                            label="📥 Download Canonical JSON",
                            data=json.dumps(canonical_json, indent=2),
                            file_name=f"{res['company_name']}_{res['period']}_{res['statement_type']}.json",
                            mime="application/json",
                            key=f"dl_ingest_{idx}"
                        )
                        
                        with st.expander("🔌 View Raw Database Record JSON"):
                            st.json(res)

# --- TAB 2: EXTRACTED DATA EXPLORER ---
with tab_explore:
    st.subheader("Browse Extracted Statements")
    statements = database.get_statements()
    
    if not statements:
        st.info("No statements processed yet. Upload a PDF to get started!")
    else:
        df_list = []
        for s in statements:
            df_list.append({
                "Company Name": s["company_name"],
                "Period": s["fiscal_period"],
                "Statement Type": s["statement_type"],
                "Currency": s["currency_unit"],
                "Revenue": f"{s['income_statement']['revenue']['value']:,.2f}" if s["income_statement"] and s["income_statement"].get("revenue") else "N/A",
                "EBITDA": f"{s['income_statement']['ebitda']['value']:,.2f}" if s["income_statement"] and s["income_statement"].get("ebitda") else "N/A",
                "Total Assets": f"{s['balance_sheet']['total_assets']['value']:,.2f}" if s["balance_sheet"] and s["balance_sheet"].get("total_assets") else "N/A",
                "CFO": f"{s['cash_flow']['cash_flow_from_operations']['value']:,.2f}" if s["cash_flow"] and s["cash_flow"].get("cash_flow_from_operations") else "N/A",
                "Arithmetic Check": "PASSED" if s["arithmetic_check_passed"] else "FAILED",
                "Status": s["status"].upper(),
                "Extracted At": s["extracted_at"],
                "id": s["id"]
            })
            
        df = pd.DataFrame(df_list)
        
        search_query = st.text_input("🔍 Search Company Name or Period")
        if search_query:
            df = df[df["Company Name"].str.contains(search_query, case=False) | df["Period"].str.contains(search_query, case=False)]
            
        st.dataframe(df.drop(columns=["id"]), use_container_width=True, hide_index=True)
        
        st.markdown("---")
        st.markdown("### Master-Detail Statement Viewer")
        
        selected_key = st.selectbox(
            "Select Company Statement for detailed review",
            options=[f"{s['company_name']} - {s['fiscal_period']} - {s['statement_type']} (ID: {s['id']})" for s in statements],
            index=0
        )
        
        if selected_key:
            record_id = int(selected_key.split("ID: ")[1].replace(")", ""))
            record = next(s for s in statements if s["id"] == record_id)
            
            tab_pl, tab_bs, tab_cf, tab_raw, tab_canonical = st.tabs([
                "📈 Income Statement",
                "🏛️ Balance Sheet",
                "💸 Cash Flow Statement",
                "🔌 Raw JSON",
                "📥 Canonical JSON Export"
            ])
            
            def make_table_df(fields_list, data_dict):
                rows_df = []
                for key, label in fields_list:
                    item = data_dict.get(key) if data_dict else None
                    if item:
                        rows_df.append({
                            "Financial Metric": label,
                            "Extracted Value": f"{item['value']:,.2f}",
                            "Unit": item["unit"],
                            "Source Pages": str(item["source_pages"]),
                            "Confidence": item["confidence"].upper(),
                            "Label Matched": item["alias_matched"]
                        })
                    else:
                        rows_df.append({
                            "Financial Metric": label,
                            "Extracted Value": "N/A",
                            "Unit": "-",
                            "Source Pages": "-",
                            "Confidence": "-",
                            "Label Matched": "-"
                        })
                return pd.DataFrame(rows_df)
                
            with tab_pl:
                st.subheader(f"Income Statement: {record['company_name']} ({record['fiscal_period']})")
                df_pl = make_table_df(PL_FIELDS, record["income_statement"])
                st.dataframe(df_pl, use_container_width=True, hide_index=True)
                
            with tab_bs:
                st.subheader(f"Balance Sheet: {record['company_name']} ({record['fiscal_period']})")
                df_bs = make_table_df(BS_FIELDS, record["balance_sheet"])
                st.dataframe(df_bs, use_container_width=True, hide_index=True)
                
            with tab_cf:
                st.subheader(f"Cash Flow Statement: {record['company_name']} ({record['fiscal_period']})")
                df_cf = make_table_df(CF_FIELDS, record["cash_flow"])
                st.dataframe(df_cf, use_container_width=True, hide_index=True)
                
            with tab_raw:
                st.subheader("Raw Database Record JSON")
                st.json(record)
                
            with tab_canonical:
                st.subheader("Canonical Structured JSON Export")
                canonical_export = to_canonical_json(record)
                st.json(canonical_export)
                
                st.download_button(
                    label="📥 Download Canonical JSON",
                    data=json.dumps(canonical_export, indent=2),
                    file_name=f"{record['company_name']}_{record['fiscal_period']}_{record['statement_type']}.json",
                    mime="application/json",
                    key=f"dl_explore_{record['id']}"
                )

# --- TAB 3: HUMAN-IN-THE-LOOP REVIEW QUEUE ---
with tab_review:
    st.subheader("Human-in-the-Loop Review Queue")
    review_statements = database.get_statements(status="flagged_for_review")
    
    if not review_statements:
        st.success("🎉 No records currently flagged for review! Everything is arithmetically consistent and resolved.")
    else:
        st.warning(f"⚠️ There are {len(review_statements)} records requiring human attention.")
        
        selected_review = st.selectbox(
            "Select Record to Review",
            options=[f"{s['company_name']} - {s['fiscal_period']} - {s['statement_type']} (ID: {s['id']})" for s in review_statements],
            index=0
        )
        
        if selected_review:
            record_id = int(selected_review.split("ID: ")[1].replace(")", ""))
            record = next(s for s in review_statements if s["id"] == record_id)
            
            col_img, col_form = st.columns([1, 1])
            
            with col_img:
                st.markdown("#### Source PDF Preview")
                pdf_file_path = os.path.join(UPLOAD_DIR, record["source_pdf"])
                if os.path.exists(pdf_file_path):
                    try:
                        zoom_level = st.slider("Zoom PDF Preview", 1.0, 3.0, 1.8, step=0.2)
                        preview_page = st.number_input("Page to View", min_value=1, max_value=200, value=1) - 1
                        pil_img = pipeline.render_pdf_page_as_image(pdf_file_path, preview_page, zoom=zoom_level)
                        st.image(pil_img, caption=f"Original Table Page: Page {preview_page+1}", use_column_width=True)
                    except Exception as e:
                        st.error(f"Could not load PDF page image: {e}")
                else:
                    st.info(f"Source PDF file '{record['source_pdf']}' was processed in a different session or deleted.")
                    
            with col_form:
                st.markdown(f"#### Review & Correct Form: {record['company_name']} ({record['fiscal_period']})")
                st.info("Directly correct values in the statement. Derived fields will recalculate and re-validate automatically.")
                
                # Interactive expanders for statement sections
                st.markdown("### 1. Income Statement")
                pl_vals = {}
                with st.expander("Expand P&L Fields"):
                    for key, label in PL_FIELDS:
                        # Skip derived fields as they will be calculated automatically
                        if "(Derived)" in label:
                            continue
                        item = record["income_statement"].get(key) if record["income_statement"] else None
                        default_val = float(item["value"]) if item and item["value"] is not None else 0.0
                        pl_vals[key] = st.number_input(label, value=default_val, format="%.2f", key=f"review_pl_{key}")
                        
                st.markdown("### 2. Balance Sheet")
                bs_vals = {}
                with st.expander("Expand Balance Sheet Fields"):
                    for key, label in BS_FIELDS:
                        if "(Derived)" in label:
                            continue
                        item = record["balance_sheet"].get(key) if record["balance_sheet"] else None
                        default_val = float(item["value"]) if item and item["value"] is not None else 0.0
                        bs_vals[key] = st.number_input(label, value=default_val, format="%.2f", key=f"review_bs_{key}")
                        
                st.markdown("### 3. Cash Flow")
                cf_vals = {}
                with st.expander("Expand Cash Flow Fields"):
                    for key, label in CF_FIELDS:
                        if "(Derived)" in label:
                            continue
                        item = record["cash_flow"].get(key) if record["cash_flow"] else None
                        default_val = float(item["value"]) if item and item["value"] is not None else 0.0
                        cf_vals[key] = st.number_input(label, value=default_val, format="%.2f", key=f"review_cf_{key}")
                        
                st.markdown("---")
                st.markdown("#### Learn Mapping / Add Custom Alias Rule")
                learn_alias = st.checkbox("Automatically add row label to self-improving alias dictionary", value=False)
                
                alias_label = ""
                alias_field = "revenue"
                if learn_alias:
                    alias_label = st.text_input("Raw row label from PDF (e.g. 'Turnover in Operations')")
                    alias_field = st.selectbox("Map it to Canonical Field", list(ALL_FIELDS_MAP.keys()), format_func=lambda x: ALL_FIELDS_MAP[x])
                    
                if st.button("✅ Save & Approve Statement", use_container_width=True):
                    # Package inputs back into SQLite schema JSON formats
                    inc_json = {}
                    for key, label in PL_FIELDS:
                        if "(Derived)" in label:
                            continue
                        inc_json[key] = {
                            "value": pl_vals[key],
                            "unit": record["currency_unit"],
                            "confidence": "high",
                            "source_pages": ["Manual Correction"],
                            "alias_matched": "Manual Correction"
                        }
                    pipeline.calculate_derived_fields("income_statement", inc_json)
                    
                    bs_json = {}
                    for key, label in BS_FIELDS:
                        if "(Derived)" in label:
                            continue
                        bs_json[key] = {
                            "value": bs_vals[key],
                            "unit": record["currency_unit"],
                            "confidence": "high",
                            "source_pages": ["Manual Correction"],
                            "alias_matched": "Manual Correction"
                        }
                    pipeline.calculate_derived_fields("balance_sheet", bs_json)
                    
                    cf_json = {}
                    for key, label in CF_FIELDS:
                        if "(Derived)" in label:
                            continue
                        cf_json[key] = {
                            "value": cf_vals[key],
                            "unit": record["currency_unit"],
                            "confidence": "high",
                            "source_pages": ["Manual Correction"],
                            "alias_matched": "Manual Correction"
                        }
                    pipeline.calculate_derived_fields("cash_flow", cf_json)
                    
                    # Run validations
                    stmt_dict = {
                        "income_statement": inc_json,
                        "balance_sheet": bs_json,
                        "cash_flow": cf_json
                    }
                    valid, errors = pipeline.run_arithmetic_validation(stmt_dict)
                    
                    # Save to DB
                    database.save_statement(
                        company_name=record["company_name"],
                        fiscal_period=record["fiscal_period"],
                        currency_unit=record["currency_unit"],
                        statement_type=record["statement_type"],
                        source_pdf=record["source_pdf"],
                        income_statement=inc_json,
                        balance_sheet=bs_json,
                        cash_flow=cf_json,
                        arithmetic_check_passed=valid,
                        conflicts=[],
                        unresolved_fields=[],
                        status="reviewed"
                    )
                    
                    if learn_alias and alias_label:
                        database.add_alias(alias_label, alias_field)
                        st.success(f"Added alias: '{alias_label}' -> '{ALL_FIELDS_MAP[alias_field]}' to dictionary!")
                        
                    st.success("Statement updated successfully and marked as Reviewed!")
                    st.rerun()

# --- TAB 4: ALIAS DICTIONARY MANAGER ---
with tab_aliases:
    st.subheader("Manage Self-Improving Alias Dictionary")
    st.info("Row labels matching these aliases are automatically recognized and mapped to canonical fields, bypassing expensive LLM OCR calls.")
    
    col_add, col_list = st.columns([1, 2])
    
    with col_add:
        st.markdown("#### Create New Alias Rule")
        new_label = st.text_input("Raw Row Label (from PDF)", placeholder="e.g. Total Revenue from Operations")
        new_field = st.selectbox("Canonical Target Field", list(ALL_FIELDS_MAP.keys()), format_func=lambda x: ALL_FIELDS_MAP[x])
        
        if st.button("➕ Add Rule to Dictionary", use_container_width=True):
            if new_label:
                added = database.add_alias(new_label, new_field)
                if added:
                    st.success("Successfully added alias rule!")
                    st.rerun()
                else:
                    st.error("Could not add alias rule. It may already exist.")
            else:
                st.warning("Please enter a row label.")
                
    with col_list:
        st.markdown("#### Active Alias Rules")
        aliases = database.get_aliases()
        
        flat_list = []
        for field, labels in aliases.items():
            field_display = ALL_FIELDS_MAP.get(field, field)
            for label in labels:
                flat_list.append({"Canonical Field": field_display, "Raw Row Label / Alias": label})
                
        if flat_list:
            df_aliases = pd.DataFrame(flat_list)
            search_alias = st.text_input("🔍 Filter Rules")
            if search_alias:
                df_aliases = df_aliases[
                    df_aliases["Canonical Field"].str.contains(search_alias, case=False) |
                    df_aliases["Raw Row Label / Alias"].str.contains(search_alias, case=False)
                ]
            st.dataframe(df_aliases, use_container_width=True, hide_index=True)
        else:
            st.info("No alias rules in database.")
