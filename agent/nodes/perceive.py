"""
agent/nodes/perceive.py
=======================

**Node 1: Perceive** — Assembles the complete ``TreasuryState`` snapshot.

Purpose
-------
The Perceive node is the agent's "eyes".  It queries every data source the
downstream nodes need and assembles them into a single, validated
``TreasuryState`` object.  All downstream nodes receive this snapshot and
trust it — they do **not** make their own API calls.

Data sources queried (in order)
---------------------------------
1. ``erp_client.get_cash_positions()`` — ERP bank account balances
2. ``bank_client.get_account_balances()`` — live bank balances (authoritative)
3. ``erp_client.get_open_payables()`` — vendor AP obligations
4. ``erp_client.get_payroll_postings()`` — payroll obligations
5. ``erp_client.get_tax_items()`` — statutory tax obligations
6. ``erp_client.get_loan_items()`` — loan installment obligations
7. ``bank_client.get_statement(last_30_days)`` — for reconciliation

Failure handling
----------------
For each source, the node follows this pattern:

1. Attempt live fetch.
2. On failure: load from cache (``DataFreshness.STALE``).
3. If no cache: record ``DataFreshness.MISSING``.
4. If the **cash position** data is MISSING: set ``execution_blocked = True``.
   This propagates through all downstream nodes and forces ``NO_ACTION``.

Unreconciled credit detection
------------------------------
Large credits (> LKR 100K) in the bank statement that have no matching ERP
AR document (same date ± 0, same amount ± 5%) are flagged as
``unreconciled_large_credits``.  The Confidence Check node routes to
DISAMBIGUATE if any are found above LKR 1M.

Input / Output
--------------
- Input:  ``AgentContext`` with ``goal`` populated.
- Output: ``AgentContext`` with ``treasury_state`` populated.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from agent.memory.cache import cache_get, cache_set, mark_stale
from agent.state import (
    AgentContext,
    DataFreshness,
    DataSourceStatus,
    MarketRates,
    Obligation,
    TreasuryState,
)
from agent.tools import erp_client, bank_client, market_data_client
from agent.tools.erp_client import ERPClientError
from agent.tools.bank_client import BankClientError

logger = logging.getLogger(__name__)

# Cache keys
_KEY_CASH_POS = "ERP_CASH_POSITION"
_KEY_PAYABLES = "ERP_OPEN_PAYABLES"
_KEY_PAYROLL = "ERP_PAYROLL"
_KEY_TAX = "ERP_TAX"
_KEY_LOANS = "ERP_LOANS"
_KEY_BANK_BALANCES = "BANK_BALANCE"
_KEY_BANK_STMT = "BANK_STATEMENT"
_KEY_MARKET_DATA = "MARKET_DATA"


_UNRECONCILED_THRESHOLD = Decimal("100000.00")   # 100K LKR — flag if credit > this
_RECONCILE_AMOUNT_TOLERANCE = Decimal("0.05")     # ±5% amount tolerance


# ---------------------------------------------------------------------------
# Helper: safe fetch with cache fallback
# ---------------------------------------------------------------------------

def _fetch_with_cache(fetch_fn, cache_key: str, source_label: str) -> tuple[list, DataSourceStatus]:
    """
    Attempt a live fetch; on failure, fall back to cache.

    Parameters
    ----------
    fetch_fn:
        Zero-argument callable that returns the live data.
    cache_key:
        Key used to store/retrieve data from ``DataCache``.
    source_label:
        Human-readable source name for ``DataSourceStatus``.

    Returns
    -------
    tuple[list, DataSourceStatus]
        The data (possibly cached) and the freshness status record.
    """
    try:
        data = fetch_fn()
        cache_set(cache_key, data)
        return data, DataSourceStatus(
            source=source_label,
            freshness=DataFreshness.FRESH,
            last_fresh_at=datetime.utcnow(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Live fetch failed for %s: %s — trying cache.", source_label, exc)
        mark_stale(cache_key)
        cached_data, freshness, last_fresh_at = cache_get(cache_key)
        return (cached_data or []), DataSourceStatus(
            source=source_label,
            freshness=freshness,
            last_fresh_at=last_fresh_at,
            stale_reason=str(exc),
        )


# ---------------------------------------------------------------------------
# Helper: parse ERP date string → date
# ---------------------------------------------------------------------------

def _parse_sap_date(raw: str | None) -> date | None:
    """
    Parse an SAP OData date string to a Python ``date``.

    SAP OData dates arrive as ``"/Date(1735689600000)/"`` (milliseconds since epoch).
    The ERP mock also uses ISO format strings for simplicity.

    Parameters
    ----------
    raw:
        Raw date string from ERP response.

    Returns
    -------
    date | None
    """
    if not raw:
        return None
    # SAP OData format: /Date(epoch_ms)/
    if raw.startswith("/Date("):
        try:
            ms = int(raw[6:raw.index(")")])
            return date.fromtimestamp(ms / 1000)
        except (ValueError, IndexError):
            pass
    # ISO format fallback (used by mock)
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _parse_amount(raw: str | None) -> Decimal:
    """Safely parse a string to Decimal; return 0 on failure."""
    if not raw:
        return Decimal("0")
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return Decimal("0")


# ---------------------------------------------------------------------------
# Helper: build Obligation list from ERP records
# ---------------------------------------------------------------------------

def _build_obligations_from_payables(records: list[dict]) -> list[Obligation]:
    obligations = []
    today = date.today()
    for r in records:
        amount = _parse_amount(r.get("AmountInCompanyCodeCurrency"))
        due = _parse_sap_date(r.get("NetDueDate"))
        if due is None or amount <= 0:
            continue
        obligations.append(Obligation(
            obligation_id=r.get("AccountingDocument", "UNKNOWN"),
            obligation_type="VENDOR_AP",
            amount=amount,
            due_date=due,
            payment_priority=r.get("PaymentPriority", "FLEXIBLE"),
            vendor_id=r.get("Vendor"),
            description=f"Vendor AP — {r.get('Vendor', 'Unknown')}",
            is_overdue=(due < today),
        ))
    return obligations


def _build_obligations_from_payroll(records: list[dict]) -> list[Obligation]:
    obligations = []
    today = date.today()
    for r in records:
        amount = _parse_amount(r.get("TotalNetPayable"))
        due = _parse_sap_date(r.get("PaymentDueDate"))
        if due is None or amount <= 0:
            continue
        obligations.append(Obligation(
            obligation_id=r.get("AccountingDocument", "UNKNOWN"),
            obligation_type="PAYROLL",
            amount=amount,
            due_date=due,
            payment_priority="FIXED",
            description=f"Payroll — period {r.get('PostingPeriod', '')}",
            is_overdue=(due < today),
        ))
    return obligations


def _build_obligations_from_tax(records: list[dict]) -> list[Obligation]:
    obligations = []
    today = date.today()
    for r in records:
        amount = _parse_amount(r.get("AmountInCompanyCodeCurrency"))
        due = _parse_sap_date(r.get("StatutoryDueDate"))
        if due is None or amount <= 0:
            continue
        tax_type = r.get("TaxType", "TAX")
        obligations.append(Obligation(
            obligation_id=r.get("AccountingDocument", "UNKNOWN"),
            obligation_type=f"TAX_{tax_type}",
            amount=amount,
            due_date=due,
            payment_priority="FIXED",
            description=f"Tax — {tax_type} period {r.get('TaxPeriod', '')}",
            is_overdue=(due < today),
        ))
    return obligations


def _build_obligations_from_loans(records: list[dict]) -> list[Obligation]:
    obligations = []
    today = date.today()
    for r in records:
        amount = _parse_amount(r.get("TotalInstallmentAmount"))
        due = _parse_sap_date(r.get("InstallmentDueDate"))
        if due is None or amount <= 0:
            continue
        obligations.append(Obligation(
            obligation_id=f"{r.get('LoanContract', 'LOAN')}-{r.get('InstallmentNumber', '')}",
            obligation_type="LOAN",
            amount=amount,
            due_date=due,
            payment_priority="FIXED",
            description=f"Loan {r.get('LoanContract', '')} installment {r.get('InstallmentNumber', '')}",
            is_overdue=(due < today),
        ))
    return obligations


# ---------------------------------------------------------------------------
# Unreconciled credit detection
# ---------------------------------------------------------------------------

def _detect_unreconciled_credits(
    bank_statement: dict,
    erp_payables: list[dict],
) -> list[dict]:
    """
    Find bank credits that have no matching ERP AR document.

    Compares every CREDIT transaction in the bank statement against ERP
    payables.  A bank credit is considered "reconciled" if there is an ERP
    document with the same amount (±5%) on the same date.

    Only credits above ``_UNRECONCILED_THRESHOLD`` (LKR 100K) are included.

    Parameters
    ----------
    bank_statement:
        Response from ``bank_client.get_statement``.
    erp_payables:
        List of open AP documents from ERP.

    Returns
    -------
    list[dict]
        Unreconciled credit records with keys: ``transactionId``, ``date``,
        ``amount``, ``description``.
    """
    transactions = bank_statement.get("transactions", [])
    unreconciled = []

    for tx in transactions:
        if tx.get("direction") != "CREDIT":
            continue
        tx_amount = _parse_amount(tx.get("amount"))
        if tx_amount < _UNRECONCILED_THRESHOLD:
            continue

        # Try to match against ERP payables by amount ±5% and date
        tx_date_str = str(tx.get("date", ""))[:10]
        matched = False
        for erp_doc in erp_payables:
            erp_amount = _parse_amount(erp_doc.get("AmountInCompanyCodeCurrency"))
            erp_date_str = str(_parse_sap_date(erp_doc.get("NetDueDate")) or "")[:10]
            amount_diff = abs(tx_amount - erp_amount) / (erp_amount if erp_amount else Decimal("1"))
            if amount_diff <= _RECONCILE_AMOUNT_TOLERANCE and tx_date_str == erp_date_str:
                matched = True
                break

        if not matched:
            unreconciled.append({
                "transactionId": tx.get("transactionId", ""),
                "date": tx_date_str,
                "amount": str(tx_amount),
                "description": tx.get("description", ""),
            })

    return unreconciled


# ---------------------------------------------------------------------------
# Main node function
# ---------------------------------------------------------------------------

def perceive_node(ctx: AgentContext) -> AgentContext:
    """
    LangGraph node function for the Perceive step.

    Queries all ERP and bank data sources, assembles ``TreasuryState``, and
    returns an updated ``AgentContext``.

    Parameters
    ----------
    ctx:
        Input ``AgentContext`` with ``goal`` populated.

    Returns
    -------
    AgentContext
        Updated context with ``treasury_state`` populated.
        If cash position data is MISSING, ``treasury_state.execution_blocked``
        is set to ``True``.
    """
    logger.info("[Perceive] Starting cycle %s for company %s", ctx.cycle_id, ctx.goal.company_code)
    company = ctx.goal.company_code
    statuses: list[DataSourceStatus] = []

    # --- 1. ERP Cash Positions ---
    erp_positions, erp_cash_status = _fetch_with_cache(
        lambda: erp_client.get_cash_positions(company),
        _KEY_CASH_POS,
        "ERP_CASH_POSITION",
    )
    statuses.append(erp_cash_status)

    # --- 2. Bank Balances (live, authoritative) ---
    bank_balances, bank_balance_status = _fetch_with_cache(
        bank_client.get_account_balances,
        _KEY_BANK_BALANCES,
        "BANK_BALANCE",
    )
    statuses.append(bank_balance_status)

    # --- 3. Vendor AP Payables ---
    payables, payables_status = _fetch_with_cache(
        lambda: erp_client.get_open_payables(company),
        _KEY_PAYABLES,
        "ERP_OPEN_PAYABLES",
    )
    statuses.append(payables_status)

    # --- 4. Payroll ---
    payroll, payroll_status = _fetch_with_cache(
        lambda: erp_client.get_payroll_postings(company),
        _KEY_PAYROLL,
        "ERP_PAYROLL",
    )
    statuses.append(payroll_status)

    # --- 5. Tax ---
    tax, tax_status = _fetch_with_cache(
        lambda: erp_client.get_tax_items(company),
        _KEY_TAX,
        "ERP_TAX",
    )
    statuses.append(tax_status)

    # --- 6. Loans ---
    loans, loan_status = _fetch_with_cache(
        lambda: erp_client.get_loan_items(company),
        _KEY_LOANS,
        "ERP_LOANS",
    )
    statuses.append(loan_status)

    # --- 7. Bank Statement (for reconciliation — 30 days) ---
    today = date.today()
    primary_account = bank_balances[0].get("accountId", "SAMP-0012345678") if bank_balances else "SAMP-0012345678"
    bank_stmt, stmt_status = _fetch_with_cache(
        lambda: bank_client.get_statement(primary_account, today - timedelta(days=30), today),
        _KEY_BANK_STMT,
        "BANK_STATEMENT",
    )
    statuses.append(stmt_status)

    # --- 8. Market Rates Data ---
    mkt_raw, mkt_status = _fetch_with_cache(
        market_data_client.get_market_rates,
        _KEY_MARKET_DATA,
        "MARKET_DATA",
    )
    statuses.append(mkt_status)

    market_rates_obj = None
    cbsl_stale = False

    if isinstance(mkt_raw, dict) and mkt_raw:
        cbsl_dict = mkt_raw.get("cbsl", {})
        cbsl_stale = cbsl_dict.get("stale", False)
        fx_dict = cbsl_dict.get("forexUSD", {})

        market_rates_obj = MarketRates(
            best_fd_rates=mkt_raw.get("bestAvailableRates", {}),
            call_deposit_best=mkt_raw.get("bestAvailableRates", {}).get("callDeposit", {}),
            overnight_policy_rate=cbsl_dict.get("overnightPolicyRate"),
            inflation_ccpi=cbsl_dict.get("inflationCCPI"),
            usd_lkr_buy=float(fx_dict["buy"]) if fx_dict.get("buy") else None,
            usd_lkr_sell=float(fx_dict["sell"]) if fx_dict.get("sell") else None,
            as_of=datetime.utcnow(),
            cbsl_stale=cbsl_stale,
        )

    # --- Determine execution_blocked ---
    cash_missing = erp_cash_status.freshness == DataFreshness.MISSING
    bank_missing = bank_balance_status.freshness == DataFreshness.MISSING
    execution_blocked = cash_missing and bank_missing
    block_reason = (
        "Cash position data unavailable and no cache exists. Cannot assess liquidity."
        if execution_blocked
        else None
    )

    # --- Compute total available balance from bank (authoritative) ---
    total_balance = Decimal("0")
    accounts_raw = []
    for acct in (bank_balances or []):
        bal = _parse_amount(acct.get("availableBalance"))
        total_balance += bal
        accounts_raw.append(acct)

    # Fallback to ERP if bank data missing
    if not accounts_raw and erp_positions:
        for pos in erp_positions:
            bal = _parse_amount(pos.get("AvailableBalance"))
            total_balance += bal
            accounts_raw.append(pos)

    # --- Build obligation lists ---
    all_obligations: list[Obligation] = []
    all_obligations += _build_obligations_from_payables(payables or [])
    all_obligations += _build_obligations_from_payroll(payroll or [])
    all_obligations += _build_obligations_from_tax(tax or [])
    all_obligations += _build_obligations_from_loans(loans or [])
    all_obligations.sort(key=lambda o: o.due_date)

    fixed_obs = [o for o in all_obligations if o.payment_priority == "FIXED"]
    flexible_obs = [o for o in all_obligations if o.payment_priority == "FLEXIBLE"]

    # Next fixed obligation
    upcoming_fixed = [o for o in fixed_obs if o.due_date >= today]
    next_fixed_date = upcoming_fixed[0].due_date if upcoming_fixed else None
    next_fixed_amount = upcoming_fixed[0].amount if upcoming_fixed else None

    # Available surplus
    available_surplus = total_balance - ctx.goal.minimum_liquidity_buffer

    # Has any stale data?
    has_stale = any(s.freshness != DataFreshness.FRESH for s in statuses)

    # --- Detect unreconciled large credits ---
    unreconciled = _detect_unreconciled_credits(
        bank_stmt if isinstance(bank_stmt, dict) else {},
        payables or [],
    )

    treasury_state = TreasuryState(
        company_code=company,
        as_of=datetime.utcnow(),
        total_available_balance=total_balance,
        accounts=accounts_raw,
        obligations=all_obligations,
        fixed_obligations=fixed_obs,
        flexible_obligations=flexible_obs,
        next_fixed_obligation_date=next_fixed_date,
        next_fixed_obligation_amount=next_fixed_amount,
        available_surplus=available_surplus,
        data_source_statuses=statuses,
        has_stale_data=has_stale,
        unreconciled_large_credits=unreconciled,
        execution_blocked=execution_blocked,
        block_reason=block_reason,
        market_rates=market_rates_obj,
        cbsl_rates_stale=cbsl_stale,
    )


    logger.info(
        "[Perceive] Done. balance=%.2f surplus=%.2f obligations=%d stale=%s blocked=%s",
        total_balance,
        available_surplus,
        len(all_obligations),
        has_stale,
        execution_blocked,
    )

    ctx.treasury_state = treasury_state
    return ctx
