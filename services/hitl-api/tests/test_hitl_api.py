"""
services/hitl-api/tests/test_hitl_api.py
==========================================

Async integration tests for the HITL Approval API.

Test strategy
-------------
- Each test gets a **fresh in-memory SQLite database** by overriding the
  ``DATABASE_URL`` env var before importing ``db.models``.  The module-level
  ``anyio_backend`` and ``pytest.ini`` settings enable async tests.
- We use ``httpx.AsyncClient`` with ``transport=ASGITransport(app=app)`` so
  tests hit the real FastAPI router without starting a network server.
- ``seed_proposal`` from ``db.models`` inserts synthetic rows directly so
  tests are self-contained.
- Test sections match the spec in ``docs/workplan-v1/06-hitl-approval-dashboard.md``:
  1. Proposals list (3 tests)
  2. Decision submission (5 tests)
  3. Audit log (3 tests)
  4. Feedback insights (2 tests)
  5. Chaos endpoint (1 test)

Running
-------
::

    cd services/hitl-api
    pytest tests/ -v

Requirements
------------
pytest, pytest-asyncio, anyio, httpx — all in requirements.txt.
"""

from __future__ import annotations

import os
import uuid

# ── IMPORTANT: patch DATABASE_URL before any app module is imported ──────────
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Import app + DB helpers after env is patched
from db.models import init_db, seed_proposal
from main import app

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def reset_db() -> None:
    """
    Re-initialise the in-memory database before each test.

    Because each test imports the module-level ``_engine`` singleton, we call
    ``init_db()`` to ensure the table exists and is empty.  In-memory SQLite
    is process-scoped so tables persist across calls within the same process;
    we truncate between tests.
    """
    await init_db()
    # Truncate between tests using raw SQL via the engine
    from sqlalchemy import text
    from db.models import _engine
    async with _engine.begin() as conn:
        await conn.execute(text("DELETE FROM decision_log"))


from auth.tokens import create_access_token


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    """Async HTTP client bound to the FastAPI app with Analyst auth header."""
    token = create_access_token({"sub": "analyst1", "role": "ANALYST"})
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    ) as ac:
        yield ac



# ---------------------------------------------------------------------------
# 1. Proposals list
# ---------------------------------------------------------------------------


async def test_get_pending_proposals_returns_correct_shape(client: AsyncClient) -> None:
    """
    Seed one PENDING proposal.  GET /proposals?status=PENDING must return it
    with the required top-level fields.
    """
    pid = str(uuid.uuid4())
    await seed_proposal(
        proposal_id=pid,
        action_type="SURPLUS_ALLOCATION",
        description="Move LKR 8M into 14-day FD",
        confidence_score=0.87,
    )

    resp = await client.get("/proposals", params={"status": "PENDING"})
    assert resp.status_code == 200

    data = resp.json()
    assert "proposals" in data
    assert len(data["proposals"]) == 1

    proposal = data["proposals"][0]
    assert proposal["proposal_id"] == pid
    assert proposal["status"] == "PENDING"
    assert proposal["action_type"] == "SURPLUS_ALLOCATION"
    assert "confidence_score" in proposal
    assert "rationale" in proposal
    assert "parameter_bounds" in proposal
    assert "proposed_at" in proposal


async def test_get_proposals_filtered_by_status(client: AsyncClient) -> None:
    """
    Seed one PENDING and one APPROVED proposal.  Filtering by APPROVED must
    return only the APPROVED one.
    """
    pid_pending = str(uuid.uuid4())
    pid_approved = str(uuid.uuid4())

    await seed_proposal(
        proposal_id=pid_pending,
        content_hash="hash-pending",
        human_decision="PENDING",
    )
    await seed_proposal(
        proposal_id=pid_approved,
        content_hash="hash-approved",
        human_decision="APPROVED",
    )

    resp = await client.get("/proposals", params={"status": "APPROVED"})
    assert resp.status_code == 200

    proposals = resp.json()["proposals"]
    assert len(proposals) == 1
    assert proposals[0]["proposal_id"] == pid_approved
    assert proposals[0]["status"] == "APPROVED"


async def test_get_proposals_empty_when_no_pending(client: AsyncClient) -> None:
    """GET /proposals?status=PENDING with empty DB must return an empty list."""
    resp = await client.get("/proposals", params={"status": "PENDING"})
    assert resp.status_code == 200
    assert resp.json() == {"proposals": []}


