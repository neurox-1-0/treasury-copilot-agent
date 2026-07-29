"""
agent/tests/conftest.py
========================

Shared fixtures for all agent tests.

Fixtures provided
-----------------
``goal``              — Default ``TreasuryGoal`` for company "1000"
``base_ctx``          — Minimal ``AgentContext`` with goal and cycle_id
``treasury_state``    — A realistic ``TreasuryState`` for happy-path tests
``ctx_with_state``    — ``AgentContext`` with treasury_state populated
``in_memory_db``      — Initialises audit log on an in-memory SQLite database
``mock_forecast``     — Default high-confidence forecast result dict
``mock_optimizer``    — Default feasible optimizer result dict

All tests that involve the audit log should use ``in_memory_db`` to avoid
touching any real database file.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest

from agent.state import (
    AgentContext,
    DataFreshness,
    DataSourceStatus,
    Obligation,
    TreasuryGoal,
    TreasuryState,
)


# ---------------------------------------------------------------------------
# Goal & Context fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def goal() -> TreasuryGoal:
    """Default treasury goal for company code 1000."""
    return TreasuryGoal(
        company_code="1000",
        currency="LKR",
        minimum_liquidity_buffer=Decimal("20000000.00"),
        target_yield_minimum=0.10,
        max_payment_risk_days=10,
        goal_profile="BALANCED",
    )


@pytest.fixture
def base_ctx(goal: TreasuryGoal) -> AgentContext:
    """Minimal AgentContext with goal only (before Perceive runs)."""
    return AgentContext(goal=goal, cycle_id=str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# TreasuryState fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def treasury_state(goal: TreasuryGoal) -> TreasuryState:
    """
    A realistic TreasuryState representing a healthy position:
    - total balance: LKR 100M
    - available surplus: LKR 80M (100M - 20M buffer)
    - one upcoming fixed obligation (payroll) in 5 days
    - one flexible vendor AP due in 15 days
    - all data sources fresh
    - no unreconciled credits
    """
    today = date.today()
    from datetime import timedelta

    fixed_ob = Obligation(
        obligation_id="PAY-001",
        obligation_type="PAYROLL",
        amount=Decimal("18000000.00"),
        due_date=today + timedelta(days=5),
        payment_priority="FIXED",
        description="Monthly payroll",
    )
    flexible_ob = Obligation(
        obligation_id="AP-001",
        obligation_type="VENDOR_AP",
        amount=Decimal("5000000.00"),
        due_date=today + timedelta(days=15),
        payment_priority="FLEXIBLE",
        vendor_id="V001",
        description="Vendor invoice",
    )

    return TreasuryState(
        company_code="1000",
        as_of=datetime.utcnow(),
        total_available_balance=Decimal("100000000.00"),
        accounts=[{"accountId": "SAMP-001", "availableBalance": "100000000.00"}],
        obligations=[fixed_ob, flexible_ob],
        fixed_obligations=[fixed_ob],
        flexible_obligations=[flexible_ob],
        next_fixed_obligation_date=fixed_ob.due_date,
        next_fixed_obligation_amount=fixed_ob.amount,
        available_surplus=Decimal("80000000.00"),
        data_source_statuses=[
            DataSourceStatus(source="ERP_CASH_POSITION", freshness=DataFreshness.FRESH, last_fresh_at=datetime.utcnow()),
            DataSourceStatus(source="BANK_BALANCE", freshness=DataFreshness.FRESH, last_fresh_at=datetime.utcnow()),
        ],
        has_stale_data=False,
        unreconciled_large_credits=[],
        execution_blocked=False,
    )


@pytest.fixture
def ctx_with_state(base_ctx: AgentContext, treasury_state: TreasuryState) -> AgentContext:
    """AgentContext with treasury_state populated (post-Perceive)."""
    base_ctx.treasury_state = treasury_state
    return base_ctx


# ---------------------------------------------------------------------------
# Forecast & Optimizer fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_forecast() -> dict:
    """High-confidence forecast result (overallConfidenceScore = 0.85)."""
    from datetime import timedelta
    today = date.today()
    return {
        "companyCode": "1000",
        "forecastHorizonDays": 14,
        "generatedAt": datetime.utcnow().isoformat(),
        "modelType": "STUB",
        "forecast": [
            {
                "date": (today + timedelta(days=i)).isoformat(),
                "predictedNetCashFlow": "2000000.00",
                "confidenceLow": "1000000.00",
                "confidenceHigh": "3000000.00",
                "dayConfidenceScore": 0.85,
            }
            for i in range(14)
        ],
        "overallConfidenceScore": 0.85,
        "flags": [],
        "fallbackUsed": False,
        "fallbackReason": None,
    }


@pytest.fixture
def mock_optimizer(treasury_state: TreasuryState) -> dict:
    """
    Feasible optimizer result recommending a 30-day FD allocation.

    Amount chosen is well within the available_surplus to avoid constraint violations.
    """
    from datetime import timedelta
    today = date.today()
    alloc_amount = Decimal("15000000.00")
    maturity = today + timedelta(days=30)

    return {
        "recommendedAllocation": [
            {
                "bank": "Sampath Bank PLC",
                "instrument": "FIXED_DEPOSIT",
                "termDays": 30,
                "amount": str(alloc_amount),
                "maturityDate": maturity.isoformat(),
                "expectedYield": "493150.68",
                "yieldRate": 0.12,
            }
        ],
        "alternativesConsidered": [
            {
                "bank": "HNB",
                "instrument": "FIXED_DEPOSIT",
                "termDays": 90,
                "amount": str(alloc_amount),
                "maturityDate": (today + timedelta(days=90)).isoformat(),
                "expectedYield": "1643835.62",
                "yieldRate": 0.135,
                "rejectedReason": "Maturity exceeds next fixed obligation date.",
            }
        ],
        "constraintsSatisfied": True,
        "infeasibilityReason": None,
        "costOfDebtHurdleBreached": False,
        "hurdleNote": None,
        "solverUsed": "scipy",
        "bufferAfterDeployment": "85000000.00",  # 100M - 15M = 85M > 20M buffer
    }


# ---------------------------------------------------------------------------
# In-memory DB fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def in_memory_db():
    """
    Override the audit log database with an in-memory SQLite instance.

    Patches ``agent.db.audit_log._SessionLocal`` and ``_engine`` to use
    ``sqlite+aiosqlite:///:memory:``.  Initialises the schema and yields.
    Tears down the engine after the test.
    """
    import agent.db.audit_log as audit_module
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    test_session = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    # Patch module-level globals
    original_engine = audit_module._engine
    original_session = audit_module._SessionLocal

    audit_module._engine = test_engine
    audit_module._SessionLocal = test_session

    await audit_module.init_db()

    yield test_session

    audit_module._engine = original_engine
    audit_module._SessionLocal = original_session
    await test_engine.dispose()
