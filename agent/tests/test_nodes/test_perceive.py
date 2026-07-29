"""
agent/tests/test_nodes/test_perceive.py
========================================

Unit tests for the Perceive node (``agent/nodes/perceive.py``).

All external calls (ERP client, bank client) are mocked — no live services needed.
The cache is cleared before each test to ensure isolation.

Test cases
----------
1. ``test_perceive_builds_treasury_state_from_mocked_services``
   Happy path: all sources return valid data → TreasuryState is fully populated.

2. ``test_perceive_marks_stale_when_erp_fails``
   ERP cash position call raises → has_stale_data=True with STALE entry.

3. ``test_perceive_sets_execution_blocked_when_cash_missing``
   All ERP and bank calls fail AND cache is empty → execution_blocked=True.

4. ``test_perceive_detects_unreconciled_large_credit``
   Bank statement contains a large CREDIT with no ERP match → flagged.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest

from agent.memory.cache import clear_cache
from agent.nodes.perceive import perceive_node
from agent.state import AgentContext, TreasuryGoal


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear the in-process cache before each test."""
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# Helpers: minimal valid mock responses
# ---------------------------------------------------------------------------

def _cash_positions():
    return [{"BankAccount": "SAMP-001", "AvailableBalance": "100000000.00", "AccountType": "CURRENT", "BankKey": "Sampath"}]

def _bank_balances():
    return [{"accountId": "SAMP-001", "availableBalance": "100000000.00", "currency": "LKR", "accountType": "CURRENT"}]

def _payables():
    today = date.today()
    return [{
        "AccountingDocument": "AP-001",
        "Vendor": "V001",
        "AmountInCompanyCodeCurrency": "5000000.00",
        "NetDueDate": (today + timedelta(days=15)).isoformat(),
        "PaymentPriority": "FLEXIBLE",
        "ClearingStatus": "OPEN",
    }]

def _payroll():
    today = date.today()
    return [{
        "AccountingDocument": "PAY-001",
        "TotalNetPayable": "18000000.00",
        "PaymentDueDate": (today + timedelta(days=5)).isoformat(),
        "PaymentPriority": "FIXED",
        "PostingPeriod": "07/2026",
    }]

def _tax():
    today = date.today()
    return [{
        "AccountingDocument": "TAX-001",
        "TaxType": "VAT",
        "AmountInCompanyCodeCurrency": "2000000.00",
        "StatutoryDueDate": (today + timedelta(days=20)).isoformat(),
        "PaymentPriority": "FIXED",
        "TaxPeriod": "06/2026",
    }]

def _loans():
    today = date.today()
    return [{
        "LoanContract": "LN-0001",
        "InstallmentNumber": 1,
        "TotalInstallmentAmount": "3000000.00",
        "InstallmentDueDate": (today + timedelta(days=30)).isoformat(),
        "PaymentPriority": "FIXED",
        "CovenantFlag": False,
    }]

def _bank_statement():
    return {"transactions": []}


# ---------------------------------------------------------------------------
# Test 1: Happy path
# ---------------------------------------------------------------------------

def test_perceive_builds_treasury_state_from_mocked_services(goal):
    """All ERP and bank calls succeed → TreasuryState is fully populated."""
    ctx = AgentContext(goal=goal)

    with patch("agent.nodes.perceive.erp_client.get_cash_positions", return_value=_cash_positions()), \
         patch("agent.nodes.perceive.bank_client.get_account_balances", return_value=_bank_balances()), \
         patch("agent.nodes.perceive.erp_client.get_open_payables", return_value=_payables()), \
         patch("agent.nodes.perceive.erp_client.get_payroll_postings", return_value=_payroll()), \
         patch("agent.nodes.perceive.erp_client.get_tax_items", return_value=_tax()), \
         patch("agent.nodes.perceive.erp_client.get_loan_items", return_value=_loans()), \
         patch("agent.nodes.perceive.bank_client.get_statement", return_value=_bank_statement()):

        result = perceive_node(ctx)

    ts = result.treasury_state
    assert ts is not None
    assert ts.total_available_balance > 0, "total_available_balance should be positive"
    assert len(ts.obligations) > 0, "Should have at least one obligation"
    assert all(o.payment_priority == "FIXED" for o in ts.fixed_obligations), \
        "All fixed_obligations must have payment_priority == FIXED"
    assert all(o.payment_priority == "FLEXIBLE" for o in ts.flexible_obligations), \
        "All flexible_obligations must have payment_priority == FLEXIBLE"
    assert ts.execution_blocked is False


# ---------------------------------------------------------------------------
# Test 2: ERP cash position fails → stale data
# ---------------------------------------------------------------------------

