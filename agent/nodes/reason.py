"""
agent/nodes/reason.py
=====================

**Node 2: Reason** — Calls the forecaster and optimizer with feedback-adjusted inputs.

Purpose
-------
The Reason node is the agent's analytical engine.  It:

1. Checks if execution is blocked (skip if so).
2. Queries the feedback loop for optimizer adjustments derived from past human decisions.
3. Fetches deposit rates from the bank and filters by ``max_term_days``.
4. Calls the forecasting service for a 14-day cash-flow forecast.
5. Calls the optimization service for the best surplus allocation.

Feedback loop integration
--------------------------
Before calling the optimizer, the node calls ``compute_feedback_adjustments``
to check whether any past rejections should constrain the instrument choices.
This is what makes the agent **adaptive** — it genuinely changes future
recommendations based on what the human has rejected in the past.

Input / Output
--------------
- Input:  ``AgentContext`` with ``treasury_state`` populated (from Perceive).
- Output: ``AgentContext`` with ``forecast_result`` and ``optimizer_result`` populated.

Edge cases
----------
- If ``treasury_state.execution_blocked`` is ``True``, the node passes through
  without calling any external services.
- If the forecaster or optimizer fails, the node sets a degraded result dict
  (``overallConfidenceScore = 0.0`` / ``constraintsSatisfied = False``) so the
  Confidence Check node can route appropriately.
"""

from __future__ import annotations

import logging

from agent.memory.feedback import compute_feedback_adjustments
from agent.state import AgentContext
from agent.tools import bank_client, forecast_client, optimizer_client
from agent.tools.bank_client import BankClientError
from agent.tools.forecast_client import ForecastClientError
from agent.tools.optimizer_client import OptimizerClientError

logger = logging.getLogger(__name__)


async def reason_node(ctx: AgentContext) -> AgentContext:
    """
    LangGraph node function for the Reason step.

    Parameters
    ----------
    ctx:
        ``AgentContext`` with ``treasury_state`` populated by Perceive.

    Returns
    -------
    AgentContext
        Updated with ``forecast_result`` and ``optimizer_result``.
        If blocked, returns ctx unchanged (except for a log message).
    """
    logger.info("[Reason] Starting — cycle %s", ctx.cycle_id)

    if ctx.treasury_state is None:
        logger.error("[Reason] treasury_state is None — cannot reason. Skipping.")
        ctx.forecast_result = _failed_forecast()
        ctx.optimizer_result = _failed_optimizer("No treasury state available.")
        return ctx

    if ctx.treasury_state.execution_blocked:
        logger.warning(
            "[Reason] Execution blocked (%s) — skipping forecast and optimizer.",
            ctx.treasury_state.block_reason,
        )
        ctx.forecast_result = _failed_forecast()
        ctx.optimizer_result = _failed_optimizer(ctx.treasury_state.block_reason or "Blocked")
        return ctx

    # ------------------------------------------------------------------
    # 1. Query the feedback loop for optimizer constraints
    # ------------------------------------------------------------------
    try:
        adjustments = await compute_feedback_adjustments(ctx.goal.company_code)
        logger.info(
            "[Reason] Feedback adjustments — max_term_days=%d note=%s",
            adjustments.max_term_days,
            adjustments.note,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Reason] Feedback query failed (%s) — using defaults.", exc)
        from agent.memory.feedback import FeedbackAdjustments
        adjustments = FeedbackAdjustments()

    # ------------------------------------------------------------------
    # 2. Fetch available deposit instruments from the bank
    # ------------------------------------------------------------------
    try:
        rates_response = bank_client.get_deposit_rates()
        raw_instruments = rates_response.get("rates", [])
    except BankClientError as exc:
        logger.warning("[Reason] Could not fetch deposit rates: %s — using empty list.", exc)
        raw_instruments = []

    # Apply feedback adjustment: filter out instruments with term > max_term_days
    instruments = [
        i for i in raw_instruments
        if int(i.get("termDays", 999)) <= adjustments.max_term_days
    ]
    logger.info(
        "[Reason] Instruments after feedback filter: %d of %d",
        len(instruments), len(raw_instruments),
    )

    # ------------------------------------------------------------------
    # 3. Forecast
    # ------------------------------------------------------------------
    try:
        forecast_result = forecast_client.get_forecast(
            company_code=ctx.goal.company_code,
            horizon_days=14,
        )
        logger.info(
            "[Reason] Forecast received — confidence=%.3f model=%s",
            forecast_result.get("overallConfidenceScore", 0),
            forecast_result.get("modelType", "?"),
        )
    except ForecastClientError as exc:
        logger.warning("[Reason] Forecast failed: %s — using degraded result.", exc)
        forecast_result = _failed_forecast()

    ctx.forecast_result = forecast_result

    # ------------------------------------------------------------------
    # 4. Optimizer
    # ------------------------------------------------------------------
    if not instruments:
        logger.warning("[Reason] No instruments available — optimizer will be infeasible.")
        ctx.optimizer_result = _failed_optimizer("No instruments available after feedback filter.")
        return ctx

    try:
        optimizer_result = optimizer_client.get_allocation(ctx.treasury_state, instruments)
        logger.info(
            "[Reason] Optimizer done — constraintsSatisfied=%s solver=%s",
            optimizer_result.get("constraintsSatisfied"),
            optimizer_result.get("solverUsed"),
        )
    except OptimizerClientError as exc:
        logger.warning("[Reason] Optimizer failed: %s — using degraded result.", exc)
        optimizer_result = _failed_optimizer(str(exc))

    ctx.optimizer_result = optimizer_result
    return ctx


# ---------------------------------------------------------------------------
# Degraded result helpers
# ---------------------------------------------------------------------------

def _failed_forecast() -> dict:
    """Return a minimal forecast dict indicating service failure."""
    return {
        "companyCode": "1000",
        "forecastHorizonDays": 14,
        "generatedAt": "",
        "modelType": "UNAVAILABLE",
        "forecast": [],
        "overallConfidenceScore": 0.0,
        "flags": ["FORECAST_SERVICE_UNAVAILABLE"],
        "fallbackUsed": True,
        "fallbackReason": "Forecast service unreachable.",
    }


def _failed_optimizer(reason: str) -> dict:
    """Return a minimal optimizer dict indicating service failure."""
    return {
        "recommendedAllocation": [],
        "alternativesConsidered": [],
        "constraintsSatisfied": False,
        "infeasibilityReason": reason,
        "costOfDebtHurdleBreached": False,
        "hurdleNote": None,
        "solverUsed": "UNAVAILABLE",
        "bufferAfterDeployment": "0",
    }
