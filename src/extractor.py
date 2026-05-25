"""
Phase 4 — LLM Extraction via Gemini 1.5 Pro.

Uses LangChain's ChatGoogleGenerativeAI with structured output
(Pydantic) to extract 50+ financial fields from retrieved context
chunks. The system prompt includes full-form annotations for every
abbreviated field (EBITDA, OPBWC, PBT, CFI, etc.) to reduce
hallucination and null values.
"""

import os
import logging

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from .models import ExtractionResult

logger = logging.getLogger(__name__)


# ── System prompt with full field annotations ─────────────────────────────────
# Abbreviations like OPBWC, CWIP, PBT, CFI are ambiguous across filings.
# Embedding the full form as an inline comment ensures Gemini maps the correct
# line item — especially for Indian GAAP vs IFRS differences.

SYSTEM_PROMPT = """\
You are an expert financial analyst specializing in structured data extraction
from annual reports, financial statements, and earnings documents.

From the retrieved document chunks below, extract ALL financial metrics
for EACH company mentioned. If a value is not found, return null.

All monetary values should be in the same unit as the source document
(mention the unit in a separate 'currency_unit' field).

Return a JSON array where each object represents one company's financials:
[
  {{
    'company_name': '',
    'currency_unit': 'INR Crores / USD Millions / etc.',
    'period': 'FY2024 / Q3FY25 / etc.',

    // ── INCOME STATEMENT ──────────────────────────────────────────
    'revenue'                       : null,  // Total Revenue / Net Sales
    'cost_of_materials'             : null,  // Raw material consumption
    'change_in_inventory'           : null,  // Change in WIP & finished goods
    'gross_profit'                  : null,  // Revenue - COGS
    'gross_margin_pct'              : null,  // Gross Profit / Revenue x 100
    'employee_benefit_expenses'     : null,  // Staff costs / salaries
    'other_expenses'                : null,  // Other operating expenses
    'ebitda'                        : null,  // Earnings Before Interest, Tax, Depreciation & Amortization
    'ebitda_margin_pct'             : null,  // EBITDA / Revenue x 100
    'depreciation_amortization'     : null,  // D&A charged for the period
    'ebit'                          : null,  // Earnings Before Interest & Tax (EBITDA - D&A)
    'ebit_margin_pct'               : null,  // EBIT / Revenue x 100
    'other_income'                  : null,  // Non-operating income
    'finance_cost'                  : null,  // Interest expense on borrowings
    'pbt'                           : null,  // Profit Before Tax
    'tax'                           : null,  // Total tax expense
    'pat'                           : null,  // Profit After Tax / Net Profit
    'net_profit_margin_pct'         : null,  // PAT / Revenue x 100
    'eps_basic'                     : null,  // Basic Earnings Per Share
    'eps_diluted'                   : null,  // Diluted Earnings Per Share

    // ── BALANCE SHEET ─────────────────────────────────────────────
    'fixed_assets'                  : null,  // Net Block / Property Plant & Equipment
    'cwip'                          : null,  // Capital Work In Progress
    'investments'                   : null,  // Long-term investments
    'trade_receivables'             : null,  // Accounts receivable / debtors
    'inventory'                     : null,  // Stock / closing inventory
    'other_assets'                  : null,  // Other current & non-current assets
    'share_capital'                 : null,  // Paid-up share capital
    'face_value'                    : null,  // Face value per share
    'reserves_surplus'              : null,  // Reserves & surplus
    'borrowings'                    : null,  // Total debt (short + long term)
    'trade_payables'                : null,  // Accounts payable / creditors
    'other_liabilities'             : null,  // Other current & non-current liabilities

    // ── CASH FLOW STATEMENT ───────────────────────────────────────
    'opbwc'                         : null,  // Operating Profit Before Working Capital Changes
    'change_in_receivables'         : null,  // Increase/decrease in trade receivables
    'change_in_inventories'         : null,  // Increase/decrease in inventory
    'change_in_payables'            : null,  // Increase/decrease in trade payables
    'other_working_capital_changes' : null,  // Misc working capital movements
    'working_capital_change'        : null,  // Net change in working capital
    'cash_generated_from_operations': null,  // OPBWC +/- Working capital changes
    'tax_paid'                      : null,  // Taxes paid (cash basis)
    'cash_flow_from_operations'     : null,  // CFO (after tax paid)
    'purchase_of_ppe'               : null,  // Capex / purchase of fixed assets
    'sale_of_ppe'                   : null,  // Proceeds from asset disposals
    'cash_flow_from_investing'      : null,  // Net CFI
    'borrowings_net'                : null,  // Net proceeds from / repayment of debt
    'dividend_paid'                 : null,  // Dividend paid to shareholders
    'equity_raised'                 : null,  // Equity / rights issue proceeds
    'other_financing_activities'    : null,  // Misc financing flows
    'cash_flow_from_financing'      : null,  // Net CFF
    'net_cash_generated'            : null,  // Net increase/decrease in cash
    'cash_at_start'                 : null,  // Opening cash & equivalents
    'cash_at_end'                   : null   // Closing cash & equivalents
  }}
]

Rules:
- Extract data for ALL companies found across chunks
- Use negative values for outflows (e.g. capex, tax paid, dividends)
- Do NOT hallucinate — return null if data is absent
- If multiple periods exist, add a 'period' field (e.g. 'FY2024', 'Q3FY25')
"""


def build_extraction_chain() -> Runnable:
    """
    Build the LangChain extraction chain:
      Gemini 1.5 Pro + structured Pydantic output.

    Returns:
        A LangChain Runnable (prompt | structured_llm) that accepts
        {'context': str} and returns an ExtractionResult.

    Raises:
        EnvironmentError: If GOOGLE_API_KEY is not set.
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "Missing GOOGLE_API_KEY in environment variables. "
            "See .env.example for required keys."
        )

    llm = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite-preview",
        temperature=0,
        google_api_key=api_key,
    )

    structured_llm = llm.with_structured_output(ExtractionResult)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            ("human", "Context chunks:\n{context}\n\nExtract all financial data."),
        ]
    )

    chain = prompt | structured_llm

    logger.info("Extraction chain built: Gemini 3.1 Flash-Lite + structured Pydantic output.")
    return chain
