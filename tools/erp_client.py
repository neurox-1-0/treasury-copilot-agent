"""
agent/tools/erp_client.py
=========================

HTTP client wrapping the Mock SAP ERP service (Component 1).

Purpose
-------
Provides typed, retry-hardened methods for every ERP data source that the
Perceive node needs.  All methods use ``tenacity`` for retry logic and fall
back to ``agent/memory/cache.py`` on failure.

Service
-------
Base URL: ``http://localhost:8001`` (configurable via ``ERP_BASE_URL`` env var).
Protocol: SAP OData v2-shaped JSON endpoints (no auth required on mock).

Retry strategy
--------------
3 attempts, exponential back-off (1 s, 2 s, 4 s), on any ``httpx`` error or
5xx response.  On final failure, raises ``ERPClientError`` so the Perceive node
can handle the stale-data path.

Methods
-------
- ``get_cash_positions()`` — bank account balances from ERP ledger
- ``get_open_payables()`` — vendor AP documents
- ``get_payroll_postings()`` — upcoming payroll obligations
- ``get_tax_items()`` — statutory tax liabilities
- ``get_loan_items()`` — loan installment schedule
- ``get_bank_statement_credits()`` — large credit transactions (for reconciliation)
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

_ERP_BASE_URL = os.getenv("ERP_BASE_URL", "http://localhost:8001")
_SERVICE_BASE = "/sap/opu/odata/sap"
_TIMEOUT = 10.0  # seconds


class ERPClientError(Exception):
    """Raised when all retry attempts to the ERP service have been exhausted."""


def _make_retry_decorator():
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
        reraise=True,
    )


def _erp_get(path: str, params: dict | None = None) -> list[dict]:
    """
    Perform a GET request to the ERP mock and return the ``value`` list.

    Parameters
    ----------
    path:
        Full path after the base URL, e.g.
        ``"/sap/opu/odata/sap/ZAPI_CASH_POSITION_SRV/A_CashPosition"``.
    params:
        Optional OData query parameters.

    Returns
    -------
    list[dict]
        The ``d.results`` array from the OData response.

    Raises
    ------
    ERPClientError
        If all retry attempts fail.
    """
    url = f"{_ERP_BASE_URL}{path}"
    try:
        _guarded_get = _make_retry_decorator()(_raw_get)
        return _guarded_get(url, params)
    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        raise ERPClientError(f"ERP request failed for {path}: {exc}") from exc


def _raw_get(url: str, params: dict | None) -> list[dict]:
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.get(url, params=params or {})
        resp.raise_for_status()
        body = resp.json()
        # OData v2 wraps results in d.results
        if "d" in body and "results" in body["d"]:
            return body["d"]["results"]
        # Fallback: direct list
        if isinstance(body, list):
            return body
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_cash_positions(company_code: str = "1000") -> list[dict]:
    """
    Fetch ERP cash position records.

    Returns the list of bank account balance records from the ERP ledger.
    These are the ERP's *internal* view of balances (may differ from live
    bank balances; reconciliation happens in the Perceive node).

    Parameters
    ----------
    company_code:
        SAP company code filter.

    Returns
    -------
    list[dict]
        Each dict has keys: ``BankAccount``, ``AvailableBalance``,
        ``AccountType``, ``CompanyCode``, ``BankKey``.

    Raises
    ------
    ERPClientError
        On service failure after 3 retries.
    """
    return _erp_get(
        f"{_SERVICE_BASE}/ZAPI_CASH_POSITION_SRV/A_CashPosition",
        params={"$filter": f"CompanyCode eq '{company_code}'", "$format": "json"},
    )


def get_open_payables(company_code: str = "1000") -> list[dict]:
    """
    Fetch open vendor AP (Accounts Payable) documents.

    Returns vendor invoices with status ``OPEN`` that have not yet been paid.

    Parameters
    ----------
    company_code:
        SAP company code filter.

    Returns
    -------
    list[dict]
        Each dict has keys: ``AccountingDocument``, ``Vendor``,
        ``AmountInCompanyCodeCurrency``, ``NetDueDate``, ``PaymentPriority``.

    Raises
    ------
    ERPClientError
        On service failure after 3 retries.
    """
    return _erp_get(
        f"{_SERVICE_BASE}/API_ACCOUNTINGDOCUMENTITEM_SRV/A_APAccountingDocumentItem",
        params={
            "$filter": f"CompanyCode eq '{company_code}' and ClearingStatus eq 'OPEN'",
            "$format": "json",
        },
    )


def get_payroll_postings(company_code: str = "1000") -> list[dict]:
    """
    Fetch upcoming payroll payment postings.

    Payroll is always ``PaymentPriority = FIXED`` and cannot be deferred.

    Parameters
    ----------
    company_code:
        SAP company code filter.

    Returns
    -------
    list[dict]
        Each dict has keys: ``AccountingDocument``, ``TotalNetPayable``,
        ``PaymentDueDate``, ``PaymentPriority``, ``PayrollArea``.

    Raises
    ------
    ERPClientError
        On service failure after 3 retries.
    """
    return _erp_get(
        f"{_SERVICE_BASE}/ZAPI_PAYROLL_POSTING_SRV/A_PayrollPosting",
        params={"$filter": f"CompanyCode eq '{company_code}'", "$format": "json"},
    )


def get_tax_items(company_code: str = "1000") -> list[dict]:
    """
    Fetch statutory tax liability items.

    Covers VAT, WHT, EPF, ETF, and corporate tax installments.  All are
    ``PaymentPriority = FIXED``.

    Parameters
    ----------
    company_code:
        SAP company code filter.

    Returns
    -------
    list[dict]
        Each dict has keys: ``AccountingDocument``, ``TaxType``,
        ``AmountInCompanyCodeCurrency``, ``StatutoryDueDate``, ``PaymentPriority``.

    Raises
    ------
    ERPClientError
        On service failure after 3 retries.
    """
    return _erp_get(
        f"{_SERVICE_BASE}/ZAPI_TAX_LIABILITY_SRV/A_TaxLiabilityItem",
        params={"$filter": f"CompanyCode eq '{company_code}'", "$format": "json"},
    )


def get_loan_items(company_code: str = "1000") -> list[dict]:
    """
    Fetch loan installment schedule items.

    Loan installments flagged with ``CovenantFlag = True`` are treated as
    ``PaymentPriority = FIXED`` even if the base type is ``FLEXIBLE``.

    Parameters
    ----------
    company_code:
        SAP company code filter.

    Returns
    -------
    list[dict]
        Each dict has keys: ``LoanContract``, ``TotalInstallmentAmount``,
        ``InstallmentDueDate``, ``PaymentPriority``, ``CovenantFlag``.

    Raises
    ------
    ERPClientError
        On service failure after 3 retries.
    """
    return _erp_get(
        f"{_SERVICE_BASE}/ZAPI_LOAN_SCHEDULE_SRV/A_LoanContractItem",
        params={"$filter": f"CompanyCode eq '{company_code}'", "$format": "json"},
    )


# ---------------------------------------------------------------------------
# Resilient API — cache-fallback variants (return 3-tuple, never raise)
# ---------------------------------------------------------------------------
#
# These are used by the Perceive node.  The original functions above raise on
# failure; these wrap them with the standard cache-fallback pattern from
# docs/workplan-v1/07-failure-handling-resilience.md.
#
# Return type: tuple[list[dict] | None, DataFreshness, datetime | None]
#   - data:         the payload (fresh or cached), or None if MISSING
#   - freshness:    DataFreshness.FRESH | STALE | MISSING
#   - last_fresh_at: UTC datetime, or None if MISSING


def get_cash_positions_resilient(
    company_code: str = "1000",
) -> "tuple[list[dict] | None, Any, Any]":
    """
    Fetch ERP cash positions with automatic cache fallback.

    Three outcomes:

    - **FRESH**: Live fetch succeeded; cache is updated.
    - **STALE**: Fetch failed; cached data returned.
    - **MISSING**: Fetch failed AND no cache exists.
      The Perceive node sets ``execution_blocked = True`` for MISSING cash
      position — the agent cannot assess liquidity without this data.

    Parameters
    ----------
    company_code:
        SAP company code filter.

    Returns
    -------
    tuple[list[dict] | None, DataFreshness, datetime | None]
        ``(data, freshness, last_fresh_at)``
    """
    from datetime import datetime as _dt

    from agent.memory.cache import cache_set as _cache_set
    from agent.resilience import handle_erp_failure
    from agent.state import DataFreshness

    cache_key = f"ERP_CASH_POSITION_{company_code}"
    try:
        data = get_cash_positions(company_code)
        _cache_set(cache_key, data)
        return data, DataFreshness.FRESH, _dt.utcnow()
    except (ERPClientError, httpx.RequestError, httpx.HTTPStatusError) as exc:
        return handle_erp_failure(cache_key, exc, "ERP cash positions")


def get_open_payables_resilient(
    company_code: str = "1000",
) -> "tuple[list[dict] | None, Any, Any]":
    """
    Fetch open payables with cache fallback.

    Stale payables are recoverable — used for obligation prioritisation, not
    for blocking liquidity decisions.

    Returns
    -------
    tuple[list[dict] | None, DataFreshness, datetime | None]
    """
    from datetime import datetime as _dt

    from agent.memory.cache import cache_set as _cache_set
    from agent.resilience import handle_erp_failure
    from agent.state import DataFreshness

    cache_key = f"ERP_OPEN_PAYABLES_{company_code}"
    try:
        data = get_open_payables(company_code)
        _cache_set(cache_key, data)
        return data, DataFreshness.FRESH, _dt.utcnow()
    except (ERPClientError, httpx.RequestError, httpx.HTTPStatusError) as exc:
        return handle_erp_failure(cache_key, exc, "ERP open payables")


def get_payroll_postings_resilient(
    company_code: str = "1000",
) -> "tuple[list[dict] | None, Any, Any]":
    """
    Fetch payroll postings with cache fallback.

    Returns
    -------
    tuple[list[dict] | None, DataFreshness, datetime | None]
    """
    from datetime import datetime as _dt

    from agent.memory.cache import cache_set as _cache_set
    from agent.resilience import handle_erp_failure
    from agent.state import DataFreshness

    cache_key = f"ERP_PAYROLL_{company_code}"
    try:
        data = get_payroll_postings(company_code)
        _cache_set(cache_key, data)
        return data, DataFreshness.FRESH, _dt.utcnow()
    except (ERPClientError, httpx.RequestError, httpx.HTTPStatusError) as exc:
        return handle_erp_failure(cache_key, exc, "ERP payroll postings")

