"""
agent/tests/test_nodes/test_confidence_check.py
=================================================

Unit tests for the Confidence & Conflict Check node (``agent/nodes/confidence_check.py``).

All four routing checks are tested independently:
1. Low forecast confidence → DISAMBIGUATE + LOW_FORECAST_CONFIDENCE flag
2. High confidence, clean data → DECIDE
3. Stale data with high materiality → DISAMBIGUATE + STALE_DATA_PRESENT flag
4. Unreconciled large credit → DISAMBIGUATE + UNRECONCILED_LARGE_CREDIT flag

These tests validate the *core routing gate* — the heart of the agent's reasoning.
"""

from __future__ import annotations

import copy
from datetime import date, timedelta
from decimal import Decimal

import pytest

from agent.nodes.confidence_check import confidence_check_node, check_confidence_and_conflicts
from agent.state import DataFreshness, DataSourceStatus


# ---------------------------------------------------------------------------
# Test 1: Low forecast confidence → DISAMBIGUATE
# ---------------------------------------------------------------------------

def test_routes_to_disambiguate_on_low_confidence(ctx_with_state, mock_forecast, mock_optimizer):
    """Forecast with overallConfidenceScore=0.4 → route=DISAMBIGUATE + LOW_FORECAST_CONFIDENCE."""
    ctx = ctx_with_state
    ctx.forecast_result = {**mock_forecast, "overallConfidenceScore": 0.4}
    ctx.optimizer_result = mock_optimizer

    result = confidence_check_node(ctx)

    assert result.route == "DISAMBIGUATE", f"Expected DISAMBIGUATE, got {result.route}"
    assert "LOW_FORECAST_CONFIDENCE" in result.conflict_flags


# ---------------------------------------------------------------------------
# Test 2: High confidence, fresh data → DECIDE
# ---------------------------------------------------------------------------

def test_routes_to_decide_on_high_confidence(ctx_with_state, mock_forecast, mock_optimizer):
    """Forecast confidence=0.9, no stale data, no unreconciled credits → route=DECIDE."""
    ctx = ctx_with_state
    ctx.forecast_result = {**mock_forecast, "overallConfidenceScore": 0.9}
    ctx.optimizer_result = mock_optimizer
    ctx.treasury_state.has_stale_data = False
    ctx.treasury_state.unreconciled_large_credits = []

    result = confidence_check_node(ctx)

    assert result.route == "DECIDE", f"Expected DECIDE, got {result.route}"
    assert len(result.conflict_flags) == 0


# ---------------------------------------------------------------------------
# Test 3: Stale data + high materiality → DISAMBIGUATE
# ---------------------------------------------------------------------------

def test_routes_to_disambiguate_on_stale_data_with_high_materiality(ctx_with_state, mock_forecast, mock_optimizer):
    """
    Stale data present AND available_surplus > 10% of minimum_buffer → DISAMBIGUATE.

    minimum_buffer = 20M, 10% = 2M, available_surplus = 80M → materiality check passes.
    """
    ctx = ctx_with_state
    ctx.forecast_result = {**mock_forecast, "overallConfidenceScore": 0.85}  # high confidence
    ctx.optimizer_result = mock_optimizer
    ctx.treasury_state.has_stale_data = True
    ctx.treasury_state.available_surplus = Decimal("80000000.00")  # well above 10% of 20M

    # Mark one source as stale
    ctx.treasury_state.data_source_statuses = [
        DataSourceStatus(
            source="ERP_CASH_POSITION",
            freshness=DataFreshness.STALE,
            stale_reason="ERP timeout",
        )
    ]

    result = confidence_check_node(ctx)

    assert result.route == "DISAMBIGUATE", f"Expected DISAMBIGUATE, got {result.route}"
    assert "STALE_DATA_PRESENT" in result.conflict_flags


# ---------------------------------------------------------------------------
# Test 4: Unreconciled large credit → DISAMBIGUATE
# ---------------------------------------------------------------------------

def test_routes_to_disambiguate_on_unreconciled_large_credit(ctx_with_state, mock_forecast, mock_optimizer):
    """Bank statement has LKR 2M unreconciled credit → route=DISAMBIGUATE + flag."""
    ctx = ctx_with_state
    ctx.forecast_result = {**mock_forecast, "overallConfidenceScore": 0.9}
    ctx.optimizer_result = mock_optimizer
    ctx.treasury_state.has_stale_data = False
    ctx.treasury_state.unreconciled_large_credits = [
        {"transactionId": "TXN-001", "amount": "2000000.00", "date": date.today().isoformat()}
    ]

    result = confidence_check_node(ctx)

    assert result.route == "DISAMBIGUATE", f"Expected DISAMBIGUATE, got {result.route}"
    assert "UNRECONCILED_LARGE_CREDIT" in result.conflict_flags


# ---------------------------------------------------------------------------
# Test 5: Optimizer infeasible → DECIDE (not DISAMBIGUATE)
# ---------------------------------------------------------------------------

def test_optimizer_infeasible_routes_to_decide(ctx_with_state, mock_forecast):
    """
    Optimizer infeasible → OPTIMIZER_INFEASIBLE flag, but route stays DECIDE.
    The Decide node handles infeasibility explicitly with NO_ACTION.
    """
    ctx = ctx_with_state
    ctx.forecast_result = {**mock_forecast, "overallConfidenceScore": 0.9}
    ctx.optimizer_result = {
        "constraintsSatisfied": False,
        "infeasibilityReason": "No instruments available",
        "recommendedAllocation": [],
        "alternativesConsidered": [],
        "solverUsed": "UNAVAILABLE",
        "bufferAfterDeployment": "0",
    }
    ctx.treasury_state.has_stale_data = False
    ctx.treasury_state.unreconciled_large_credits = []

    result = confidence_check_node(ctx)

    assert result.route == "DECIDE", f"Expected DECIDE for infeasible optimizer, got {result.route}"
    assert "OPTIMIZER_INFEASIBLE" in result.conflict_flags
