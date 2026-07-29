"""
agent/tests/test_nodes/test_disambiguate.py
============================================

Unit tests for the Disambiguate node (``agent/nodes/disambiguate.py``).

Tests verify:
1. High stakes (large surplus + affects fixed) → ESCALATE
2. Low stakes (small surplus, no fixed impact) → PROCEED_FLAGGED
3. Rationale string is always generated (LLM or template)
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from agent.nodes.disambiguate import compute_stakes_score, disambiguate_node


# ---------------------------------------------------------------------------
# Test 1: High stakes → ESCALATE
# ---------------------------------------------------------------------------

def test_high_stakes_routes_to_escalate(ctx_with_state):
    """
    High amount_ratio + affects_fixed flags → stakes_score >= 0.6 → ESCALATE.

    available_surplus = 80M, minimum_buffer = 20M → ratio = 4.0
    affects_fixed = True (STALE_DATA_PRESENT flag)
    score = min(1.0, 4.0*0.5 + 0.5) = min(1.0, 2.5) = 1.0 → ESCALATE
    """
    ctx = ctx_with_state
    ctx.treasury_state.available_surplus = Decimal("80000000.00")
    ctx.conflict_flags = ["STALE_DATA_PRESENT"]

    # Patch rationale to avoid LLM call
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "agent.nodes.disambiguate.llm_generate_rationale",
        return_value="[TEST] Escalate rationale"
    ):
        result = disambiguate_node(ctx)

    assert result.disambiguation_path == "ESCALATE", \
        f"Expected ESCALATE for high stakes, got {result.disambiguation_path}"


# ---------------------------------------------------------------------------
# Test 2: Low stakes → PROCEED_FLAGGED
# ---------------------------------------------------------------------------

def test_low_stakes_routes_to_proceed_flagged(ctx_with_state):
    """
    Low amount_ratio + no fixed impact → stakes_score < 0.6 → PROCEED_FLAGGED.

    available_surplus = 1M, minimum_buffer = 20M → ratio = 0.05
    affects_fixed = False (LOW_FORECAST_CONFIDENCE only)
    score = min(1.0, 0.05*0.5 + 0.0) = 0.025 → PROCEED_FLAGGED
    """
    ctx = ctx_with_state
    ctx.treasury_state.available_surplus = Decimal("1000000.00")
    ctx.conflict_flags = ["LOW_FORECAST_CONFIDENCE"]

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "agent.nodes.disambiguate.llm_generate_rationale",
        return_value="[TEST] Proceed flagged rationale"
    ):
        result = disambiguate_node(ctx)

    assert result.disambiguation_path == "PROCEED_FLAGGED", \
        f"Expected PROCEED_FLAGGED for low stakes, got {result.disambiguation_path}"


# ---------------------------------------------------------------------------
# Test 3: Rationale string is always generated
# ---------------------------------------------------------------------------

def test_rationale_string_is_generated(ctx_with_state):
    """Disambiguation rationale is a non-empty string regardless of path."""
    ctx = ctx_with_state
    ctx.conflict_flags = ["LOW_FORECAST_CONFIDENCE"]

    # Allow the template fallback to run (no GEMINI_API_KEY in test env)
    result = disambiguate_node(ctx)

    assert result.disambiguation_rationale is not None
    assert len(result.disambiguation_rationale) > 10, \
        "Rationale should be a meaningful string, not empty"


# ---------------------------------------------------------------------------
# Test 4: Stakes score formula correctness
# ---------------------------------------------------------------------------

def test_stakes_score_formula_correctness(ctx_with_state):
    """
    Verify the stakes score formula directly.

    Case A: ratio=1.0, affects_fixed=True → score = min(1.0, 0.5+0.5) = 1.0
    Case B: ratio=0.5, affects_fixed=False → score = min(1.0, 0.25) = 0.25
    Case C: ratio=10, affects_fixed=False → score = min(1.0, 5.0) = 1.0
    """
    ctx = ctx_with_state

    # Case A
    ctx.treasury_state.available_surplus = Decimal("20000000.00")  # = buffer, ratio=1.0
    ctx.conflict_flags = ["UNRECONCILED_LARGE_CREDIT"]
    score_a = compute_stakes_score(ctx)
    assert abs(score_a - 1.0) < 0.01, f"Expected 1.0, got {score_a}"

    # Case B
    ctx.treasury_state.available_surplus = Decimal("10000000.00")  # ratio=0.5
    ctx.conflict_flags = ["LOW_FORECAST_CONFIDENCE"]
    score_b = compute_stakes_score(ctx)
    assert abs(score_b - 0.25) < 0.01, f"Expected 0.25, got {score_b}"

    # Case C (clamped)
    ctx.treasury_state.available_surplus = Decimal("200000000.00")  # ratio=10
    ctx.conflict_flags = []
    score_c = compute_stakes_score(ctx)
    assert score_c <= 1.0, "Stakes score must be clamped to 1.0"
