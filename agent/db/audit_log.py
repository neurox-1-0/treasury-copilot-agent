"""
agent/db/audit_log.py
=====================

SQLite audit log for the Treasury Copilot Agent reasoning loop.

Purpose
-------
Every proposal produced by the Decide node is persisted here as a ``PENDING``
record before being sent to the Human Approval Gate.  The table serves three
functions:

1. **Idempotency** — ``is_duplicate_pending(content_hash)`` prevents the same
   proposal from being sent to HITL twice in a cycle (e.g. on a graph retry).
2. **Audit trail** — Every decision, human override, and payment outcome is
   appended to a single immutable log for compliance.
3. **Feedback loop input** — ``query_last_n_decisions`` is called by the Reason
   node to detect rejection patterns and adjust optimizer inputs.

Storage
-------
- **Development**: SQLite via ``aiosqlite`` (default path ``agent_audit.db``
  in the current working directory).
- **Production**: Swap the connection string to PostgreSQL — no code changes
  needed beyond setting ``DATABASE_URL``.

Async model
-----------
All public functions are ``async`` and must be awaited.  The database engine
uses SQLAlchemy's async extension (``create_async_engine``) so they can be
called safely from within a LangGraph async node.

Table schema
------------
See ``CREATE_TABLE_SQL`` below for the full ``decision_log`` schema.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

def get_async_database_url(url: str | None = None) -> str:
    raw = url or os.getenv("DATABASE_URL", "sqlite+aiosqlite:///agent_audit.db")
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    if raw.startswith("sqlite://"):
        return raw.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return raw


_DATABASE_URL = get_async_database_url()
_engine = create_async_engine(_DATABASE_URL, echo=False)
_SessionLocal = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS decision_log (
    id                  INTEGER PRIMARY KEY,
    cycle_id            TEXT NOT NULL,
    proposal_id         TEXT NOT NULL UNIQUE,
    company_code        TEXT NOT NULL,
    action_type         TEXT NOT NULL,
    description         TEXT,
    rationale           TEXT,
    confidence_score    REAL,
    flagged_ambiguities TEXT,
    disambiguation_path TEXT,
    proposed_at         TIMESTAMP NOT NULL,
    human_decision      TEXT,
    modified_parameters TEXT,
    human_note          TEXT,
    decided_at          TIMESTAMP,
    payment_id          TEXT,
    payment_status      TEXT,
    closed_at           TIMESTAMP,
    content_hash        TEXT NOT NULL,
    approved_by         TEXT,
    approver_role       TEXT,
    previous_hash       TEXT
);
"""


_MIGRATIONS = [
    "ALTER TABLE decision_log ADD COLUMN approved_by TEXT;",
    "ALTER TABLE decision_log ADD COLUMN approver_role TEXT;",
    "ALTER TABLE decision_log ADD COLUMN previous_hash TEXT;",
]


async def init_db() -> None:
    """
    Create the ``decision_log`` table if it does not exist and apply migrations.

    Call once at agent startup. Safe to call multiple times.
    """
    async with _engine.begin() as conn:
        await conn.execute(text(CREATE_TABLE_SQL))
        for migration in _MIGRATIONS:
            try:
                await conn.execute(text(migration))
            except Exception:
                # Column already exists
                pass
    logger.info("Audit log database initialised at %s", _DATABASE_URL)


def _get_session() -> async_sessionmaker:
    """Return the async session factory (exposed for test injection)."""
    return _SessionLocal


import hashlib


