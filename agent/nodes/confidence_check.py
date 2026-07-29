"""
agent/nodes/confidence_check.py
================================

**Node 3: Confidence & Conflict Check** — The core reasoning gate.

Purpose
-------
This node evaluates four data quality and signal-consistency checks and
decides whether to route to ``DECIDE`` (proceed normally) or ``DISAMBIGUATE``
(more scrutiny required).

This is **one of the three proofs of genuine agency** in the system:
the routing decision here actually changes the agent's behaviour based on
data quality signals — it does not always proceed.

Checks (in order)
-----------------
1. **Low forecast confidence** — ``overallConfidenceScore < 0.6`` → DISAMBIGUATE
2. **Stale data + materiality** — stale data AND ``available_surplus > 10% of buffer`` → DISAMBIGUATE
3. **Unreconciled large credits** — any credit > LKR 1M unreconciled → DISAMBIGUATE
4. **Optimizer infeasibility** — ``constraintsSatisfied = False`` → DECIDE
   (still proceed; the Decide node surfaces the infeasibility in its output)

Routing output
--------------
The node sets:
- ``ctx.confidence_score`` — the raw forecast confidence (0–1)
- ``ctx.conflict_flags`` — list of active flag strings
- ``ctx.route`` — ``"DECIDE"`` | ``"DISAMBIGUATE"``

The LangGraph conditional edge reads ``ctx.route`` to determine the next node.

Input / Output
--------------
- Input:  ``AgentContext`` with ``forecast_result``, ``optimizer_result``, ``treasury_state``.
- Output: ``AgentContext`` with ``confidence_score``, ``conflict_flags``, ``route``.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from agent.state import AgentContext

logger = logging.getLogger(__name__)

# Thresholds (documented for auditability)
_CONFIDENCE_THRESHOLD = 0.6
_LARGE_UNRECONCILED_THRESHOLD = Decimal("1000000.00")  # LKR 1M


def check_confidence_and_conflicts(ctx: AgentContext) -> tuple[float, list[str], str]:
    """
    Run all four confidence and conflict checks.

    This function is deterministic — no randomness, no LLM calls.
    All branching is based on verifiable data quality signals.

    Parameters
    ----------
    ctx:
        Current ``AgentContext``.

    Returns
    -------
    tuple[float, list[str], str]
        - ``score``: forecast confidence (0–1)
        - ``flags``: list of active conflict flag strings
        - ``route``: ``"DECIDE"`` | ``"DISAMBIGUATE"``

    Rules (in priority order)
    -------------------------
    - ``LOW_FORECAST_CONFIDENCE`` — triggers if score < 0.6
    - ``STALE_DATA_PRESENT`` — triggers if has_stale_data AND surplus > 10% of buffer
    - ``UNRECONCILED_LARGE_CREDIT`` — triggers if any credit > LKR 1M is unreconciled
    - ``OPTIMIZER_INFEASIBLE`` — triggers if constraintsSatisfied == False (routes to DECIDE,
      not DISAMBIGUATE — the Decide node handles infeasibility explicitly)
    """
    flags: list[str] = []
    route = "DECIDE"  # optimistic default

    forecast = ctx.forecast_result or {}
    ts = ctx.treasury_state
    optimizer = ctx.optimizer_result or {}

    # --- Check 1: Low forecast confidence & fallback ---
    score = float(forecast.get("overallConfidenceScore", 0.0))
    if score < _CONFIDENCE_THRESHOLD:
        flags.append("LOW_FORECAST_CONFIDENCE")
        route = "DISAMBIGUATE"
        logger.info("[ConfidenceCheck] LOW_FORECAST_CONFIDENCE: score=%.3f < %.1f", score, _CONFIDENCE_THRESHOLD)

    if forecast.get("fallbackUsed"):
        flags.append("FORECAST_FALLBACK_USED")
        route = "DISAMBIGUATE"
        logger.info("[ConfidenceCheck] FORECAST_FALLBACK_USED — routing to DISAMBIGUATE")

    # --- Check 2: Stale data + materiality ---
    if ts and ts.has_stale_data:
        materiality_threshold = ctx.goal.minimum_liquidity_buffer * Decimal("0.10")
        if ts.available_surplus > materiality_threshold:
            flags.append("STALE_DATA_PRESENT")
            route = "DISAMBIGUATE"
            logger.info(
                "[ConfidenceCheck] STALE_DATA_PRESENT — surplus %.2f > materiality %.2f",
                ts.available_surplus, materiality_threshold,
            )

    # --- Check 3: Unreconciled large credits ---
    if ts:
        large_unreconciled = [
            c for c in ts.unreconciled_large_credits
            if Decimal(c.get("amount", "0")) > _LARGE_UNRECONCILED_THRESHOLD
        ]
        if large_unreconciled:
            flags.append("UNRECONCILED_LARGE_CREDIT")
            route = "DISAMBIGUATE"
            logger.info(
                "[ConfidenceCheck] UNRECONCILED_LARGE_CREDIT — %d credits above LKR 1M",
                len(large_unreconciled),
            )

    # --- Check 4: Optimizer infeasibility & solver fallback ---
    if not optimizer.get("constraintsSatisfied", True):
        flags.append("OPTIMIZER_INFEASIBLE")
        # NOTE: route remains DECIDE — Decide node handles this explicitly
        logger.info("[ConfidenceCheck] OPTIMIZER_INFEASIBLE — routing to DECIDE for explicit NO_ACTION")

    if optimizer.get("solverUsed") == "GREEDY_FALLBACK":
        flags.append("OPTIMIZER_GREEDY_FALLBACK")
        logger.info("[ConfidenceCheck] OPTIMIZER_GREEDY_FALLBACK flagged")

    return score, flags, route


    return score, flags, route


def confidence_check_node(ctx: AgentContext) -> AgentContext:
    """
    LangGraph node function for the Confidence & Conflict Check step.

    Parameters
    ----------
    ctx:
        ``AgentContext`` with forecast, optimizer, and treasury_state populated.

    Returns
    -------
    AgentContext
        Updated with ``confidence_score``, ``conflict_flags``, ``route``.
    """
    logger.info("[ConfidenceCheck] Starting — cycle %s", ctx.cycle_id)

    score, flags, route = check_confidence_and_conflicts(ctx)

    ctx.confidence_score = score
    ctx.conflict_flags = flags
    ctx.route = route

    logger.info(
        "[ConfidenceCheck] Done — score=%.3f flags=%s route=%s",
        score, flags, route,
    )
    return ctx


def route_after_confidence_check(ctx: AgentContext) -> str:
    """
    LangGraph conditional edge function.

    Reads ``ctx.route`` (set by ``confidence_check_node``) and returns the
    next node name.

    Parameters
    ----------
    ctx:
        Current ``AgentContext``.

    Returns
    -------
    str
        ``"decide"`` or ``"disambiguate"``
    """
    return ctx.route.lower() if ctx.route else "decide"
