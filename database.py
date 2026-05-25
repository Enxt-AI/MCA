import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "financial_data.db")

def get_connection():
    """Returns a SQLite connection that maps row results to dictionaries."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database schema and seeds default aliases."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Check if old table exists and drop it for schema upgrade
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='extracted_statements'")
    table_exists = cursor.fetchone()
    if table_exists:
        cursor.execute("PRAGMA table_info(extracted_statements)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "revenue" in columns and "income_statement" not in columns:
            print("Old 4-field table schema detected. Upgrading to comprehensive 42-field JSON schema...")
            cursor.execute("DROP TABLE extracted_statements")
            conn.commit()
    
    # Create tables with comprehensive schema
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS extracted_statements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name TEXT NOT NULL,
        fiscal_period TEXT NOT NULL,
        currency_unit TEXT,
        statement_type TEXT NOT NULL DEFAULT 'Consolidated', -- 'Standalone' or 'Consolidated'
        source_pdf TEXT NOT NULL,
        extracted_at TEXT NOT NULL,
        income_statement TEXT, -- JSON string containing P&L fields
        balance_sheet TEXT, -- JSON string containing Balance Sheet fields
        cash_flow TEXT, -- JSON string containing Cash Flow fields
        arithmetic_check_passed INTEGER DEFAULT 0,
        conflicts TEXT DEFAULT '[]', -- JSON string array of conflicts
        unresolved_fields TEXT DEFAULT '[]', -- JSON string array of unresolved fields
        status TEXT DEFAULT 'processed', -- 'processed', 'flagged_for_review', 'reviewed'
        UNIQUE(company_name, fiscal_period, statement_type, source_pdf)
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS alias_dictionary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_label TEXT UNIQUE NOT NULL,
        canonical_field TEXT NOT NULL
    )
    """)
    
    conn.commit()
    
    # Comprehensive seeds for 42+ financial fields across P&L, BS, and Cash Flow
    seed_aliases = {
        # --- INCOME STATEMENT ---
        "revenue": [
            "revenue from operations", "revenue", "sales", "turnover", "net sales",
            "income from operations", "revenue from contracts with customers",
            "net revenue from operations", "sale of products", "sale of services",
            "revenue from operations (gross)", "revenue from operations (net)",
            "total revenue from operations", "gross revenue", "revenue from operation",
            "income from sale of products", "income from sale of services",
            "revenue from contracts with customers (gross)"
        ],
        "cost_of_materials": [
            "cost of materials consumed", "cost of raw materials consumed", "raw material consumed",
            "consumption of raw materials", "cost of materials", "cogs", "cost of goods sold",
            "materials consumed", "raw materials consumed", "cost of material consumed",
            "purchases of stock-in-trade", "purchase of stock-in-trade", "purchases of stock in trade",
            "cost of material and components consumed", "material cost",
            "total cost of materials consumed"
        ],
        "change_in_inventory": [
            "changes in inventories of finished goods, work-in-progress and stock-in-trade",
            "changes in inventories of finished goods and work-in-progress",
            "change in inventories", "changes in inventories", "decrease / (increase) in inventories",
            "decrease/(increase) in inventories", "inventory change", "changes in inventories of finished goods",
            "total changes in inventories of finished goods, work-in-progress and stock-in-trade",
            "(increase)/decrease in inventories", "increase/(decrease) in inventories"
        ],
        "employee_benefit_expenses": [
            "employee benefits expense", "employee benefit expense", "employee benefits expenses",
            "staff costs", "staff cost", "salaries and wages", "salaries, wages and bonus",
            "personnel expenses", "employee cost", "employee costs",
            "total employee benefits expense", "wages, salaries and bonus",
            "manpower cost", "human resource cost"
        ],
        "other_expenses": [
            "other expenses", "other expense", "administrative expenses", "selling and distribution expenses",
            "manufacturing expenses", "other operating expenses", "operating expenses",
            "total other expenses", "selling, general and administrative expenses",
            "general and administrative expenses", "miscellaneous expenses"
        ],
        "other_income": [
            "other income", "non-operating income", "other non-operating income",
            "interest income", "dividend income", "other operating income",
            "total other income", "miscellaneous income", "other revenues"
        ],
        "depreciation_amortization": [
            "depreciation and amortisation expense", "depreciation and amortization expense",
            "depreciation and amortisation", "depreciation and amortization",
            "depreciation & amortisation", "depreciation, amortisation and impairment",
            "total depreciation and amortisation expense", "depreciation",
            "amortisation", "amortization", "depreciation on tangible assets"
        ],
        "finance_cost": [
            "finance costs", "finance cost", "interest and finance charges", "interest expenses",
            "finance costs and interest expense", "interest expense",
            "total finance costs", "borrowing costs", "interest on borrowings",
            "interest and borrowing costs"
        ],
        "pbt": [
            "profit before tax", "profit / (loss) before tax", "profit before exceptional items and tax",
            "total profit before tax", "profit/(loss) before tax", "pbt", "profit before tax and exceptional items",
            "profit before taxation", "income before tax", "earnings before tax",
            "profit / loss before tax", "profit before income tax",
            "profit (loss) before tax", "profit or loss before tax"
        ],
        "tax": [
            "tax expense", "tax expenses", "total tax expense", "provision for tax", "current tax",
            "deferred tax", "current tax expense", "deferred tax expense", "tax expense (current & deferred)",
            "income tax expense", "total income tax expense", "provision for income tax",
            "tax expense for the year", "taxation"
        ],
        "pat": [
            "profit after tax", "profit for the year", "profit for the period", "pat",
            "net profit after tax", "profit / (loss) for the year", "profit / (loss) for the period",
            "profit/(loss) for the year", "total profit for the period", "net profit",
            "profit / loss after tax", "profit (loss) for the year",
            "profit or loss for the year", "profit after income tax",
            "net income", "total comprehensive income for the year",
            "profit after tax for the year", "net profit for the year",
            "profit/(loss) after tax", "profit / (loss) after tax"
        ],
        "eps_basic": [
            "eps basic", "basic eps", "basic earnings per share", "basic", "earnings per equity share - basic",
            "basic earnings per equity share", "eps - basic"
        ],
        "eps_diluted": [
            "eps diluted", "diluted eps", "diluted earnings per share", "diluted", "earnings per equity share - diluted",
            "diluted earnings per equity share", "eps - diluted"
        ],
        
        # --- BALANCE SHEET ---
        "fixed_assets": [
            "property, plant and equipment", "tangible assets", "net block", "fixed assets",
            "property, plant & equipment", "property, plant and equipment (net)", "fixed assets (net)",
            "total property, plant and equipment", "net fixed assets",
            "property plant and equipment", "plant and machinery"
        ],
        "cwip": [
            "capital work-in-progress", "capital work in progress", "cwip", "capital wip",
            "capital work-in-progress (cwip)", "capital work in progress (cwip)"
        ],
        "investments": [
            "non-current investments", "long term investments", "investments", "non-current financial assets - investments",
            "financial assets - investments", "total investments", "investment in subsidiaries",
            "investment in associates", "current investments", "mutual funds",
            "investment in equity instruments", "investment in debt instruments"
        ],
        "trade_receivables": [
            "trade receivables", "sundry debtors", "receivables", "current financial assets - trade receivables",
            "total trade receivables", "trade and other receivables",
            "debtors", "accounts receivable", "bills receivable",
            "trade receivables (current)", "current - trade receivables"
        ],
        "inventory": [
            "inventories", "inventory", "stock", "stocks", "inventories (current)",
            "total inventories", "raw materials", "work-in-progress", "finished goods",
            "stock in trade", "stores and spares"
        ],
        "share_capital": [
            "share capital", "equity share capital", "paid up share capital", "issued and paid up capital",
            "total share capital", "paid-up equity share capital", "issued, subscribed and paid up capital",
            "issued subscribed and paid up"
        ],
        "face_value": [
            "face value", "par value", "nominal value", "face value per share"
        ],
        "reserves_surplus": [
            "reserves and surplus", "other equity", "reserves & surplus", "retained earnings",
            "total other equity", "total reserves and surplus", "reserves",
            "other reserves", "surplus in statement of profit and loss",
            "securities premium", "retained earnings / (deficit)"
        ],
        "borrowings": [
            "borrowings", "long-term borrowings", "short-term borrowings", "current maturities of long-term debt",
            "non-current borrowings", "current borrowings", "total borrowings",
            "loans", "term loans", "bank loans", "financial liabilities - borrowings",
            "non-current financial liabilities - borrowings", "current financial liabilities - borrowings",
            "total loans", "secured loans", "unsecured loans",
            "borrowings, non-current", "borrowings, current"
        ],
        "trade_payables": [
            "trade payables", "sundry creditors", "payables", "current financial liabilities - trade payables",
            "total trade payables", "trade and other payables",
            "creditors", "accounts payable", "bills payable",
            "total outstanding dues of micro enterprises and small enterprises",
            "total outstanding dues of creditors other than micro enterprises",
            "trade payables (current)", "current - trade payables",
            "payables - trade payables", "trade payables total",
            "total payables", "trade payables, current"
        ],
        
        # --- CASH FLOW STATEMENT ---
        "opbwc": [
            "operating profit before working capital changes", "operating profit before changes in working capital",
            "profit before working capital changes", "operating profit before working capital changes subtotal"
        ],
        "change_in_receivables": [
            "decrease / (increase) in trade receivables", "(increase) / decrease in trade receivables",
            "decrease/(increase) in trade receivables", "trade receivables", "change in trade receivables"
        ],
        "change_in_inventories": [
            "decrease / (increase) in inventories", "(increase) / decrease in inventories",
            "decrease/(increase) in inventories", "inventories", "change in inventories"
        ],
        "change_in_payables": [
            "increase / (decrease) in trade payables", "trade payables", "change in trade payables",
            "increase/(decrease) in trade payables"
        ],
        "other_working_capital_changes": [
            "other working capital changes", "adjustments for working capital", "changes in other assets and liabilities"
        ],
        "cash_generated_from_ops": [
            "cash generated from operations", "cash generated from operating activities"
        ],
        "tax_paid": [
            "direct taxes paid", "income taxes paid", "tax paid", "income tax paid", "taxes paid",
            "direct tax paid", "direct taxes paid (net of refunds)"
        ],
        "cash_flow_from_operations": [
            "net cash flow from operating activities", "net cash generated from operating activities",
            "cash flow from operations", "cfo", "net cash from operating activities"
        ],
        "purchase_of_ppe": [
            "purchase of property, plant and equipment", "payment for property, plant and equipment",
            "capital expenditure", "purchase of fixed assets", "capex", "purchase of ppe"
        ],
        "sale_of_ppe": [
            "proceeds from sale of property, plant and equipment", "sale of property, plant and equipment",
            "proceeds from sale of fixed assets", "sale of fixed assets", "proceeds from sale of ppe"
        ],
        "cash_flow_from_investing": [
            "net cash flow from investing activities", "net cash generated from investing activities",
            "cash flow from investing activities", "cfi", "net cash from / (used in) investing activities"
        ],
        "borrowings_net": [
            "proceeds / (repayments) of borrowings", "net borrowings", "borrowings (net)",
            "proceeds from long term borrowings", "repayment of long term borrowings",
            "proceeds/(repayments) of borrowings"
        ],
        "dividend_paid": [
            "dividend paid", "payment of dividend", "dividends paid", "payment of equity dividend"
        ],
        "equity_raised": [
            "proceeds from issue of share capital", "proceeds from issue of equity shares", "issue of shares",
            "proceeds from issue of shares"
        ],
        "other_financing_activities": [
            "other financing activities", "interest paid", "finance cost paid", "payment of lease liabilities"
        ],
        "cash_flow_from_financing": [
            "net cash flow from financing activities", "net cash generated from financing activities",
            "cash flow from financing activities", "cff", "net cash from / (used in) financing activities"
        ],
        "net_cash_generated": [
            "net increase / (decrease) in cash and cash equivalents", "net cash generated",
            "net increase/(decrease) in cash and cash equivalents", "net increase in cash and cash equivalents"
        ],
        "cash_at_start": [
            "cash and cash equivalents at the beginning of the year",
            "cash and cash equivalents at the beginning of the period", "opening cash",
            "cash and cash equivalents at beginning of the year"
        ],
        "cash_at_end": [
            "cash and cash equivalents at the end of the year",
            "cash and cash equivalents at the end of the period", "closing cash",
            "cash and cash equivalents at end of the year"
        ]
    }
    
    inserted = 0
    for field, raw_labels in seed_aliases.items():
        for label in raw_labels:
            try:
                normalized_label = label.strip().lower()
                cursor.execute(
                    "INSERT INTO alias_dictionary (raw_label, canonical_field) VALUES (?, ?)",
                    (normalized_label, field)
                )
                inserted += 1
            except sqlite3.IntegrityError:
                pass
                
    if inserted > 0:
        conn.commit()
        print(f"Seeded database with {inserted} initial aliases for 42 financial fields.")
        
    conn.close()