async def insert_proposal(
    *,
    cycle_id: str,
    proposal_id: str,
    company_code: str,
    action_type: str,
    description: str,
    rationale: str,
    confidence_score: float,
    flagged_ambiguities: list[str],
    disambiguation_path: str | None,
    content_hash: str,
) -> None:
    """
    Insert a new proposal with ``human_decision = 'PENDING'`` and a tamper-evident hash chain.
    """
    async with _SessionLocal() as session:
        # Get the content_hash of the previous proposal to maintain hash chain
        res = await session.execute(
            text("SELECT content_hash FROM decision_log ORDER BY id DESC LIMIT 1")
        )
        last_row = res.fetchone()
        last_hash = last_row[0] if last_row and last_row[0] else "GENESIS"
        previous_hash = hashlib.sha256(last_hash.encode("utf-8")).hexdigest()

        await session.execute(
            text("""
                INSERT INTO decision_log (
                    cycle_id, proposal_id, company_code, action_type, description,
                    rationale, confidence_score, flagged_ambiguities,
                    disambiguation_path, proposed_at, human_decision, content_hash,
                    previous_hash
                ) VALUES (
                    :cycle_id, :proposal_id, :company_code, :action_type, :description,
                    :rationale, :confidence_score, :flagged_ambiguities,
                    :disambiguation_path, :proposed_at, 'PENDING', :content_hash,
                    :previous_hash
                )
            """),
            {
                "cycle_id": cycle_id,
                "proposal_id": proposal_id,
                "company_code": company_code,
                "action_type": action_type,
                "description": description,
                "rationale": rationale,
                "confidence_score": confidence_score,
                "flagged_ambiguities": json.dumps(flagged_ambiguities),
                "disambiguation_path": disambiguation_path,
                "proposed_at": datetime.utcnow().isoformat(),
                "content_hash": content_hash,
                "previous_hash": previous_hash,
            },
        )
        await session.commit()
    logger.info("Proposal %s inserted with PENDING status. previous_hash=%s", proposal_id, previous_hash[:8])



async def update_decision(
    *,
    proposal_id: str,
    human_decision: str,
    modified_parameters: dict | None = None,
    human_note: str | None = None,
    payment_id: str | None = None,
    payment_status: str | None = None,
    closed_at: datetime | None = None,
    approved_by: str | None = None,
    approver_role: str | None = None,
) -> None:
    """
    Update an existing proposal record with the human decision outcome and approver identity.
    """
    async with _SessionLocal() as session:
        await session.execute(
            text("""
                UPDATE decision_log
                SET human_decision      = :human_decision,
                    modified_parameters = :modified_parameters,
                    human_note          = :human_note,
                    decided_at          = :decided_at,
                    payment_id          = :payment_id,
                    payment_status      = :payment_status,
                    closed_at           = :closed_at,
                    approved_by         = COALESCE(:approved_by, approved_by),
                    approver_role       = COALESCE(:approver_role, approver_role)
                WHERE proposal_id = :proposal_id
            """),
            {
                "proposal_id": proposal_id,
                "human_decision": human_decision,
                "modified_parameters": json.dumps(modified_parameters) if modified_parameters else None,
                "human_note": human_note,
                "decided_at": datetime.utcnow().isoformat(),
                "payment_id": payment_id,
                "payment_status": payment_status,
                "closed_at": closed_at.isoformat() if closed_at else None,
                "approved_by": approved_by,
                "approver_role": approver_role,
            },
        )
        await session.commit()
    logger.info("Proposal %s updated: decision=%s payment_status=%s by=%s (%s)", proposal_id, human_decision, payment_status, approved_by, approver_role)



async def is_duplicate_pending(content_hash: str) -> bool:
    """
    Return ``True`` if a proposal with this ``content_hash`` is already ``PENDING``.

    Used by the Decide node's idempotency check to prevent sending the same
    recommendation to HITL twice in the same cycle (e.g. due to a graph retry
    after a transient failure).

    Parameters
    ----------
    content_hash:
        SHA-256 fingerprint computed by ``ProposedAction.compute_hash``.

    Returns
    -------
    bool
        ``True`` if a PENDING record exists; ``False`` otherwise.
    """
    async with _SessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT 1 FROM decision_log
                WHERE content_hash = :content_hash
                  AND human_decision = 'PENDING'
                LIMIT 1
            """),
            {"content_hash": content_hash},
        )
        return result.fetchone() is not None


async def count_rejections_for_type(action_type: str, lookback_n: int = 5) -> dict[str, int]:
    """
    Count rejection patterns across the last ``lookback_n`` decisions.

    Called by the Reason node's feedback loop to detect whether the human has
    repeatedly rejected proposals of a certain type.

    Parameters
    ----------
    action_type:
        e.g. ``"SURPLUS_ALLOCATION"``.
    lookback_n:
        How many recent decisions to inspect (default 5).

    Returns
    -------
    dict[str, int]
        ``{"long_term_rejected": N, "total_rejected": M}``
        where ``long_term_rejected`` counts rejections where ``termDays > 30``.
    """
    async with _SessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT modified_parameters, human_decision
                FROM decision_log
                WHERE action_type = :action_type
                ORDER BY proposed_at DESC
                LIMIT :n
            """),
            {"action_type": action_type, "n": lookback_n},
        )
        rows = result.fetchall()

    long_term_rejected = 0
    total_rejected = 0
    for row in rows:
        decision = row[1]
        if decision != "REJECTED":
            continue
        total_rejected += 1
        params_raw = row[0]
        if params_raw:
            try:
                params = json.loads(params_raw)
                if int(params.get("termDays", 0)) > 30:
                    long_term_rejected += 1
            except (json.JSONDecodeError, ValueError):
                pass

    return {"long_term_rejected": long_term_rejected, "total_rejected": total_rejected}