def test_perceive_marks_stale_when_erp_fails(goal):
    """ERP cash position call raises → has_stale_data=True with STALE/MISSING entry."""
    ctx = AgentContext(goal=goal)

    with patch("agent.nodes.perceive.erp_client.get_cash_positions", side_effect=Exception("ERP timeout")), \
         patch("agent.nodes.perceive.bank_client.get_account_balances", return_value=_bank_balances()), \
         patch("agent.nodes.perceive.erp_client.get_open_payables", return_value=_payables()), \
         patch("agent.nodes.perceive.erp_client.get_payroll_postings", return_value=_payroll()), \
         patch("agent.nodes.perceive.erp_client.get_tax_items", return_value=_tax()), \
         patch("agent.nodes.perceive.erp_client.get_loan_items", return_value=_loans()), \
         patch("agent.nodes.perceive.bank_client.get_statement", return_value=_bank_statement()):

        result = perceive_node(ctx)

    ts = result.treasury_state
    assert ts is not None
    assert ts.has_stale_data is True, "has_stale_data must be True when ERP fails"

    erp_status = next(
        (s for s in ts.data_source_statuses if s.source == "ERP_CASH_POSITION"), None
    )
    assert erp_status is not None
    assert erp_status.freshness.value in ("STALE", "MISSING"), \
        f"ERP_CASH_POSITION freshness should be STALE or MISSING, got {erp_status.freshness}"


# ---------------------------------------------------------------------------
# Test 3: All cash sources fail + empty cache → execution_blocked
# ---------------------------------------------------------------------------

def test_perceive_sets_execution_blocked_when_cash_missing(goal):
    """All ERP and bank calls fail AND cache is empty → execution_blocked=True."""
    ctx = AgentContext(goal=goal)

    with patch("agent.nodes.perceive.erp_client.get_cash_positions", side_effect=Exception("ERP down")), \
         patch("agent.nodes.perceive.bank_client.get_account_balances", side_effect=Exception("Bank down")), \
         patch("agent.nodes.perceive.erp_client.get_open_payables", return_value=[]), \
         patch("agent.nodes.perceive.erp_client.get_payroll_postings", return_value=[]), \
         patch("agent.nodes.perceive.erp_client.get_tax_items", return_value=[]), \
         patch("agent.nodes.perceive.erp_client.get_loan_items", return_value=[]), \
         patch("agent.nodes.perceive.bank_client.get_statement", return_value={"transactions": []}):

        result = perceive_node(ctx)

    ts = result.treasury_state
    assert ts is not None
    assert ts.execution_blocked is True, "execution_blocked must be True when cash data is MISSING"
    assert ts.block_reason is not None and len(ts.block_reason) > 0


# ---------------------------------------------------------------------------
# Test 4: Unreconciled large credit detected
# ---------------------------------------------------------------------------

def test_perceive_detects_unreconciled_large_credit(goal):
    """Bank statement has a LKR 5M CREDIT with no ERP match → flagged."""
    ctx = AgentContext(goal=goal)

    # Bank statement with a large credit on a different date/amount from ERP payables
    today = date.today()
    bank_stmt_with_credit = {
        "transactions": [
            {
                "transactionId": "TXN-001",
                "referenceId": "REF-001",
                "date": today.isoformat(),
                "amount": "5000000.00",
                "direction": "CREDIT",
                "description": "Unknown credit transfer",
            }
        ]
    }

    # ERP payables have different amounts — no match
    erp_payables_no_match = [{
        "AccountingDocument": "AP-001",
        "Vendor": "V001",
        "AmountInCompanyCodeCurrency": "1000000.00",  # different amount
        "NetDueDate": (today + timedelta(days=15)).isoformat(),  # different date
        "PaymentPriority": "FLEXIBLE",
        "ClearingStatus": "OPEN",
    }]

    with patch("agent.nodes.perceive.erp_client.get_cash_positions", return_value=_cash_positions()), \
         patch("agent.nodes.perceive.bank_client.get_account_balances", return_value=_bank_balances()), \
         patch("agent.nodes.perceive.erp_client.get_open_payables", return_value=erp_payables_no_match), \
         patch("agent.nodes.perceive.erp_client.get_payroll_postings", return_value=_payroll()), \
         patch("agent.nodes.perceive.erp_client.get_tax_items", return_value=_tax()), \
         patch("agent.nodes.perceive.erp_client.get_loan_items", return_value=_loans()), \
         patch("agent.nodes.perceive.bank_client.get_statement", return_value=bank_stmt_with_credit):

        result = perceive_node(ctx)

    ts = result.treasury_state
    assert ts is not None
    assert len(ts.unreconciled_large_credits) > 0, \
        "Should detect at least one unreconciled large credit"
    assert float(ts.unreconciled_large_credits[0]["amount"]) >= 100000