def get_aliases():
    """Fetches the complete alias mapping as a dict of canonical_field -> list of raw_labels."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT raw_label, canonical_field FROM alias_dictionary")
    rows = cursor.fetchall()
    conn.close()
    
    # 42 canonical fields
    canonical_fields = [
        "revenue", "cost_of_materials", "change_in_inventory", "employee_benefit_expenses",
        "other_expenses", "other_income", "depreciation_amortization", "finance_cost", "pbt", "tax", "pat", "eps_basic", "eps_diluted",
        "fixed_assets", "cwip", "investments", "trade_receivables", "inventory", "share_capital", "face_value", "reserves_surplus", "borrowings", "trade_payables",
        "opbwc", "change_in_receivables", "change_in_inventories", "change_in_payables", "other_working_capital_changes",
        "cash_generated_from_ops", "tax_paid", "cash_flow_from_operations", "purchase_of_ppe", "sale_of_ppe", "cash_flow_from_investing",
        "borrowings_net", "dividend_paid", "equity_raised", "other_financing_activities", "cash_flow_from_financing", "net_cash_generated", "cash_at_start", "cash_at_end"
    ]
    
    mappings = {field: [] for field in canonical_fields}
    for row in rows:
        field = row["canonical_field"]
        if field in mappings:
            mappings[field].append(row["raw_label"])
        else:
            mappings[field] = [row["raw_label"]]
            
    return mappings

def save_statement(company_name, fiscal_period, source_pdf, currency_unit="INR Lakhs", statement_type="Consolidated",
                   income_statement=None, balance_sheet=None, cash_flow=None, arithmetic_check_passed=True, 
                   conflicts=None, unresolved_fields=None, status="processed"):
    """Saves or updates an extracted statement in the database."""
    conn = get_connection()
    cursor = conn.cursor()
    
    now = datetime.utcnow().isoformat() + "Z"
    
    def to_json_str(data):
        if data is None:
            return None
        if isinstance(data, str):
            return data
        return json.dumps(data)
        
    conflicts_str = json.dumps(conflicts or [])
    unresolved_str = json.dumps(unresolved_fields or [])
    
    try:
        cursor.execute("""
        INSERT INTO extracted_statements (
            company_name, fiscal_period, currency_unit, statement_type, source_pdf, extracted_at, 
            income_statement, balance_sheet, cash_flow, 
            arithmetic_check_passed, conflicts, unresolved_fields, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_name, fiscal_period, statement_type, source_pdf) DO UPDATE SET
            extracted_at = excluded.extracted_at,
            currency_unit = excluded.currency_unit,
            income_statement = COALESCE(excluded.income_statement, income_statement),
            balance_sheet = COALESCE(excluded.balance_sheet, balance_sheet),
            cash_flow = COALESCE(excluded.cash_flow, cash_flow),
            arithmetic_check_passed = excluded.arithmetic_check_passed,
            conflicts = excluded.conflicts,
            unresolved_fields = excluded.unresolved_fields,
            status = excluded.status
        """, (
            company_name, fiscal_period, currency_unit, statement_type, source_pdf, now,
            to_json_str(income_statement), to_json_str(balance_sheet), to_json_str(cash_flow),
            1 if arithmetic_check_passed else 0, conflicts_str, unresolved_str, status
        ))
        conn.commit()
    except Exception as e:
        print(f"Error saving statement to database: {e}")
    finally:
        conn.close()

def add_alias(raw_label, canonical_field):
    """Adds a new alias mapping to the dictionary. Returns True if added, False otherwise."""
    conn = get_connection()
    cursor = conn.cursor()
    normalized_label = raw_label.strip().lower()
    success = False
    try:
        cursor.execute(
            "INSERT INTO alias_dictionary (raw_label, canonical_field) VALUES (?, ?)",
            (normalized_label, canonical_field)
        )
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        try:
            cursor.execute(
                "UPDATE alias_dictionary SET canonical_field = ? WHERE raw_label = ?",
                (canonical_field, normalized_label)
            )
            conn.commit()
            success = True
        except Exception:
            pass
    conn.close()
    return success

def get_statements(status=None):
    """Retrieves saved statements from the database, optionally filtered by status."""
    conn = get_connection()
    cursor = conn.cursor()
    if status:
        cursor.execute("SELECT * FROM extracted_statements WHERE status = ? ORDER BY extracted_at DESC", (status,))
    else:
        cursor.execute("SELECT * FROM extracted_statements ORDER BY extracted_at DESC")
    rows = cursor.fetchall()
    conn.close()
    
    statements = []
    for r in rows:
        statements.append({
            "id": r["id"],
            "company_name": r["company_name"],
            "fiscal_period": r["fiscal_period"],
            "currency_unit": r["currency_unit"],
            "statement_type": r["statement_type"],
            "source_pdf": r["source_pdf"],
            "extracted_at": r["extracted_at"],
            "income_statement": json.loads(r["income_statement"]) if r["income_statement"] else None,
            "balance_sheet": json.loads(r["balance_sheet"]) if r["balance_sheet"] else None,
            "cash_flow": json.loads(r["cash_flow"]) if r["cash_flow"] else None,
            "arithmetic_check_passed": bool(r["arithmetic_check_passed"]),
            "conflicts": json.loads(r["conflicts"]),
            "unresolved_fields": json.loads(r["unresolved_fields"]),
            "status": r["status"]
        })
    return statements

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at:", DB_PATH)
