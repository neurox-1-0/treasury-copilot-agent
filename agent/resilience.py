"""
agent/resilience.py
====================

Cross-cutting failure handling utilities for the Treasury Copilot Agent.

This module is the **single source of truth** for all failure policies.
It is imported by tool clients, LangGraph nodes, and the timeout checker —
never the reverse.

Sections
--------
1. ``initiate_payment_safe()``
   A no-retry payment wrapper that maps bank HTTP errors to
   ``PaymentWriteStatus`` values.  This is the most safety-critical
   function in the codebase — payment retries risk double-debiting.

2. ``handle_erp_failure()``
   Shared handler for ERP read failures: falls back to cache and
   returns a properly typed 3-tuple so callers never need a bare
   ``except: pass``.

3. ``handle_bank_read_failure()``
   Same pattern for bank balance/statement reads.

4. ``NOTIFICATION_WEBHOOK_URL``
   Where approval timeout and UNKNOWN payment notifications are posted.

Design principle
-----------------
Every external call has three defined outcomes:
  - "I tried and succeeded"   → data, DataFreshness.FRESH, now
  - "I tried and failed — used fallback"  → cached_data, DataFreshness.STALE, last_fresh_at
  - "I tried and have nothing"            → None, DataFreshness.MISSING, None

These three states have very different downstream consequences when real
money is involved.  None may be silently swallowed.

References
-----------
spec: ``docs/workplan-v1/07-failure-handling-resilience.md``
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from agent.memory.cache import cache_get, cache_set
from agent.state import DataFreshness, PaymentWriteStatus

logger = logging.getLogger(__name__)

NOTIFICATION_WEBHOOK_URL = os.getenv(
    "NOTIFICATION_WEBHOOK_URL",
    "http://localhost:9000/webhook",  # default for local dev / test
)

_BANK_BASE_URL = os.getenv("BANK_BASE_URL", "http://localhost:8002")
_CLIENT_ID = os.getenv("BANK_CLIENT_ID", "treasury-agent")
_CLIENT_SECRET = os.getenv("BANK_CLIENT_SECRET", "demo-secret-1234")
_SIGNING_SECRET = os.getenv("PAYMENT_SIGNING_SECRET", "dev-signing-secret")


# ---------------------------------------------------------------------------
# Payment write — the most safety-critical function
# ---------------------------------------------------------------------------


async def initiate_payment_safe(
    *,
    source_account_id: str,
    beneficiary_account: str,
    amount: Decimal,
    currency: str,
    purpose: str,
    requested_execution_date: date,
    reference_note: str | None = None,
    token: str = "demo-token",
) -> tuple[str | None, PaymentWriteStatus]:

    """
    Initiate a bank payment and return a typed ``(payment_id, status)`` tuple.

    **Safety contract: this function is called exactly once per proposal.
    It never retries on any failure.  Retrying a payment POST risks
    double-debiting the account.**

    Parameters
    ----------
    source_account_id:
        Debit account (e.g. ``"SAMP-0012345678"``).
    beneficiary_account:
        Credit account (e.g. ``"COMB-0098765432"``).
    amount:
        Payment amount.
    currency:
        ISO 4217 code (``"LKR"``).
    purpose:
        Payment purpose code / description.
    requested_execution_date:
        Requested value date.
    reference_note:
        Optional free-text note for auditing.
    token:
        A valid OAuth2 bearer token (caller responsible for acquiring it).

    Returns
    -------
    tuple[str | None, PaymentWriteStatus]
        ``(payment_id, status)`` where status is one of:

        - ``SUBMITTED``  — bank accepted; caller must poll for EXECUTED.
        - ``REJECTED``   — bank definitively rejected (no money moved).
        - ``UNKNOWN``    — timeout or 5xx; money may or may not have moved.

    Notes
    -----
    On ``UNKNOWN`` the Report node must:
    1. Set ``payment_status = "UNKNOWN"`` in the audit log.
    2. Show "Manual bank verification required" on the dashboard.
    3. Send a webhook notification.
    4. **Not retry. Not guess. Not close the cycle.**
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
    signature = hmac.new(
        _SIGNING_SECRET.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_BANK_BASE_URL}/payments/initiate",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-Signature": signature,
                },
            )

            if resp.status_code == 200:
                body = resp.json()
                payment_id = body.get("paymentId")
                logger.info(
                    "[resilience] Payment submitted: paymentId=%s", payment_id
                )
                return payment_id, PaymentWriteStatus.SUBMITTED

            if resp.status_code in (400, 422):
                # Definitive rejection — money did NOT move
                logger.warning(
                    "[resilience] Payment rejected (HTTP %s): %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None, PaymentWriteStatus.REJECTED

            # 5xx or unexpected status
            logger.error(
                "[resilience] Payment initiation returned HTTP %s — status UNKNOWN.",
                resp.status_code,
            )
            return None, PaymentWriteStatus.UNKNOWN

    except httpx.TimeoutException:
        # The request may or may not have reached the bank — treat as UNKNOWN
        logger.error(
            "[resilience] Payment initiation timed out — status UNKNOWN. "
            "Manual bank verification required."
        )
        return None, PaymentWriteStatus.UNKNOWN

    except httpx.RequestError as exc:
        # Network-level failure (DNS, connection refused, etc.)
        logger.error("[resilience] Payment initiation network error: %s", exc)
        return None, PaymentWriteStatus.UNKNOWN


# ---------------------------------------------------------------------------
# ERP read resilience helper
# ---------------------------------------------------------------------------


def handle_erp_failure(
    cache_key: str,
    exc: Exception,
    source_label: str,
) -> tuple[Any | None, DataFreshness, datetime | None]:
    """
    Handle an ERP read failure by falling back to the cache.

    Called by ERP client functions when all retry attempts are exhausted.

    Parameters
    ----------
    cache_key:
        The cache key used for this data source (e.g.
        ``"ERP_CASH_POSITION_1000"``).
    exc:
        The exception that caused the failure (for logging).
    source_label:
        Human-readable label for log messages (e.g. ``"ERP cash positions"``).

    Returns
    -------
    tuple[Any | None, DataFreshness, datetime | None]
        ``(data, freshness, last_fresh_at)`` where freshness is either
        ``STALE`` (cache hit) or ``MISSING`` (no cache entry).
    """
    logger.warning(
        "[resilience] %s fetch failed (%s) — falling back to cache key '%s'.",
        source_label,
        type(exc).__name__,
        cache_key,
    )
    cached_data, freshness, last_fresh_at = cache_get(cache_key)
    if freshness == DataFreshness.MISSING:
        logger.error(
            "[resilience] %s: no cached data available. "
            "DataFreshness.MISSING will be returned.",
            source_label,
        )
    return cached_data, freshness, last_fresh_at


# ---------------------------------------------------------------------------
# Bank read resilience helper
# ---------------------------------------------------------------------------


def handle_bank_read_failure(
    cache_key: str,
    exc: Exception,
    source_label: str,
) -> tuple[Any | None, DataFreshness, datetime | None]:
    """
    Handle a bank read failure by falling back to the cache.

    Identical contract to ``handle_erp_failure`` — same pattern, different
    source label for log clarity.

    If bank balance is stale the Confidence Check node adds
    ``"BANK_BALANCE_STALE"`` to conflict flags and routes to Disambiguate.

    Parameters
    ----------
    cache_key:
        Cache key for this bank data source.
    exc:
        The triggering exception.
    source_label:
        Human-readable source name for log messages.

    Returns
    -------
    tuple[Any | None, DataFreshness, datetime | None]
        ``(data, freshness, last_fresh_at)``
    """
    logger.warning(
        "[resilience] %s fetch failed (%s) — falling back to cache key '%s'.",
        source_label,
        type(exc).__name__,
        cache_key,
    )
    return cache_get(cache_key)


# ---------------------------------------------------------------------------
# Webhook notification
# ---------------------------------------------------------------------------


async def send_notification(event: str, payload: dict) -> None:
    """
    POST a structured event notification to the configured webhook URL.

    Used for approval timeouts and UNKNOWN payment states.  Non-blocking:
    failure to deliver the webhook is logged but does not raise an exception
    (the audit log is the source of truth, not the webhook).

    Parameters
    ----------
    event:
        Event type string, e.g. ``"APPROVAL_TIMEOUT"`` or
        ``"PAYMENT_STATUS_UNKNOWN"``.
    payload:
        Additional context included in the notification body.
    """
    body = {"event": event, "timestamp": datetime.utcnow().isoformat(), **payload}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(NOTIFICATION_WEBHOOK_URL, json=body)
            if resp.status_code == 200:
                logger.info("[resilience] Notification sent: event=%s", event)
            else:
                logger.warning(
                    "[resilience] Webhook returned HTTP %s for event=%s",
                    resp.status_code,
                    event,
                )
    except httpx.RequestError as exc:
        logger.warning(
            "[resilience] Webhook delivery failed for event=%s: %s", event, exc
        )


# ---------------------------------------------------------------------------
# Sensitive Data Governance
# ---------------------------------------------------------------------------


def mask_account(account_id: str | None) -> str:
    """
    Mask bank account number to show only the last 4 characters.

    Examples
    --------
    "SAMP-0012345678" -> "SAMP-****5678"
    "12345678"        -> "****5678"
    "12"              -> "12"
    """
    if not account_id:
        return ""
    if "-" in account_id:
        prefix, num = account_id.rsplit("-", 1)
        if len(num) > 4:
            masked = "*" * (len(num) - 4) + num[-4:]
            return f"{prefix}-{masked}"
    if len(account_id) > 4:
        return "*" * (len(account_id) - 4) + account_id[-4:]
    return account_id


def _scrub_headers(headers: dict[str, str]) -> dict[str, str]:
    """
    Return a copy of request headers with sensitive credentials redacted.
    """
    sensitive_keys = {"authorization", "api-key", "secret", "x-api-key", "bearer"}
    scrubbed = {}
    for key, val in headers.items():
        if key.lower() in sensitive_keys:
            scrubbed[key] = "[REDACTED]"
        else:
            scrubbed[key] = val
    return scrubbed

