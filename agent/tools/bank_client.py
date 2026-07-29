"""
agent/tools/bank_client.py
==========================

HTTP client wrapping the Mock Sampath-Style Bank API (Component 2).

Purpose
-------
Provides typed, token-aware methods for every bank operation the agent needs.
Handles OAuth2 client-credentials token acquisition and auto-refresh transparently.

Service
-------
Base URL: ``http://localhost:8002`` (configurable via ``BANK_BASE_URL`` env var).
Auth: OAuth2 client-credentials (``client_id=treasury-agent``,
      ``client_secret=demo-secret-1234`` — configurable via env vars).

Token refresh
-------------
The ``_get_token()`` function is called lazily before each request.  If the
cached token is expired (or not yet acquired), a new one is fetched.  This
is thread-safe for single-threaded async use.

Payment signing
---------------
``initiate_payment`` computes an HMAC-SHA256 signature over the canonical
request body and attaches it as the ``X-Signature`` header, matching the
bank mock's verification logic.

Methods
-------
- ``get_account_balances()`` — list all accounts and balances
- ``get_statement(account_id, from_date, to_date)`` — account statement
- ``get_deposit_rates()`` — current deposit rate sheet
- ``initiate_payment(...)`` — submit a payment instruction
- ``get_payment_status(payment_id)`` — poll payment status
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BANK_BASE_URL = os.getenv("BANK_BASE_URL", "http://localhost:8002")
_CLIENT_ID = os.getenv("BANK_CLIENT_ID", "treasury-agent")
_CLIENT_SECRET = os.getenv("BANK_CLIENT_SECRET", "demo-secret-1234")
_SIGNING_SECRET = os.getenv("PAYMENT_SIGNING_SECRET", "dev-signing-secret")
_TIMEOUT = 15.0


class BankClientError(Exception):
    """Raised when the bank API returns an error or is unreachable."""


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

_token_cache: dict[str, Any] = {"token": None, "expires_at": None}


def _get_token() -> str:
    """
    Return a valid OAuth2 Bearer token, fetching a new one if needed.

    The token is cached in-process.  On expiry or first call, a new token
    is obtained from ``/auth/token``.

    Returns
    -------
    str
        The Bearer token string.

    Raises
    ------
    BankClientError
        If the token endpoint is unreachable or returns a non-200 status.
    """
    now = datetime.now(timezone.utc)
    if _token_cache["token"] and _token_cache["expires_at"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(
                f"{_BANK_BASE_URL}/auth/token",
                json={"client_id": _CLIENT_ID, "client_secret": _CLIENT_SECRET},
            )
            resp.raise_for_status()
            body = resp.json()
            _token_cache["token"] = body["access_token"]
            # expires_in is in seconds; cache with 60-second safety margin
            from datetime import timedelta
            _token_cache["expires_at"] = now + timedelta(seconds=body.get("expires_in", 3600) - 60)
            return _token_cache["token"]
    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        raise BankClientError(f"Bank token fetch failed: {exc}") from exc


def _bank_get(path: str, params: dict | None = None) -> Any:
    """Issue an authenticated GET to the bank API."""
    token = _get_token()
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                f"{_BANK_BASE_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params or {},
            )
            resp.raise_for_status()
            return resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        raise BankClientError(f"Bank GET {path} failed: {exc}") from exc


def _bank_post(path: str, payload: dict, extra_headers: dict | None = None) -> Any:
    """Issue an authenticated POST to the bank API."""
    token = _get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(
                f"{_BANK_BASE_URL}{path}",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        raise BankClientError(f"Bank POST {path} failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_account_balances() -> list[dict]:
    """
    Fetch all bank accounts and their current balances.

    Calls ``GET /accounts`` then ``GET /accounts/{id}/balance`` for each
    account and assembles a unified list.

    Returns
    -------
    list[dict]
        Each dict has: ``accountId``, ``currency``, ``accountType``,
        ``availableBalance``, ``bookBalance``.

    Raises
    ------
    BankClientError
        If the bank API is unreachable.
    """
    accounts_resp = _bank_get("/accounts")
    accounts = accounts_resp.get("accounts", [])
    balances = []
    for acct in accounts:
        try:
            bal = _bank_get(f"/accounts/{acct['accountId']}/balance")
            balances.append({**acct, **bal})
        except BankClientError:
            logger.warning("Could not fetch balance for account %s", acct.get("accountId"))
    return balances


def get_statement(account_id: str, from_date: date, to_date: date) -> dict:
    """
    Fetch the account statement for a given date range.

    Used by the Perceive node to detect unreconciled large credits.

    Parameters
    ----------
    account_id:
        Bank account identifier, e.g. ``"SAMP-0012345678"``.
    from_date:
        Inclusive start date.
    to_date:
        Inclusive end date.

    Returns
    -------
    dict
        Statement response with ``transactions`` list.

    Raises
    ------
    BankClientError
        If the bank API is unreachable.
    """
    return _bank_get(
        f"/accounts/{account_id}/statement",
        params={"fromDate": from_date.isoformat(), "toDate": to_date.isoformat()},
    )


def get_deposit_rates() -> dict:
    """
    Fetch the current term deposit rate sheet.

    Returns the ``DepositRatesResponse`` dict used by the optimizer to build
    the instrument list.

    Returns
    -------
    dict
        ``{"rates": [{"bank": ..., "type": ..., "termDays": ..., "rate": ...}]}``.

    Raises
    ------
    BankClientError
        If the bank API is unreachable.
    """
    return _bank_get("/rates/deposits")


def initiate_payment(
    *,
    source_account_id: str,
    beneficiary_account: str,
    amount: Decimal,
    currency: str,
    purpose: str,
    requested_execution_date: date,
    reference_note: str | None = None,
) -> dict:
    """
    Submit a payment instruction to the bank.

    Computes the HMAC-SHA256 ``X-Signature`` over the canonical JSON body,
    matching the bank mock's verification logic.

    Parameters
    ----------
    source_account_id:
        Debit account (must exist in bank mock).
    beneficiary_account:
        Credit account (must start with ``"COMB-"`` in the mock).
    amount:
        Payment amount in ``currency``.
    currency:
        ISO 4217 code (``"LKR"``).
    purpose:
        Payment purpose code / description.
    requested_execution_date:
        Requested value date.
    reference_note:
        Optional free-text note attached to the payment.

    Returns
    -------
    dict
        ``PaymentInitiateResponse`` with ``paymentId``, ``status``.

    Raises
    ------
    BankClientError
        If the payment is rejected or the bank API is unreachable.
    """
    payload = {
        "sourceAccountId": source_account_id,
        "beneficiaryAccount": beneficiary_account,
        "amount": str(amount),
        "currency": currency,
        "purpose": purpose,
        "requestedExecutionDate": requested_execution_date.isoformat(),
    }
    if reference_note:
        payload["referenceNote"] = reference_note

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(_SIGNING_SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()

    return _bank_post("/payments/initiate", payload, extra_headers={"X-Signature": signature})


def get_payment_status(payment_id: str) -> dict:
    """
    Poll the status of a previously initiated payment.

    Returns the current ``status``: ``"PENDING_APPROVAL"`` | ``"APPROVED"`` |
    ``"EXECUTED"`` | ``"FAILED"``.

    Parameters
    ----------
    payment_id:
        Bank-issued payment reference (e.g. ``"PMT-20260720123456"``).

    Returns
    -------
    dict
        ``{"paymentId": ..., "status": ..., "executedAt": ...}``.

    Raises
    ------
    BankClientError
        If the payment is not found or the bank API is unreachable.
    """
    return _bank_get(f"/payments/{payment_id}/status")


# ---------------------------------------------------------------------------
# Resilient API — cache-fallback for reads; safe wrapper for writes
# ---------------------------------------------------------------------------


def get_account_balances_resilient() -> "tuple[list[dict] | None, Any, Any]":
    """
    Fetch account balances with cache fallback.

    If the bank API is unreachable, returns the most recently cached balances.
    Stale bank balance adds ``"BANK_BALANCE_STALE"`` to conflict flags in the
    Confidence Check node, which routes to Disambiguate.

    Returns
    -------
    tuple[list[dict] | None, DataFreshness, datetime | None]
        ``(balances, freshness, last_fresh_at)``
    """
    from datetime import datetime as _dt

    from agent.memory.cache import cache_set as _cache_set
    from agent.resilience import handle_bank_read_failure
    from agent.state import DataFreshness

    cache_key = "BANK_BALANCE"
    try:
        data = get_account_balances()
        _cache_set(cache_key, data)
        return data, DataFreshness.FRESH, _dt.utcnow()
    except BankClientError as exc:
        return handle_bank_read_failure(cache_key, exc, "bank account balances")


async def initiate_payment_safe(
    *,
    source_account_id: str,
    beneficiary_account: str,
    amount: "Decimal",
    currency: str,
    purpose: str,
    requested_execution_date: "date",
    reference_note: str | None = None,
) -> "tuple[str | None, Any]":
    """
    Submit a payment instruction and return a typed ``(payment_id, status)`` tuple.

    **No retry — ever.**  This is the most safety-critical policy in the system.
    Retrying a payment POST risks double-debiting the account.

    Delegates to ``agent.resilience.initiate_payment_safe`` which handles
    OAuth token acquisition, HMAC signing, and the full error-to-status mapping.

    Parameters
    ----------
    source_account_id:
        Debit account, e.g. ``"SAMP-0012345678"``.
    beneficiary_account:
        Credit account, e.g. ``"COMB-0098765432"``.
    amount:
        Payment amount as ``Decimal``.
    currency:
        ISO 4217 code (``"LKR"``).
    purpose:
        Payment purpose / description.
    requested_execution_date:
        Requested value date.
    reference_note:
        Optional audit reference.

    Returns
    -------
    tuple[str | None, PaymentWriteStatus]
        - ``(payment_id, PaymentWriteStatus.SUBMITTED)`` on success
        - ``(None, PaymentWriteStatus.REJECTED)`` on definitive rejection
        - ``(None, PaymentWriteStatus.UNKNOWN)`` on timeout/5xx
    """
    from agent.resilience import initiate_payment_safe as _safe

    token = _get_token()
    return await _safe(
        source_account_id=source_account_id,
        beneficiary_account=beneficiary_account,
        amount=amount,
        currency=currency,
        purpose=purpose,
        requested_execution_date=requested_execution_date,
        reference_note=reference_note,
        token=token,
    )

