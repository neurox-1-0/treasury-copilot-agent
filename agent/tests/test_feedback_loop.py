"""
agent/tests/test_feedback_loop.py
===================================

Unit tests for the feedback loop (``agent/memory/feedback.py``).

Tests verify that ``compute_feedback_adjustments`` correctly detects rejection
patterns and adjusts the optimizer's ``max_term_days`` constraint.

This is the third proof of genuine agency: the agent genuinely changes future
behaviour based on past human decisions.
"""

from __future__ import annotations

from datetime import datetime
import json
from unittest.mock import AsyncMock, patch

import pytest

import agent.db.audit_log as audit_module
from agent.memory.feedback import compute_feedback_adjustments, FeedbackAdjustments


# ---------------------------------------------------------------------------
# Test 1: 2 long-term rejections → max_term_days = 30
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_feedback_caps_term_after_two_long_term_rejections(in_memory_db):
    """
    Insert 2 REJECTED decisions for 90-day FD → max_term_days should be capped at 30.

    The feedback module checks: if long-term (>30 day) proposals were rejected
    2+ times in last 5 cycles, cap max_term_days=30.
    """
    # Insert 2 rejected 90-day FD decisions
    for i in range(2):
        await audit_module.insert_proposal(
            cycle_id=f"cycle-{i}",
            proposal_id=f"prop-{i}",
            company_code="1000",
            action_type="SURPLUS_ALLOCATION",
            description=f"90-day FD proposal {i}",
            rationale="Test",
            confidence_score=0.85,
            flagged_ambiguities=[],
            disambiguation_path=None,
            content_hash=f"hash-{i}",
        )
        # Update each to REJECTED with termDays=90 in modified_parameters
        await audit_module.update_decision(
            proposal_id=f"prop-{i}",
            human_decision="REJECTED",
            modified_parameters={"termDays": 90, "amount": "15000000"},
            human_note="Too long term",
        )

    result = await compute_feedback_adjustments("1000")

    assert result.max_term_days == 30, \
        f"Expected max_term_days=30 after 2 long-term rejections, got {result.max_term_days}"
    assert result.note is not None and "30" in result.note


# ---------------------------------------------------------------------------
# Test 2: All recent decisions approved → no constraint
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_feedback_no_constraint_when_all_approved(in_memory_db):
    """
    Insert 5 APPROVED decisions → no constraint should be applied.
    max_term_days should remain at the default (90).
    """
    for i in range(5):
        await audit_module.insert_proposal(
            cycle_id=f"cycle-app-{i}",
            proposal_id=f"prop-app-{i}",
            company_code="1000",
            action_type="SURPLUS_ALLOCATION",
            description=f"Approved proposal {i}",
            rationale="Test",
            confidence_score=0.9,
            flagged_ambiguities=[],
            disambiguation_path=None,
            content_hash=f"hash-app-{i}",
        )
        await audit_module.update_decision(
            proposal_id=f"prop-app-{i}",
            human_decision="APPROVED",
        )

    result = await compute_feedback_adjustments("1000")

    assert result.max_term_days == 90, \
        f"Expected max_term_days=90 (no constraint) when all approved, got {result.max_term_days}"
    assert result.note is None


# ---------------------------------------------------------------------------
# Test 3: Only 1 long-term rejection → no cap yet
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_feedback_no_cap_with_single_rejection(in_memory_db):
    """
    Only 1 long-term rejection → threshold not reached → no cap.
    """
    await audit_module.insert_proposal(
        cycle_id="cycle-rej",
        proposal_id="prop-rej",
        company_code="1000",
        action_type="SURPLUS_ALLOCATION",
        description="90-day FD",
        rationale="Test",
        confidence_score=0.85,
        flagged_ambiguities=[],
        disambiguation_path=None,
        content_hash="hash-rej",
    )
    await audit_module.update_decision(
        proposal_id="prop-rej",
        human_decision="REJECTED",
        modified_parameters={"termDays": 90},
    )

    result = await compute_feedback_adjustments("1000")

    assert result.max_term_days == 90, \
        "Should not cap term with only 1 long-term rejection (threshold is 2)"


# ---------------------------------------------------------------------------
# Test 4: Mixed decisions — only short-term rejections → no cap
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_feedback_no_cap_for_short_term_rejections(in_memory_db):
    """
    Rejections with termDays <= 30 should not trigger the long-term cap.
    """
    for i in range(3):
        await audit_module.insert_proposal(
            cycle_id=f"cycle-short-{i}",
            proposal_id=f"prop-short-{i}",
            company_code="1000",
            action_type="SURPLUS_ALLOCATION",
            description=f"30-day FD {i}",
            rationale="Test",
            confidence_score=0.85,
            flagged_ambiguities=[],
            disambiguation_path=None,
            content_hash=f"hash-short-{i}",
        )
        await audit_module.update_decision(
            proposal_id=f"prop-short-{i}",
            human_decision="REJECTED",
            modified_parameters={"termDays": 30},  # short-term, should not trigger cap
        )

    result = await compute_feedback_adjustments("1000")

    assert result.max_term_days == 90, \
        "Short-term rejections should not trigger the long-term cap"
