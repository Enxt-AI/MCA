"""
Pydantic data models for structured financial extraction.

Defines 50+ financial fields across:
  - IncomeStatement  (revenue → EPS)
  - BalanceSheet     (assets → liabilities)
  - CashFlow         (operating → financing)
  - CompanyFinancials (wraps all three per company/period)
  - ExtractionResult  (top-level container for multi-company output)
"""

from pydantic import BaseModel, Field
from typing import Optional


# ── INCOME STATEMENT ──────────────────────────────────────────────────────────

class IncomeStatement(BaseModel):
    """Income statement / Profit & Loss fields."""

    revenue: Optional[float] = Field(
        None, description="Total Revenue / Net Sales"
    )
    cost_of_materials: Optional[float] = Field(
        None, description="Raw material consumption"
    )
    change_in_inventory: Optional[float] = Field(
        None, description="Change in WIP & finished goods"
    )
    gross_profit: Optional[float] = Field(
        None, description="Revenue minus COGS"
    )
    gross_margin_pct: Optional[float] = Field(
        None, description="Gross Profit / Revenue * 100"
    )
    employee_benefit_expenses: Optional[float] = Field(
        None, description="Staff costs / salaries"
    )
    other_expenses: Optional[float] = Field(
        None, description="Other operating expenses"
    )
    ebitda: Optional[float] = Field(
        None,
        description="Earnings Before Interest, Tax, Depreciation & Amortization",
    )
    ebitda_margin_pct: Optional[float] = Field(
        None, description="EBITDA / Revenue * 100"
    )
    depreciation_amortization: Optional[float] = Field(
        None, description="Depreciation & Amortization charged for the period"
    )
    ebit: Optional[float] = Field(
        None, description="Earnings Before Interest & Tax (EBITDA - D&A)"
    )
    ebit_margin_pct: Optional[float] = Field(
        None, description="EBIT / Revenue * 100"
    )
    other_income: Optional[float] = Field(
        None, description="Non-operating income"
    )
    finance_cost: Optional[float] = Field(
        None, description="Interest expense on borrowings"
    )
    pbt: Optional[float] = Field(None, description="Profit Before Tax")
    tax: Optional[float] = Field(None, description="Total tax expense")
    pat: Optional[float] = Field(
        None, description="Profit After Tax / Net Profit"
    )
    net_profit_margin_pct: Optional[float] = Field(
        None, description="PAT / Revenue * 100"
    )
    eps_basic: Optional[float] = Field(
        None, description="Basic Earnings Per Share"
    )
    eps_diluted: Optional[float] = Field(
        None, description="Diluted Earnings Per Share"
    )


# ── BALANCE SHEET ─────────────────────────────────────────────────────────────

class BalanceSheet(BaseModel):
    """Balance sheet / Statement of financial position fields."""

    fixed_assets: Optional[float] = Field(
        None, description="Net Block / Property Plant & Equipment"
    )
    cwip: Optional[float] = Field(
        None, description="Capital Work In Progress"
    )
    investments: Optional[float] = Field(
        None, description="Long-term investments"
    )
    trade_receivables: Optional[float] = Field(
        None, description="Accounts receivable / debtors"
    )
    inventory: Optional[float] = Field(
        None, description="Stock / closing inventory"
    )
    other_assets: Optional[float] = Field(
        None, description="Other current & non-current assets"
    )
    share_capital: Optional[float] = Field(
        None, description="Paid-up share capital"
    )
    face_value: Optional[float] = Field(
        None, description="Face value per share"
    )
    reserves_surplus: Optional[float] = Field(
        None, description="Reserves & surplus"
    )
    borrowings: Optional[float] = Field(
        None, description="Total debt (short + long term)"
    )
    trade_payables: Optional[float] = Field(
        None, description="Accounts payable / creditors"
    )
    other_liabilities: Optional[float] = Field(
        None, description="Other current & non-current liabilities"
    )


# ── CASH FLOW STATEMENT ──────────────────────────────────────────────────────

class CashFlow(BaseModel):
    """Cash flow statement fields."""

    opbwc: Optional[float] = Field(
        None, description="Operating Profit Before Working Capital Changes"
    )
    change_in_receivables: Optional[float] = Field(
        None, description="Increase/decrease in trade receivables"
    )
    change_in_inventories: Optional[float] = Field(
        None, description="Increase/decrease in inventory"
    )
    change_in_payables: Optional[float] = Field(
        None, description="Increase/decrease in trade payables"
    )
    other_working_capital_changes: Optional[float] = Field(
        None, description="Misc working capital movements"
    )
    working_capital_change: Optional[float] = Field(
        None, description="Net change in working capital"
    )
    cash_generated_from_ops: Optional[float] = Field(
        None, description="OPBWC +/- Working capital changes"
    )
    tax_paid: Optional[float] = Field(
        None, description="Taxes paid (cash basis)"
    )
    cash_flow_from_operations: Optional[float] = Field(
        None, description="CFO (after tax paid)"
    )
    purchase_of_ppe: Optional[float] = Field(
        None, description="Capex / purchase of fixed assets"
    )
    sale_of_ppe: Optional[float] = Field(
        None, description="Proceeds from asset disposals"
    )
    cash_flow_from_investing: Optional[float] = Field(
        None, description="Net CFI"
    )
    borrowings_net: Optional[float] = Field(
        None, description="Net proceeds from / repayment of debt"
    )
    dividend_paid: Optional[float] = Field(
        None, description="Dividend paid to shareholders"
    )
    equity_raised: Optional[float] = Field(
        None, description="Equity / rights issue proceeds"
    )
    other_financing_activities: Optional[float] = Field(
        None, description="Misc financing flows"
    )
    cash_flow_from_financing: Optional[float] = Field(
        None, description="Net CFF"
    )
    net_cash_generated: Optional[float] = Field(
        None, description="Net increase/decrease in cash"
    )
    cash_at_start: Optional[float] = Field(
        None, description="Opening cash & equivalents"
    )
    cash_at_end: Optional[float] = Field(
        None, description="Closing cash & equivalents"
    )


# ── COMPANY-LEVEL WRAPPER ────────────────────────────────────────────────────

class CompanyFinancials(BaseModel):
    """All financial data for a single company in a single period."""

    company_name: str
    period: str = Field(..., description="e.g. FY2024, Q3FY25")
    currency_unit: str = Field(..., description="e.g. INR Crores, USD Millions")
    income_statement: IncomeStatement = IncomeStatement()
    balance_sheet: BalanceSheet = BalanceSheet()
    cash_flow: CashFlow = CashFlow()


# ── TOP-LEVEL EXTRACTION RESULT ──────────────────────────────────────────────

class ExtractionResult(BaseModel):
    """Container for all extracted company financials from a document."""

    companies: list[CompanyFinancials]
