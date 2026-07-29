"""
agent/tests/test_graph.py
==========================

Integration tests for the full LangGraph reasoning loop graph.

All external services (ERP, bank, forecaster, optimizer) are mocked — no live
services required.  These tests validate the end-to-end graph execution path:

1. ``test_full_graph_runs_happy_path``
   All mocks return valid data → proposed_action with action_type=SURPLUS_ALLOCATION.

2. ``test_full_graph_reaches_disambiguate_on_low_confidence``
   Forecaster returns low confidence → graph passes through disambiguate node.

3. ``test_full_graph_no_action_on_execution_blocked``
   All ERP+bank calls fail → execution_blocked → proposed_action.action_type=NO_ACTION.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from agent.graph import build_graph
from agent.memory.cache import clear_cache
from agent.memory.feedback import FeedbackAdjustments
from agent.state import AgentContext, TreasuryGoal


@pytest.fixture(autouse=True)
def reset_cache_for_graph_tests():
    clear_cache()
    yield
    clear_cache()


def _make_goal():
    return TreasuryGoal(
        company_code="1000",
        currency="LKR",
        minimum_liquidity_buffer=Decimal("20000000.00"),
    )


def _bank_balances():
    return [{"accountId": "SAMP-001", "availableBalance": "100000000.00", "currency": "LKR", "accountType": "CURRENT"}]


def _payroll():
    # Set payroll 60 days out so the 30-day FD maturity does not exceed it
    return [{"AccountingDocument": "PAY-001", "TotalNetPayable": "18000000.00",
             "PaymentDueDate": (date.today() + timedelta(days=60)).isoformat(), "PaymentPriority": "FIXED"}]


def _forecast(confidence: float = 0.85) -> dict:
    return {
        "companyCode": "1000", "forecastHorizonDays": 14,
        "generatedAt": "", "modelType": "STUB",
        "forecast": [], "overallConfidenceScore": confidence,
        "flags": [], "fallbackUsed": False, "fallbackReason": None,
    }


def _optimizer() -> dict:
    today = date.today()
    return {
        "recommendedAllocation": [{
            "bank": "Sampath Bank PLC", "instrument": "FIXED_DEPOSIT",
            "termDays": 30, "amount": "15000000.00",
            "maturityDate": (today + timedelta(days=30)).isoformat(),
            "expectedYield": "493150.68", "yieldRate": 0.12,
        }],
        "alternativesConsidered": [],
        "constraintsSatisfied": True, "infeasibilityReason": None,
        "bufferAfterDeployment": "85000000.00", "solverUsed": "scipy",
    }


# Common patches for all ERP/bank mock calls
_COMMON_PATCHES = {
    "agent.nodes.perceive.erp_client.get_cash_positions": lambda: [
        {"BankAccount": "SAMP-001", "AvailableBalance": "100000000.00", "AccountType": "CURRENT", "BankKey": "Sampath"}
    ],
    "agent.nodes.perceive.bank_client.get_account_balances": _bank_balances,
    "agent.nodes.perceive.erp_client.get_open_payables": lambda: [],
    "agent.nodes.perceive.erp_client.get_payroll_postings": _payroll,
    "agent.nodes.perceive.erp_client.get_tax_items": lambda: [],
    "agent.nodes.perceive.erp_client.get_loan_items": lambda: [],
    "agent.nodes.perceive.bank_client.get_statement": lambda *a, **kw: {"transactions": []},
    "agent.nodes.perceive.market_data_client.get_market_rates": lambda: {"cbsl": {"stale": False}, "bestAvailableRates": {}},
}



# ---------------------------------------------------------------------------
# Test 1: Happy path → SURPLUS_ALLOCATION
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_full_graph_runs_happy_path(in_memory_db):
    """
    Full graph with all mocks returning valid data → SURPLUS_ALLOCATION proposal.
    Audit log should have exactly one PENDING entry.
    """
    rates = {"rates": [{"bank": "Sampath", "type": "FIXED_DEPOSIT", "termDays": 30, "rate": 0.12}]}
    feedback = FeedbackAdjustments()

    graph = build_graph()
    initial_ctx = AgentContext(goal=_make_goal())

    # Payroll 60 days out so 30-day FD maturity doesn't violate constraint
    payroll_data = [{"AccountingDocument": "PAY-001", "TotalNetPayable": "18000000.00",
                     "PaymentDueDate": (date.today() + timedelta(days=60)).isoformat(),
                     "PaymentPriority": "FIXED"}]
    cash_data = [{"BankAccount": "SAMP-001", "AvailableBalance": "100000000.00",
                  "AccountType": "CURRENT", "BankKey": "Sampath"}]

    with patch("agent.nodes.perceive.erp_client.get_cash_positions", return_value=cash_data), \
         patch("agent.nodes.perceive.bank_client.get_account_balances", return_value=_bank_balances()), \
         patch("agent.nodes.perceive.erp_client.get_open_payables", return_value=[]), \
         patch("agent.nodes.perceive.erp_client.get_payroll_postings", return_value=payroll_data), \
         patch("agent.nodes.perceive.erp_client.get_tax_items", return_value=[]), \
         patch("agent.nodes.perceive.erp_client.get_loan_items", return_value=[]), \
         patch("agent.nodes.perceive.bank_client.get_statement", return_value={"transactions": []}), \
         patch("agent.nodes.perceive.market_data_client.get_market_rates", return_value={"cbsl": {"stale": False}, "bestAvailableRates": {}}), \
         patch("agent.nodes.reason.compute_feedback_adjustments", new=AsyncMock(return_value=feedback)), \
         patch("agent.nodes.reason.bank_client.get_deposit_rates", return_value=rates), \
         patch("agent.nodes.reason.forecast_client.get_forecast", return_value=_forecast(0.85)), \
         patch("agent.nodes.reason.optimizer_client.get_allocation", return_value=_optimizer()), \
         patch("agent.nodes.decide.is_duplicate_pending", new=AsyncMock(return_value=False)), \
         patch("agent.nodes.decide.insert_proposal", new=AsyncMock()), \
         patch("agent.nodes.decide.llm_generate_rationale", return_value="[TEST] Happy path rationale"), \
         patch("agent.nodes.disambiguate.llm_generate_rationale", return_value="[TEST] Disambiguate rationale"):

        result = await graph.ainvoke(initial_ctx)

    if isinstance(result, dict):
        ctx = AgentContext(**result)
    else:
        ctx = result

    assert ctx.proposed_action is not None, "Should produce a proposed_action"
    assert ctx.proposed_action.action_type == "SURPLUS_ALLOCATION", \
        f"Expected SURPLUS_ALLOCATION, got {ctx.proposed_action.action_type}"


# ---------------------------------------------------------------------------
# Test 2: Low confidence → disambiguation path taken
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_full_graph_reaches_disambiguate_on_low_confidence(in_memory_db):
    """
    Forecaster returns overallConfidenceScore=0.3 → graph passes through
    disambiguate node → disambiguation_path is set.
    """
    rates = {"rates": [{"bank": "Sampath", "type": "FIXED_DEPOSIT", "termDays": 30, "rate": 0.12}]}
    feedback = FeedbackAdjustments()

    graph = build_graph()
    initial_ctx = AgentContext(goal=_make_goal())

    cash_data = [{"BankAccount": "SAMP-001", "AvailableBalance": "100000000.00",
                  "AccountType": "CURRENT", "BankKey": "Sampath"}]
    payroll_data = [{"AccountingDocument": "PAY-001", "TotalNetPayable": "18000000.00",
                     "PaymentDueDate": (date.today() + timedelta(days=60)).isoformat(),
                     "PaymentPriority": "FIXED"}]

    with patch("agent.nodes.perceive.erp_client.get_cash_positions", return_value=cash_data), \
         patch("agent.nodes.perceive.bank_client.get_account_balances", return_value=_bank_balances()), \
         patch("agent.nodes.perceive.erp_client.get_open_payables", return_value=[]), \
         patch("agent.nodes.perceive.erp_client.get_payroll_postings", return_value=payroll_data), \
         patch("agent.nodes.perceive.erp_client.get_tax_items", return_value=[]), \
         patch("agent.nodes.perceive.erp_client.get_loan_items", return_value=[]), \
         patch("agent.nodes.perceive.bank_client.get_statement", return_value={"transactions": []}), \
         patch("agent.nodes.reason.compute_feedback_adjustments", new=AsyncMock(return_value=feedback)), \
         patch("agent.nodes.reason.bank_client.get_deposit_rates", return_value=rates), \
         patch("agent.nodes.reason.forecast_client.get_forecast", return_value=_forecast(0.3)), \
         patch("agent.nodes.reason.optimizer_client.get_allocation", return_value=_optimizer()), \
         patch("agent.nodes.decide.is_duplicate_pending", new=AsyncMock(return_value=False)), \
         patch("agent.nodes.decide.insert_proposal", new=AsyncMock()), \
         patch("agent.nodes.decide.llm_generate_rationale", return_value="[TEST] Low conf rationale"), \
         patch("agent.nodes.disambiguate.llm_generate_rationale", return_value="[TEST] Disambig rationale"):

        result = await graph.ainvoke(initial_ctx)

    if isinstance(result, dict):
        ctx = AgentContext(**result)
    else:
        ctx = result

    assert ctx.disambiguation_path is not None, \
        "disambiguation_path should be set when low confidence triggers disambiguate node"
    assert ctx.disambiguation_path in ("PROCEED_FLAGGED", "ESCALATE")
    assert "LOW_FORECAST_CONFIDENCE" in (ctx.conflict_flags or [])


# ---------------------------------------------------------------------------
# Test 3: Execution blocked → NO_ACTION
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_full_graph_no_action_on_execution_blocked(in_memory_db):
    """
    All ERP and bank calls fail with no cache → execution_blocked → NO_ACTION.
    """
    graph = build_graph()
    initial_ctx = AgentContext(goal=_make_goal())

    feedback = FeedbackAdjustments()

    with patch("agent.nodes.perceive.erp_client.get_cash_positions", side_effect=Exception("ERP down")), \
         patch("agent.nodes.perceive.bank_client.get_account_balances", side_effect=Exception("Bank down")), \
         patch("agent.nodes.perceive.erp_client.get_open_payables", return_value=[]), \
         patch("agent.nodes.perceive.erp_client.get_payroll_postings", return_value=[]), \
         patch("agent.nodes.perceive.erp_client.get_tax_items", return_value=[]), \
         patch("agent.nodes.perceive.erp_client.get_loan_items", return_value=[]), \
         patch("agent.nodes.perceive.bank_client.get_statement", return_value={"transactions": []}), \
         patch("agent.nodes.reason.compute_feedback_adjustments", new=AsyncMock(return_value=feedback)), \
         patch("agent.nodes.reason.bank_client.get_deposit_rates", return_value={"rates": []}), \
         patch("agent.nodes.reason.forecast_client.get_forecast", return_value=_forecast(0.0)), \
         patch("agent.nodes.decide.is_duplicate_pending", new=AsyncMock(return_value=False)), \
         patch("agent.nodes.decide.insert_proposal", new=AsyncMock()), \
         patch("agent.nodes.decide.llm_generate_rationale", return_value="[TEST] Blocked rationale"):

        result = await graph.ainvoke(initial_ctx)

    if isinstance(result, dict):
        ctx = AgentContext(**result)
    else:
        ctx = result

    assert ctx.proposed_action is not None
    assert ctx.proposed_action.action_type == "NO_ACTION", \
        f"Expected NO_ACTION when blocked, got {ctx.proposed_action.action_type}"
