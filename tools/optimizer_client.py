"""
agent/tools/optimizer_client.py
================================

HTTP client wrapping the SciPy Optimizer Service (Component 4).

Purpose
-------
Provides the Reason node with an optimal surplus allocation recommendation
given the current treasury state, available instruments, and feedback-loop
constraints (``max_term_days``).

Service
-------
Base URL: ``http://localhost:8004`` (configurable via ``OPTIMIZER_BASE_URL``).
Protocol: JSON POST to ``/optimize`` — no auth required.

Usage in Reason node
--------------------
::

    from agent.tools.optimizer_client import get_allocation
    from agent.tools.bank_client import get_deposit_rates

    rates = get_deposit_rates()
    instruments = [
        i for i in rates.get("rates", [])
        if i["termDays"] <= feedback_adjustments.max_term_days
    ]
    result = get_allocation(treasury_state, instruments)
    confidence = result["constraintsSatisfied"]
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any

import httpx

from agent.state import TreasuryState

logger = logging.getLogger(__name__)

_OPTIMIZER_BASE_URL = os.getenv("OPTIMIZER_BASE_URL", "http://localhost:8004")
_TIMEOUT = 15.0


class OptimizerClientError(Exception):
    """Raised when the optimizer service is unreachable or returns an error."""


def get_allocation(treasury_state: TreasuryState, instruments: list[dict]) -> dict:
    """
    Request an optimal surplus allocation from the SciPy Optimizer Service.

    Builds the ``OptimizationRequest`` from the current ``TreasuryState``
    and the (feedback-adjusted) instrument list, then calls ``POST /optimize``.

    Parameters
    ----------
    treasury_state:
        Current ``TreasuryState`` from the Perceive node.
    instruments:
        List of available deposit instruments (already filtered by
        ``FeedbackAdjustments.max_term_days``).  Each item must have:
        ``bank`` (str), ``type`` (str), ``termDays`` (int), ``rate`` (float).

    Returns
    -------
    dict
        ``OptimizationResult``-shaped dict with keys:
        - ``recommendedAllocation`` (list of allocation records)
        - ``alternativesConsidered`` (list of rejected alternatives)
        - ``constraintsSatisfied`` (bool) ← Confidence Check uses this
        - ``infeasibilityReason`` (str | None)
        - ``bufferAfterDeployment`` (str decimal)
        - ``solverUsed`` (str: ``"scipy"`` or ``"greedy"``)

    Raises
    ------
    OptimizerClientError
        If the service is unreachable or returns a non-200 status.

    Examples
    --------
    ::

        result = get_allocation(treasury_state, instruments)
        if result["constraintsSatisfied"]:
            allocation = result["recommendedAllocation"][0]
        else:
            logger.warning("Optimizer infeasible: %s", result["infeasibilityReason"])
    """
    payload: dict[str, Any] = {
        "availableSurplus": str(treasury_state.available_surplus),
        "minimumBufferRequired": str(treasury_state.goal.minimum_liquidity_buffer)
        if hasattr(treasury_state, "goal")
        else "20000000.00",
        "currentTotalBalance": str(treasury_state.total_available_balance),
        "asOfDate": treasury_state.as_of.date().isoformat(),
        "instruments": instruments,
    }

    if treasury_state.next_fixed_obligation_date:
        payload["nextFixedObligationDate"] = treasury_state.next_fixed_obligation_date.isoformat()
    if treasury_state.next_fixed_obligation_amount is not None:
        payload["nextFixedObligationAmount"] = str(treasury_state.next_fixed_obligation_amount)

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(f"{_OPTIMIZER_BASE_URL}/optimize", json=payload)
            resp.raise_for_status()
            return resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        raise OptimizerClientError(f"Optimizer request failed: {exc}") from exc
