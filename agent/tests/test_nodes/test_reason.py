"""
agent/tests/test_nodes/test_reason.py
=======================================

Unit tests for the Reason node (``agent/nodes/reason.py``).

All external calls (feedback DB, bank API, forecaster, optimizer) are mocked.
These tests verify that:
1. Feedback adjustments are correctly applied to filter instruments.
2. Forecast and optimizer results are passed through to the context.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from agent.memory.feedback import FeedbackAdjustments
from agent.nodes.reason import reason_node


# ---------------------------------------------------------------------------
# Test 1: Feedback adjustment caps instrument term
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_feedback_caps_instrument_term(ctx_with_state, mock_forecast):
    """
    When feedback returns max_term_days=30, instruments with termDays>30
    should be filtered out before the optimizer call.
    """
    # Two instruments: one 30-day, one 90-day
    all_rates = {
        "rates": [
            {"bank": "Sampath", "type": "FIXED_DEPOSIT", "termDays": 30, "rate": 0.12},
            {"bank": "HNB",     "type": "FIXED_DEPOSIT", "termDays": 90, "rate": 0.135},
        ]
    }

    # Feedback says cap at 30 days
    feedback_adj = FeedbackAdjustments(max_term_days=30, note="Capped by feedback")

    captured_instruments = []

    def mock_get_allocation(ts, instruments):
        captured_instruments.extend(instruments)
        return {
            "recommendedAllocation": [],
            "alternativesConsidered": [],
            "constraintsSatisfied": False,
            "infeasibilityReason": "No instruments",
            "solverUsed": "UNAVAILABLE",
            "bufferAfterDeployment": "0",
        }

    with patch("agent.nodes.reason.compute_feedback_adjustments", new=AsyncMock(return_value=feedback_adj)), \
         patch("agent.nodes.reason.bank_client.get_deposit_rates", return_value=all_rates), \
         patch("agent.nodes.reason.forecast_client.get_forecast", return_value=mock_forecast), \
         patch("agent.nodes.reason.optimizer_client.get_allocation", side_effect=mock_get_allocation):

        result = await reason_node(ctx_with_state)

    # Only the 30-day instrument should have been passed to the optimizer
    assert all(i["termDays"] <= 30 for i in captured_instruments), \
        "Instruments with termDays > max_term_days should have been filtered"
    assert len(captured_instruments) == 1
    assert captured_instruments[0]["bank"] == "Sampath"


# ---------------------------------------------------------------------------
# Test 2: Forecast and optimizer results are populated in context
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_reason_populates_forecast_and_optimizer(ctx_with_state, mock_forecast, mock_optimizer):
    """
    When all calls succeed, forecast_result and optimizer_result should be
    populated in the returned AgentContext.
    """
    rates = {"rates": [{"bank": "Sampath", "type": "FIXED_DEPOSIT", "termDays": 30, "rate": 0.12}]}
    feedback_adj = FeedbackAdjustments()  # no constraints

    with patch("agent.nodes.reason.compute_feedback_adjustments", new=AsyncMock(return_value=feedback_adj)), \
         patch("agent.nodes.reason.bank_client.get_deposit_rates", return_value=rates), \
         patch("agent.nodes.reason.forecast_client.get_forecast", return_value=mock_forecast), \
         patch("agent.nodes.reason.optimizer_client.get_allocation", return_value=mock_optimizer):

        result = await reason_node(ctx_with_state)

    assert result.forecast_result is not None
    assert result.forecast_result["overallConfidenceScore"] == 0.85
    assert result.optimizer_result is not None
    assert result.optimizer_result["constraintsSatisfied"] is True


# ---------------------------------------------------------------------------
# Test 3: Execution blocked → skip
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_reason_skips_when_execution_blocked(ctx_with_state):
    """When execution_blocked=True, reason node should skip all external calls."""
    ctx_with_state.treasury_state.execution_blocked = True
    ctx_with_state.treasury_state.block_reason = "Test block"

    with patch("agent.nodes.reason.compute_feedback_adjustments", new=AsyncMock()) as mock_feedback, \
         patch("agent.nodes.reason.bank_client.get_deposit_rates") as mock_rates, \
         patch("agent.nodes.reason.forecast_client.get_forecast") as mock_forecast, \
         patch("agent.nodes.reason.optimizer_client.get_allocation") as mock_opt:

        result = await reason_node(ctx_with_state)

    # None of the external calls should have been made
    mock_feedback.assert_not_called()
    mock_rates.assert_not_called()
    mock_forecast.assert_not_called()
    mock_opt.assert_not_called()

    # Degraded results should be set
    assert result.forecast_result["overallConfidenceScore"] == 0.0
    assert result.optimizer_result["constraintsSatisfied"] is False