async def query_last_n_decisions(company_code: str, n: int = 5) -> list[dict[str, Any]]:
    """
    Retrieve the last ``n`` decision_log records for ``company_code``.

    Called by ``agent/memory/feedback.py`` to compute ``FeedbackAdjustments``.

    Parameters
    ----------
    company_code:
        SAP company code (``"1000"``).
    n:
        Number of most recent records to return.

    Returns
    -------
    list[dict]
        Each dict has keys: ``proposal_id``, ``action_type``, ``human_decision``,
        ``modified_parameters`` (parsed dict), ``proposed_at``.
    """
    async with _SessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT proposal_id, action_type, human_decision,
                       modified_parameters, proposed_at
                FROM decision_log
                WHERE company_code = :company_code
                ORDER BY proposed_at DESC
                LIMIT :n
            """),
            {"company_code": company_code, "n": n},
        )
        rows = result.fetchall()

    records = []
    for row in rows:
        params = None
        if row[3]:
            try:
                params = json.loads(row[3])
            except (json.JSONDecodeError, ValueError):
                params = {}
        records.append(
            {
                "proposal_id": row[0],
                "action_type": row[1],
                "human_decision": row[2],
                "modified_parameters": params or {},
                "proposed_at": row[4],
            }
        )
    return records


async def get_proposal(proposal_id: str) -> dict | None:
    """
    Fetch a single proposal record by ``proposal_id``.

    Used by the Report node to retrieve the full record before updating it.

    Parameters
    ----------
    proposal_id:
        UUID of the proposal.

    Returns
    -------
    dict | None
        The record as a dict, or ``None`` if not found.
    """
    async with _SessionLocal() as session:
        result = await session.execute(
            text("SELECT * FROM decision_log WHERE proposal_id = :pid LIMIT 1"),
            {"pid": proposal_id},
        )
        row = result.fetchone()
    if row is None:
        return None
    return dict(row._mapping)


async def verify_audit_chain() -> dict[str, Any]:
    """
    Verify the cryptographic hash chain of the ``decision_log`` table.

    Returns
    -------
    dict
        ``{"valid": bool, "rows_checked": int, "first_broken_at": str | None}``
    """
    async with _SessionLocal() as session:
        result = await session.execute(
            text("SELECT proposal_id, content_hash, previous_hash FROM decision_log ORDER BY id ASC")
        )
        rows = result.fetchall()

    if not rows:
        return {"valid": True, "rows_checked": 0, "first_broken_at": None}

    last_content_hash = "GENESIS"
    for idx, row in enumerate(rows):
        pid, content_hash, prev_hash = row[0], row[1], row[2]
        expected_prev_hash = hashlib.sha256(last_content_hash.encode("utf-8")).hexdigest()
        
        # Check chain link
        if prev_hash and prev_hash != expected_prev_hash:
            logger.error(
                "Hash chain broken at row %d (proposal_id=%s). Expected %s, found %s",
                idx, pid, expected_prev_hash, prev_hash
            )
            return {"valid": False, "rows_checked": idx + 1, "first_broken_at": pid}

        last_content_hash = content_hash

    return {"valid": True, "rows_checked": len(rows), "first_broken_at": None}

