"""
agent/tests/test_resilience.py
================================

Comprehensive test suite for Component 7: Failure Handling & Resilience.

Tests are organised into four sections matching the spec in
``docs/workplan-v1/07-failure-handling-resilience.md``:

1. Cache behaviour
2. ERP client resilience (STALE / MISSING / no-raise)
3. Bank client — write failure (UNKNOWN / REJECTED / no-retry)
4. Approval timeout (marks TIMEOUT, sends notification)

All tests are unit-level — no real network calls, no real database files.
External services are replaced with ``unittest.mock.patch`` and
``pytest-httpx`` (or simple ``AsyncMock``).

Running
-------
::

    # From the project root (venv active):
    pytest agent/tests/test_resilience.py -v

    # Unit tests only (no real services):
    pytest agent/tests/test_resilience.py -v -m "not integration"
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from agent.memory.cache import DataCache, cache_set, clear_cache
from agent.state import DataFreshness, PaymentWriteStatus


# ===========================================================================
# Helpers
# ===========================================================================

def _make_proposal_id() -> str:
    return str(uuid.uuid4())


# ===========================================================================
# 1. Cache behaviour
# ===========================================================================


class TestCacheBehaviour:
    """Tests for agent/memory/cache.py — the stale-data fallback store."""

    def setup_method(self):
        """Reset the in-process cache before each test."""
        clear_cache()

    def test_cache_set_and_get(self):
        """
        After cache_set, cache_get returns the stored data with STALE
        freshness (the caller only uses cache_get when a live fetch failed).
        """
        from agent.memory.cache import cache_get

        cache_set("TEST_KEY", {"balance": "1000"})
        data, freshness, last_fresh = cache_get("TEST_KEY")

        assert data == {"balance": "1000"}
        assert freshness == DataFreshness.STALE
        assert last_fresh is not None
        assert isinstance(last_fresh, datetime)

    def test_cache_get_returns_missing_for_absent_key(self):
        """
        cache_get on a key never written returns (None, MISSING, None).
        """
        from agent.memory.cache import cache_get

        data, freshness, last_fresh = cache_get("NONEXISTENT_KEY")

        assert data is None
        assert freshness == DataFreshness.MISSING
        assert last_fresh is None

    def test_cache_has_returns_false_when_empty(self):
        """cache_has reflects whether a key is present."""
        from agent.memory.cache import cache_has

        assert not cache_has("MISSING_KEY")
        cache_set("PRESENT_KEY", {})
        assert cache_has("PRESENT_KEY")

    def test_cache_set_overwrites_previous(self):
        """A second cache_set replaces the first."""
        from agent.memory.cache import cache_get

        cache_set("KEY", {"value": 1})
        cache_set("KEY", {"value": 2})
        data, _, _ = cache_get("KEY")
        assert data == {"value": 2}

    def test_clear_cache_removes_all_entries(self):
        """clear_cache empties the DataCache singleton."""
        cache_set("A", {"x": 1})
        cache_set("B", {"x": 2})
        clear_cache()
        assert DataCache == {}


# ===========================================================================
# 2. ERP client resilience
# ===========================================================================


class TestERPClientResilience:
    """
    Tests for the resilient ERP client variants in agent/tools/erp_client.py.

    These ensure that:
    - On failure + cached data → STALE returned, no exception raised
    - On failure + no cache   → MISSING returned, no exception raised
    - On success              → FRESH returned, cache is populated
    """

    def setup_method(self):
        clear_cache()

    def test_erp_client_returns_fresh_on_success(self):
        """
        When the ERP mock responds normally, get_cash_positions_resilient
        returns (data, FRESH, timestamp) and updates the cache.
        """
        from agent.memory.cache import cache_has
        from agent.tools.erp_client import get_cash_positions_resilient

        fresh_data = [{"BankAccount": "SAMP-001", "AvailableBalance": "100000000.00"}]

        with patch(
            "agent.tools.erp_client.get_cash_positions", return_value=fresh_data
        ):
            data, freshness, last_fresh_at = get_cash_positions_resilient("1000")

        assert data == fresh_data
        assert freshness == DataFreshness.FRESH
        assert last_fresh_at is not None
        assert cache_has("ERP_CASH_POSITION_1000")

    def test_erp_client_returns_stale_on_timeout(self):
        """
        When the ERP mock times out (ERPClientError), and the cache has a
        previous successful fetch, STALE data is returned without raising.
        """
        from agent.tools.erp_client import ERPClientError, get_cash_positions_resilient

        stale_data = [{"BankAccount": "SAMP-001", "AvailableBalance": "99000000.00"}]
        cache_set("ERP_CASH_POSITION_1000", stale_data)

        with patch(
            "agent.tools.erp_client.get_cash_positions",
            side_effect=ERPClientError("Connection timed out"),
        ):
            data, freshness, last_fresh_at = get_cash_positions_resilient("1000")

        assert data == stale_data
        assert freshness == DataFreshness.STALE
        assert last_fresh_at is not None

    def test_erp_client_returns_missing_when_no_cache(self):
        """
        When the ERP mock fails AND no cached data exists, MISSING is
        returned.  The Perceive node must set execution_blocked = True.
        """
        from agent.tools.erp_client import ERPClientError, get_cash_positions_resilient

        # No cache pre-population
        with patch(
            "agent.tools.erp_client.get_cash_positions",
            side_effect=ERPClientError("Service unavailable"),
        ):
            data, freshness, last_fresh_at = get_cash_positions_resilient("1000")

        assert data is None
        assert freshness == DataFreshness.MISSING
        assert last_fresh_at is None

    def test_erp_client_does_not_raise_on_failure(self):
        """
        The resilient variant never raises — it always returns a 3-tuple.
        """
        from agent.tools.erp_client import ERPClientError, get_cash_positions_resilient

        with patch(
            "agent.tools.erp_client.get_cash_positions",
            side_effect=ERPClientError("500 Internal Server Error"),
        ):
            result = get_cash_positions_resilient("1000")

        assert isinstance(result, tuple)
        assert len(result) == 3  # (data, freshness, last_fresh_at)


# ===========================================================================
# 3. Bank client — write failure (most critical section)
# ===========================================================================


class TestBankWriteFailure:
    """
    Tests for ``agent.resilience.initiate_payment_safe``.

    The payment write path has the strictest safety requirements:
    - UNKNOWN on timeout (money may have moved; do not retry)
    - REJECTED on 400/422 (definitive; money did not move)
    - SUBMITTED on success (poll for EXECUTED separately)
    - Called exactly ONCE — no retry, ever
    """

    VALID_PAYLOAD = dict(
        source_account_id="SAMP-0012345678",
        beneficiary_account="COMB-0098765432",
        amount=Decimal("8000000.00"),
        currency="LKR",
        purpose="FD Allocation",
        requested_execution_date=__import__("datetime").date.today(),
        reference_note="test-ref",
        token="test-bearer-token",
    )

    @pytest.mark.asyncio
    async def test_payment_submitted_on_200(self):
        """200 response → SUBMITTED with paymentId."""
        from agent.resilience import initiate_payment_safe

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"paymentId": "PMT-001", "status": "PENDING_APPROVAL"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            payment_id, status = await initiate_payment_safe(**self.VALID_PAYLOAD)

        assert status == PaymentWriteStatus.SUBMITTED
        assert payment_id == "PMT-001"

    @pytest.mark.asyncio
    async def test_payment_write_failure_timeout_returns_unknown(self):
        """
        Timeout during payment initiation → UNKNOWN.

        The money may or may not have reached the bank.
        Manual verification required. Do NOT retry.
        """
        import httpx

        from agent.resilience import initiate_payment_safe

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client_cls.return_value = mock_client

            payment_id, status = await initiate_payment_safe(**self.VALID_PAYLOAD)

        assert status == PaymentWriteStatus.UNKNOWN
        assert payment_id is None

    @pytest.mark.asyncio
    async def test_payment_definitive_rejection_on_422(self):
        """
        422 Unprocessable Entity → REJECTED (money did NOT move).
        Covers INSUFFICIENT_FUNDS, INVALID_ACCOUNT, etc.
        """
        from agent.resilience import initiate_payment_safe

        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.text = '{"error": "INSUFFICIENT_FUNDS"}'

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            payment_id, status = await initiate_payment_safe(**self.VALID_PAYLOAD)

        assert status == PaymentWriteStatus.REJECTED
        assert payment_id is None

    @pytest.mark.asyncio
    async def test_payment_server_error_returns_unknown(self):
        """
        5xx from bank → UNKNOWN (not definitive; could be partial)
        """
        from agent.resilience import initiate_payment_safe

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            payment_id, status = await initiate_payment_safe(**self.VALID_PAYLOAD)

        assert status == PaymentWriteStatus.UNKNOWN
        assert payment_id is None

    @pytest.mark.asyncio
    async def test_payment_initiation_called_exactly_once(self):
        """
        CRITICAL: payment POST is called exactly once, regardless of
        the error type. No retry is ever attempted.
        """
        import httpx

        from agent.resilience import initiate_payment_safe

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client_cls.return_value = mock_client

            await initiate_payment_safe(**self.VALID_PAYLOAD)

            # Exactly ONE call — never retried
            assert mock_client.post.call_count == 1


# ===========================================================================
# 4. Approval timeout
# ===========================================================================


class TestApprovalTimeout:
    """
    Tests for ``agent/timeout_checker.py``.

    Uses an in-memory SQLite database (aiosqlite) seeded with proposals
    at controlled timestamps.
    """

    @pytest.fixture(autouse=True)
    async def setup_db(self):
        """
        Provision an in-memory SQLite DB with the decision_log table for each test.
        """
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

        # Create the decision_log table (minimal schema for testing)
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda c: c.execute(
                    __import__("sqlalchemy").text(
                        """
                        CREATE TABLE IF NOT EXISTS decision_log (
                            proposal_id TEXT PRIMARY KEY,
                            cycle_id TEXT,
                            company_code TEXT,
                            action_type TEXT,
                            description TEXT,
                            rationale TEXT,
                            confidence_score REAL,
                            flagged_ambiguities TEXT,
                            parameter_bounds TEXT,
                            alternatives_rejected TEXT,
                            human_decision TEXT,
                            decided_at TEXT,
                            human_note TEXT,
                            modified_parameters TEXT,
                            payment_status TEXT,
                            proposed_at TEXT NOT NULL
                        )
                        """
                    )
                )
            )

        yield

        await self.engine.dispose()

    async def _insert_proposal(
        self, proposal_id: str, proposed_at: datetime, human_decision: str | None = None
    ):
        """Helper: insert a proposal row with a specific timestamp."""
        import sqlalchemy as sa

        async with self.session_factory() as session:
            await session.execute(
                sa.text(
                    """
                    INSERT INTO decision_log
                        (proposal_id, cycle_id, company_code, action_type,
                         description, rationale, confidence_score,
                         flagged_ambiguities, parameter_bounds,
                         alternatives_rejected, human_decision, proposed_at)
                    VALUES
                        (:pid, :cid, '1000', 'SURPLUS_ALLOCATION',
                         'Test proposal', 'Test rationale', 0.85,
                         '[]', '{}', '[]', :decision, :proposed_at)
                    """
                ),
                {
                    "pid": proposal_id,
                    "cid": str(uuid.uuid4()),
                    "decision": human_decision,
                    "proposed_at": proposed_at.isoformat(),
                },
            )
            await session.commit()

    @pytest.mark.asyncio
    async def test_approval_timeout_marks_proposal_as_timeout(self):
        """
        A proposal proposed 25 hours ago with no decision should be
        marked TIMEOUT after process_expired_approvals runs.
        """
        import sqlalchemy as sa

        from agent.timeout_checker import process_expired_approvals

        pid = _make_proposal_id()
        old_time = datetime.now(timezone.utc) - timedelta(hours=25)
        await self._insert_proposal(pid, old_time)

        with patch("agent.timeout_checker.send_notification", new_callable=AsyncMock):
            timed_out = await process_expired_approvals(
                timeout_hours=24, session_factory=self.session_factory
            )

        assert pid in timed_out

        # Verify the DB was updated
        async with self.session_factory() as session:
            result = await session.execute(
                sa.text("SELECT human_decision FROM decision_log WHERE proposal_id = :pid"),
                {"pid": pid},
            )
            row = result.fetchone()
        assert row[0] == "TIMEOUT"

    @pytest.mark.asyncio
    async def test_recent_proposal_not_timed_out(self):
        """
        A proposal created 1 hour ago should NOT be marked TIMEOUT.
        """
        from agent.timeout_checker import process_expired_approvals

        pid = _make_proposal_id()
        recent_time = datetime.now(timezone.utc) - timedelta(hours=1)
        await self._insert_proposal(pid, recent_time)

        with patch("agent.timeout_checker.send_notification", new_callable=AsyncMock):
            timed_out = await process_expired_approvals(
                timeout_hours=24, session_factory=self.session_factory
            )

        assert pid not in timed_out

    @pytest.mark.asyncio
    async def test_approval_timeout_sends_notification(self):
        """
        process_expired_approvals must POST an APPROVAL_TIMEOUT notification
        for each timed-out proposal.
        """
        from agent.timeout_checker import process_expired_approvals

        pid = _make_proposal_id()
        old_time = datetime.now(timezone.utc) - timedelta(hours=25)
        await self._insert_proposal(pid, old_time)

        mock_notify = AsyncMock()
        with patch("agent.timeout_checker.send_notification", mock_notify):
            await process_expired_approvals(
                timeout_hours=24, session_factory=self.session_factory
            )

        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        assert call_args[0][0] == "APPROVAL_TIMEOUT"
        assert call_args[0][1].get("proposal_id") == pid

    @pytest.mark.asyncio
    async def test_already_decided_proposal_not_re_processed(self):
        """
        Proposals that already have a human_decision (APPROVED, REJECTED, etc.)
        must NOT be touched by the timeout checker.
        """
        import sqlalchemy as sa

        from agent.timeout_checker import process_expired_approvals

        pid = _make_proposal_id()
        old_time = datetime.now(timezone.utc) - timedelta(hours=25)
        await self._insert_proposal(pid, old_time, human_decision="APPROVED")

        with patch("agent.timeout_checker.send_notification", new_callable=AsyncMock):
            timed_out = await process_expired_approvals(
                timeout_hours=24, session_factory=self.session_factory
            )

        assert pid not in timed_out

        # Verify the decision was NOT overwritten
        async with self.session_factory() as session:
            result = await session.execute(
                sa.text("SELECT human_decision FROM decision_log WHERE proposal_id = :pid"),
                {"pid": pid},
            )
            row = result.fetchone()
        assert row[0] == "APPROVED"


# ===========================================================================
# 5. PaymentWriteStatus enum
# ===========================================================================


class TestPaymentWriteStatusEnum:
    """Basic contract tests for the PaymentWriteStatus enum in state.py."""

    def test_enum_has_required_members(self):
        """SUBMITTED, REJECTED, and UNKNOWN must exist."""
        assert PaymentWriteStatus.SUBMITTED == "SUBMITTED"
        assert PaymentWriteStatus.REJECTED == "REJECTED"
        assert PaymentWriteStatus.UNKNOWN == "UNKNOWN"

    def test_enum_is_str_subclass(self):
        """PaymentWriteStatus must be a str enum for JSON serialisation."""
        assert isinstance(PaymentWriteStatus.SUBMITTED, str)
