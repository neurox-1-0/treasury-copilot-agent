"""
agent/tests/test_nodes/test_decide.py
=======================================

Unit tests for the Decide node (``agent/nodes/decide.py``).

Tests verify:
1. Idempotency: duplicate PENDING proposal is skipped
2. Infeasible optimizer → NO_ACTION
3. Execution blocked → NO_ACTION
4. Constraint violation surfaced (not silently adjusted)
5. Valid proposal has all required fields
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from agent.nodes.decide import decide_node
from agent.state import ProposedAction


@pytest.fixture(autouse=True)
def _reset_db_init():
    """Reset the DB init flag so in_memory_db patches are always applied."""
    import agent.db.audit_log as al
    al._db_initialised = False  # allow re-init per test
    yield


# ---------------------------------------------------------------------------
# Test 1: Idempotency — duplicate PENDING skipped
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_idempotency_skips_duplicate_pending(ctx_with_state, mock_forecast, mock_optimizer, in_memory_db):
    """Pre-existing PENDING record with same content_hash → skip_reason=DUPLICATE_PENDING."""
    ctx = ctx_with_state
    # Ensure maturity constraint doesn't fire
    ctx.treasury_state.next_fixed_obligation_date = date.today() + timedelta(days=45)
    ctx.forecast_result = mock_forecast
    ctx.optimizer_result = mock_optimizer
    ctx.confidence_score = 0.85
    ctx.conflict_flags = []

    # Mock is_duplicate_pending to return True — simulating a pre-existing PENDING entry
    with patch("agent.nodes.decide.is_duplicate_pending", new=AsyncMock(return_value=True)), \
         patch("agent.nodes.decide.llm_generate_rationale", return_value="[TEST] rationale"):
        result = await decide_node(ctx)

    assert result.skip_reason == "DUPLICATE_PENDING", \
        "Should skip when a PENDING proposal with the same hash already exists"
    assert result.proposed_action is None


# ---------------------------------------------------------------------------
# Test 2: Infeasible optimizer → NO_ACTION
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_infeasible_optimizer_produces_no_action(ctx_with_state, mock_forecast, in_memory_db):
    """Optimizer returns constraintsSatisfied=False → action_type=NO_ACTION."""
    ctx = ctx_with_state
    ctx.forecast_result = mock_forecast
    ctx.optimizer_result = {
        "constraintsSatisfied": False,
        "infeasibilityReason": "No feasible allocation found",
        "recommendedAllocation": [],
        "alternativesConsidered": [],
        "solverUsed": "UNAVAILABLE",
        "bufferAfterDeployment": "0",
    }
    ctx.confidence_score = 0.85
    ctx.conflict_flags = []

    with patch("agent.nodes.decide.llm_generate_rationale", return_value="[TEST] rationale"):
        result = await decide_node(ctx)

    assert result.proposed_action is not None
    assert result.proposed_action.action_type == "NO_ACTION", \
        f"Expected NO_ACTION, got {result.proposed_action.action_type}"


# ---------------------------------------------------------------------------
# Test 3: Execution blocked → NO_ACTION
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_execution_blocked_produces_no_action(ctx_with_state, mock_forecast, mock_optimizer, in_memory_db):
    """treasury_state.execution_blocked=True → action_type=NO_ACTION."""
    ctx = ctx_with_state
    ctx.treasury_state.execution_blocked = True
    ctx.treasury_state.block_reason = "Cash position data unavailable."
    ctx.forecast_result = mock_forecast
    ctx.optimizer_result = mock_optimizer
    ctx.confidence_score = 0.0

    with patch("agent.nodes.decide.llm_generate_rationale", return_value="[TEST] rationale"):
        result = await decide_node(ctx)

    assert result.proposed_action is not None
    assert result.proposed_action.action_type == "NO_ACTION"


# ---------------------------------------------------------------------------
# Test 4: Constraint violation surfaced (not silently adjusted)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_constraint_violation_is_surfaced_not_silently_adjusted(ctx_with_state, mock_forecast, in_memory_db):
    """
    Optimizer recommends allocating more than available_surplus → CONSTRAINT_VIOLATION.
    The violation must be named in the description — not silently capped.
    """
    ctx = ctx_with_state
    ctx.treasury_state.available_surplus = Decimal("5000000.00")  # only 5M available

    # Optimizer says allocate 15M (too much)
    ctx.optimizer_result = {
        "constraintsSatisfied": True,
        "recommendedAllocation": [{
            "bank": "Sampath",
            "instrument": "FIXED_DEPOSIT",
            "termDays": 30,
            "amount": "15000000.00",  # > 5M surplus
            "maturityDate": (date.today() + timedelta(days=30)).isoformat(),
            "expectedYield": "147945.21",
            "yieldRate": 0.12,
        }],
        "alternativesConsidered": [],
        "bufferAfterDeployment": "85000000.00",
        "solverUsed": "scipy",
    }
    ctx.forecast_result = mock_forecast
    ctx.confidence_score = 0.85
    ctx.conflict_flags = []

    with patch("agent.nodes.decide.llm_generate_rationale", return_value="[TEST] rationale"):
        result = await decide_node(ctx)

    assert result.proposed_action is not None
    assert result.proposed_action.action_type == "CONSTRAINT_VIOLATION", \
        f"Expected CONSTRAINT_VIOLATION, got {result.proposed_action.action_type}"
    assert "surplus" in result.proposed_action.description.lower() or \
           "constraint" in result.proposed_action.description.lower(), \
        "Description should name the violated constraint"


# ---------------------------------------------------------------------------
# Test 5: Valid proposal has all required fields
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_valid_proposal_has_required_fields(ctx_with_state, mock_forecast, mock_optimizer, in_memory_db):
    """
    Happy path: valid treasury_state + forecast + optimizer → full ProposedAction.
    All required fields must be present and non-empty.

    The next_fixed_obligation_date is set to 45 days out so the 30-day FD
    maturity does not trigger a constraint violation.
    """
    ctx = ctx_with_state
    # Push the next fixed obligation out past the 30-day FD maturity
    ctx.treasury_state.next_fixed_obligation_date = date.today() + timedelta(days=45)
    ctx.forecast_result = mock_forecast
    ctx.optimizer_result = mock_optimizer
    ctx.confidence_score = 0.85
    ctx.conflict_flags = []

    with patch("agent.nodes.decide.llm_generate_rationale", return_value="[TEST] rationale text"):
        result = await decide_node(ctx)

    p = result.proposed_action
    assert p is not None, "ProposedAction should be created"
    assert p.proposal_id and len(p.proposal_id) > 0, "proposal_id must be set"
    assert p.action_type == "SURPLUS_ALLOCATION"
    assert p.description and len(p.description) > 0
    assert p.rationale and len(p.rationale) > 0
    assert isinstance(p.alternatives_rejected, list)
    assert isinstance(p.overall_confidence_score, float)
    assert p.content_hash and len(p.content_hash) == 64, "content_hash must be a 64-char hex string"
    assert p.parameter_bounds and "termDays" in p.parameter_bounds
    assert p.requires_human_approval is True