# ---------------------------------------------------------------------------
# 2. Decision submission
# ---------------------------------------------------------------------------


async def test_approve_decision_updates_status(client: AsyncClient) -> None:
    """
    Seed a PENDING proposal, submit APPROVED, then verify the row has
    human_decision == APPROVED and decided_at is set.
    """
    pid = str(uuid.uuid4())
    await seed_proposal(proposal_id=pid, content_hash="hash-approve-test")

    resp = await client.post(
        f"/proposals/{pid}/decision",
        json={"decision": "APPROVED"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "APPROVED"
    assert body["proposal_id"] == pid
    assert "recorded_at" in body

    # Verify persistence
    check = await client.get("/proposals", params={"status": "APPROVED"})
    proposals = check.json()["proposals"]
    assert any(p["proposal_id"] == pid for p in proposals)


async def test_reject_decision_updates_status(client: AsyncClient) -> None:
    """
    REJECTED decision with a human note must persist both the decision and
    the note in the decision_log row.
    """
    pid = str(uuid.uuid4())
    await seed_proposal(proposal_id=pid, content_hash="hash-reject-test")

    resp = await client.post(
        f"/proposals/{pid}/decision",
        json={"decision": "REJECTED", "humanNote": "Too long a lock-up period"},
    )
    assert resp.status_code == 200
    assert resp.json()["decision"] == "REJECTED"

    # Fetch via audit log and check the note
    audit = await client.get("/audit-log", params={"decision": "REJECTED"})
    records = audit.json()["records"]
    assert len(records) == 1
    assert records[0]["human_note"] == "Too long a lock-up period"


async def test_modify_with_valid_parameters_succeeds(client: AsyncClient) -> None:
    """
    MODIFIED decision with termDays=7 (within bounds min=1, max=14) must
    return 200 with verificationResult.constraints_satisfied == True.
    """
    pid = str(uuid.uuid4())
    await seed_proposal(
        proposal_id=pid,
        content_hash="hash-modify-valid",
        parameter_bounds={"termDays": {"min": 1, "max": 14}},
    )

    resp = await client.post(
        f"/proposals/{pid}/decision",
        json={
            "decision": "MODIFIED",
            "modifiedParameters": {"termDays": 7},
            "humanNote": "Prefer shorter lock-up",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "MODIFIED"
    assert body["verification_result"]["constraints_satisfied"] is True


async def test_modify_outside_bounds_returns_constraint_violation(
    client: AsyncClient,
) -> None:
    """
    MODIFIED decision with termDays=60 (max=14) must return 400 with
    error == CONSTRAINT_VIOLATION.
    """
    pid = str(uuid.uuid4())
    await seed_proposal(
        proposal_id=pid,
        content_hash="hash-modify-invalid",
        parameter_bounds={"termDays": {"min": 1, "max": 14}},
    )

    resp = await client.post(
        f"/proposals/{pid}/decision",
        json={
            "decision": "MODIFIED",
            "modifiedParameters": {"termDays": 60},
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "CONSTRAINT_VIOLATION"
    assert "60" in body["message"]
    assert "parameter_bounds" in body


async def test_decision_on_unknown_proposal_returns_404(client: AsyncClient) -> None:
    """POST to a non-existent proposal_id must return 404."""
    resp = await client.post(
        "/proposals/NONEXISTENT/decision",
        json={"decision": "APPROVED"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. Audit log
# ---------------------------------------------------------------------------


async def test_audit_log_returns_all_completed_decisions(client: AsyncClient) -> None:
    """
    Seed 3 completed proposals (APPROVED, REJECTED, MODIFIED).
    GET /audit-log must return all 3.
    """
    for i, decision in enumerate(["APPROVED", "REJECTED", "MODIFIED"]):
        await seed_proposal(
            proposal_id=str(uuid.uuid4()),
            content_hash=f"hash-audit-{i}",
            human_decision=decision,
        )

    resp = await client.get("/audit-log")
    assert resp.status_code == 200
    assert resp.json()["count"] == 3


async def test_audit_log_date_filter(client: AsyncClient) -> None:
    """
    GET /audit-log with from_date / to_date filters must only return rows
    whose proposed_at falls within the range.  We test the filter wiring; the
    exact boundary behaviour is DB-level.
    """
    # Seed a proposal — it will have proposed_at = now (within any reasonable range)
    pid = str(uuid.uuid4())
    await seed_proposal(proposal_id=pid, content_hash="hash-date-filter")

    resp = await client.get(
        "/audit-log",
        params={"from_date": "2020-01-01", "to_date": "2030-12-31"},
    )
    assert resp.status_code == 200
    records = resp.json()["records"]
    assert any(r["proposal_id"] == pid for r in records)

    # Filter to a range in the past — proposal should NOT appear
    resp_past = await client.get(
        "/audit-log",
        params={"from_date": "2000-01-01", "to_date": "2000-12-31"},
    )
    assert resp_past.status_code == 200
    assert resp_past.json()["count"] == 0


async def test_audit_log_export_returns_csv(client: AsyncClient) -> None:
    """
    GET /audit-log/export must return Content-Type: text/csv and a valid
    CSV body with a header row.
    """
    await seed_proposal(proposal_id=str(uuid.uuid4()), content_hash="hash-csv-export")

    resp = await client.get("/audit-log/export")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]

    lines = resp.text.strip().splitlines()
    assert len(lines) >= 2  # header + at least one data row
    header = lines[0]
    assert "proposal_id" in header
    assert "action_type" in header
    assert "human_decision" in header


# ---------------------------------------------------------------------------
# 4. Feedback insights
# ---------------------------------------------------------------------------


async def test_feedback_insights_counts_are_correct(client: AsyncClient) -> None:
    """
    Seed 3 APPROVED and 2 REJECTED proposals.
    GET /feedback/insights must return last_30_days.approved==3 and rejected==2.
    """
    for i in range(3):
        await seed_proposal(
            proposal_id=str(uuid.uuid4()),
            content_hash=f"hash-approved-{i}",
            human_decision="APPROVED",
        )
    for i in range(2):
        await seed_proposal(
            proposal_id=str(uuid.uuid4()),
            content_hash=f"hash-rejected-{i}",
            human_decision="REJECTED",
        )

    resp = await client.get("/feedback/insights")
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_30_days"]["approved"] == 3
    assert body["last_30_days"]["rejected"] == 2
    assert body["last_30_days"]["total_proposals"] == 5


async def test_feedback_insights_detects_rejection_pattern(client: AsyncClient) -> None:
    """
    Seed 3 REJECTED decisions for long-term deposits (termDays > 30).
    GET /feedback/insights must return a non-empty rejection_patterns list.
    """
    import json as _json
    from sqlalchemy import text as _text
    from db.models import _engine, _SessionLocal
    from datetime import datetime, timezone

    # Seed REJECTED proposals with long termDays in modified_parameters
    for i in range(3):
        pid = str(uuid.uuid4())
        params = _json.dumps({"termDays": 60})
        async with _SessionLocal() as session:
            await session.execute(
                _text("""
                    INSERT INTO decision_log (
                        cycle_id, proposal_id, company_code, action_type, description,
                        rationale, confidence_score, flagged_ambiguities,
                        disambiguation_path, proposed_at, human_decision,
                        content_hash, modified_parameters
                    ) VALUES (
                        'cycle-001', :pid, '1000', 'SURPLUS_ALLOCATION', 'Long term FD',
                        'rationale', 0.8, '[]', NULL, :now, 'REJECTED',
                        :hash, :params
                    )
                """),
                {
                    "pid": pid,
                    "now": datetime.now(timezone.utc).isoformat(),
                    "hash": f"hash-long-{i}",
                    "params": params,
                },
            )
            await session.commit()

    resp = await client.get("/feedback/insights")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rejection_patterns"]) > 0
    assert any("30 days" in p["pattern"] or "rejected" in p["pattern"].lower() for p in body["rejection_patterns"])


# ---------------------------------------------------------------------------
# 5. Chaos endpoint
# ---------------------------------------------------------------------------


async def test_chaos_toggle_sets_mode_on_bank_mock(client: AsyncClient) -> None:
    """
    POST /chaos {service: bank-mock, mode: timeout} must return 200.
    The bank-mock may not be running during unit tests — the endpoint must
    handle that gracefully and still return 200 with a warning.
    """
    resp = await client.post(
        "/chaos",
        json={"service": "bank-mock", "mode": "timeout"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "bank-mock"
    assert body["mode"] == "timeout"
    # upstream_status may be 200 (if bank-mock running) or 503 (if not)
    assert body["upstream_status"] in (200, 503)
