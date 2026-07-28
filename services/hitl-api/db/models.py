"""
services/hitl-api/db/models.py
================================

Async SQLAlchemy access layer for the ``decision_log`` table.

Design notes
------------
- **Shared table**: This module reads and writes the same ``decision_log``
  table that ``agent/db/audit_log.py`` creates and populates.  No second
  schema is defined here.  The HITL API and the agent use the same
  ``DATABASE_URL`` environment variable (default:
  ``sqlite+aiosqlite:///agent_audit.db``).  For multi-process dev, both
  processes must point at the same file path.

- **No SQLAlchemy ORM models**: We use raw ``text()`` queries that exactly
  mirror the ``CREATE_TABLE_SQL`` in ``agent/db/audit_log.py``.  This keeps
  the coupling surface minimal — if the agent team alters the schema, both
  sides update together.

- **parameter_bounds storage**: The ``decision_log`` schema does not have a
  ``parameter_bounds`` column.  We derive bounds from the ``rationale`` field
  for simple cases (SURPLUS_ALLOCATION / termDays), or from a separate
  ``parameter_bounds`` JSON column added by this module's
  ``ensure_parameter_bounds_column()`` migration helper.  The column is added
  lazily on first use so the existing DB is not broken.

Functions
---------
init_db()
    Create table + add ``parameter_bounds`` column if missing.  Call at startup.
get_proposals(status)
    Fetch all rows matching the given status string.
get_proposal_by_id(proposal_id)
    Fetch a single row or return None.
record_decision(proposal_id, decision, modified_parameters, human_note)
    Write the human decision back to the row.
get_audit_log(from_date, to_date, action_type, decision, limit, offset)
    Paginated audit history with optional filters.
get_feedback_insights(company_code, days)
    Aggregate approval / rejection / modification counts and detect patterns.
seed_proposal(...)
    Test helper — inserts a synthetic row directly.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine — same DATABASE_URL as agent/db/audit_log.py
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


# ---------------------------------------------------------------------------
# Table creation / migration
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
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

_CREATE_GOAL_PARAMS_SQL = """
CREATE TABLE IF NOT EXISTS goal_parameters (
    company_code            TEXT PRIMARY KEY,
    minimum_liquidity_buffer TEXT NOT NULL,
    target_yield_minimum    REAL NOT NULL,
    max_payment_risk_days   INTEGER NOT NULL,
    goal_profile            TEXT NOT NULL DEFAULT 'BALANCED',
    updated_by              TEXT,
    updated_at              TIMESTAMP
);
"""

_CREATE_ADMIN_AUDIT_SQL = """
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id          INTEGER PRIMARY KEY,
    changed_by  TEXT NOT NULL,
    changed_at  TIMESTAMP NOT NULL,
    field_name  TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT NOT NULL,
    company_code TEXT NOT NULL
);
"""


_MIGRATIONS = [
    "ALTER TABLE decision_log ADD COLUMN parameter_bounds TEXT;",
    "ALTER TABLE decision_log ADD COLUMN approved_by TEXT;",
    "ALTER TABLE decision_log ADD COLUMN approver_role TEXT;",
    "ALTER TABLE decision_log ADD COLUMN previous_hash TEXT;",
]


async def init_db() -> None:
    """
    Create tables and execute migrations (idempotent).
    """
    async with _engine.begin() as conn:
        await conn.execute(text(_CREATE_TABLE_SQL))
        await conn.execute(text(_CREATE_GOAL_PARAMS_SQL))
        await conn.execute(text(_CREATE_ADMIN_AUDIT_SQL))
        for migration in _MIGRATIONS:
            try:
                await conn.execute(text(migration))
            except Exception:
                pass
    logger.info("HITL API: DB ready at %s", _DATABASE_URL)



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json(raw: str | None) -> Any:
    """Parse a JSON string column value, returning the Python object or None."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _row_to_dict(row_mapping: Any) -> dict:
    """Convert a SQLAlchemy row mapping to a plain dict."""
    d = dict(row_mapping)
    # Parse JSON columns
    d["flagged_ambiguities"] = _parse_json(d.get("flagged_ambiguities")) or []
    d["modified_parameters"] = _parse_json(d.get("modified_parameters"))
    d["parameter_bounds"] = _parse_json(d.get("parameter_bounds")) or {}
    return d


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


async def get_proposals(status: str | None = None) -> list[dict]:
    """
    Fetch all ``decision_log`` rows matching ``status``.

    Parameters
    ----------
    status:
        Filter by ``human_decision`` value.  ``"PENDING"`` matches rows where
        ``human_decision = 'PENDING'``.  Pass ``None`` to return all rows.

    Returns
    -------
    list[dict]
        List of row dicts with parsed JSON columns.
    """
    async with _SessionLocal() as session:
        if status is not None:
            result = await session.execute(
                text(
                    "SELECT * FROM decision_log "
                    "WHERE human_decision = :status "
                    "ORDER BY proposed_at DESC"
                ),
                {"status": status},
            )
        else:
            result = await session.execute(
                text("SELECT * FROM decision_log ORDER BY proposed_at DESC")
            )
        rows = result.fetchall()
    return [_row_to_dict(r._mapping) for r in rows]


async def get_proposal_by_id(proposal_id: str) -> dict | None:
    """
    Fetch a single ``decision_log`` row by ``proposal_id``.

    Returns
    -------
    dict | None
        The row as a dict, or ``None`` if not found.
    """
    async with _SessionLocal() as session:
        result = await session.execute(
            text("SELECT * FROM decision_log WHERE proposal_id = :pid LIMIT 1"),
            {"pid": proposal_id},
        )
        row = result.fetchone()
    if row is None:
        return None
    return _row_to_dict(row._mapping)


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


async def record_decision(
    *,
    proposal_id: str,
    decision: str,
    modified_parameters: dict | None = None,
    human_note: str | None = None,
    approved_by: str | None = None,
    approver_role: str | None = None,
) -> None:
    """
    Write a human decision back to the ``decision_log`` row with approver identity.
    """
    async with _SessionLocal() as session:
        await session.execute(
            text("""
                UPDATE decision_log
                SET human_decision      = :decision,
                    modified_parameters = :modified_params,
                    human_note          = :human_note,
                    decided_at          = :decided_at,
                    approved_by         = :approved_by,
                    approver_role       = :approver_role
                WHERE proposal_id = :proposal_id
            """),
            {
                "decision": decision,
                "modified_params": json.dumps(modified_parameters) if modified_parameters else None,
                "human_note": human_note,
                "decided_at": datetime.now(timezone.utc).isoformat(),
                "approved_by": approved_by,
                "approver_role": approver_role,
                "proposal_id": proposal_id,
            },
        )
        await session.commit()
    logger.info("Proposal %s → %s by %s (%s)", proposal_id, decision, approved_by, approver_role)


# ---------------------------------------------------------------------------
# Goal Parameters & Admin Audit Log
# ---------------------------------------------------------------------------


async def get_goal_parameters(company_code: str = "1000") -> dict:
    """
    Fetch goal parameters for company_code, falling back to defaults if unset.
    """
    async with _SessionLocal() as session:
        res = await session.execute(
            text("SELECT * FROM goal_parameters WHERE company_code = :company_code LIMIT 1"),
            {"company_code": company_code},
        )
        row = res.fetchone()

    if row:
        d = dict(row._mapping)
        return {
            "company_code": d["company_code"],
            "minimum_liquidity_buffer": d["minimum_liquidity_buffer"],
            "target_yield_minimum": float(d["target_yield_minimum"]),
            "max_payment_risk_days": int(d["max_payment_risk_days"]),
            "goal_profile": d.get("goal_profile", "BALANCED"),
            "updated_by": d.get("updated_by"),
            "updated_at": d.get("updated_at"),
        }

    # Defaults
    return {
        "company_code": company_code,
        "minimum_liquidity_buffer": "20000000.00",
        "target_yield_minimum": 0.10,
        "max_payment_risk_days": 10,
        "goal_profile": "BALANCED",
        "updated_by": None,
        "updated_at": None,
    }


async def update_goal_parameters(
    *,
    company_code: str = "1000",
    updates: dict[str, Any],
    admin_username: str,
) -> dict:
    """
    Update standing goal parameters and log all changes to admin_audit_log.
    """
    current = await get_goal_parameters(company_code)

    now = datetime.now(timezone.utc).isoformat()
    new_buffer = str(updates.get("minimum_liquidity_buffer", current["minimum_liquidity_buffer"]))
    new_yield = float(updates.get("target_yield_minimum", current["target_yield_minimum"]))
    new_risk_days = int(updates.get("max_payment_risk_days", current["max_payment_risk_days"]))
    new_profile = str(updates.get("goal_profile", current["goal_profile"]))

    async with _SessionLocal() as session:
        # Audit each changed field
        field_map = [
            ("minimum_liquidity_buffer", str(current["minimum_liquidity_buffer"]), new_buffer),
            ("target_yield_minimum", str(current["target_yield_minimum"]), str(new_yield)),
            ("max_payment_risk_days", str(current["max_payment_risk_days"]), str(new_risk_days)),
            ("goal_profile", str(current["goal_profile"]), new_profile),
        ]

        for field_name, old_val, new_val in field_map:
            if old_val != new_val:
                await session.execute(
                    text("""
                        INSERT INTO admin_audit_log (
                            changed_by, changed_at, field_name, old_value, new_value, company_code
                        ) VALUES (
                            :changed_by, :changed_at, :field_name, :old_value, :new_value, :company_code
                        )
                    """),
                    {
                        "changed_by": admin_username,
                        "changed_at": now,
                        "field_name": field_name,
                        "old_value": old_val,
                        "new_value": new_val,
                        "company_code": company_code,
                    },
                )

        # Upsert goal_parameters table
        await session.execute(
            text("""
                INSERT INTO goal_parameters (
                    company_code, minimum_liquidity_buffer, target_yield_minimum,
                    max_payment_risk_days, goal_profile, updated_by, updated_at
                ) VALUES (
                    :company_code, :buffer, :yield_min, :risk_days, :profile, :updated_by, :updated_at
                )
                ON CONFLICT(company_code) DO UPDATE SET
                    minimum_liquidity_buffer = excluded.minimum_liquidity_buffer,
                    target_yield_minimum     = excluded.target_yield_minimum,
                    max_payment_risk_days    = excluded.max_payment_risk_days,
                    goal_profile             = excluded.goal_profile,
                    updated_by               = excluded.updated_by,
                    updated_at               = excluded.updated_at
            """),
            {
                "company_code": company_code,
                "buffer": new_buffer,
                "yield_min": new_yield,
                "risk_days": new_risk_days,
                "profile": new_profile,
                "updated_by": admin_username,
                "updated_at": now,
            },
        )
        await session.commit()

    return await get_goal_parameters(company_code)


async def get_admin_audit_log(company_code: str = "1000", limit: int = 50) -> list[dict]:
    """
    Fetch history of goal parameter changes from admin_audit_log.
    """
    async with _SessionLocal() as session:
        res = await session.execute(
            text("""
                SELECT * FROM admin_audit_log
                WHERE company_code = :company_code
                ORDER BY changed_at DESC
                LIMIT :limit
            """),
            {"company_code": company_code, "limit": limit},
        )
        rows = res.fetchall()

    return [dict(r._mapping) for r in rows]



async def store_parameter_bounds(proposal_id: str, bounds: dict) -> None:
    """
    Persist ``parameter_bounds`` for a proposal.

    Called when a proposal is first inserted via ``seed_proposal`` or via the
    agent insert path, so that constraint re-verification can use the bounds
    later.

    Parameters
    ----------
    proposal_id:
        UUID of the proposal.
    bounds:
        e.g. ``{"termDays": {"min": 1, "max": 14}}``.
    """
    async with _SessionLocal() as session:
        await session.execute(
            text(
                "UPDATE decision_log SET parameter_bounds = :bounds "
                "WHERE proposal_id = :pid"
            ),
            {"bounds": json.dumps(bounds), "pid": proposal_id},
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


async def get_audit_log(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    action_type: str | None = None,
    decision: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    Retrieve a paginated, filtered slice of the ``decision_log``.

    Parameters
    ----------
    from_date:
        ISO date string (``"YYYY-MM-DD"``).  Filters ``proposed_at >= from_date``.
    to_date:
        ISO date string.  Filters ``proposed_at <= to_date + 1 day``.
    action_type:
        e.g. ``"SURPLUS_ALLOCATION"``.  Exact match filter.
    decision:
        e.g. ``"APPROVED"``.  Exact match filter.
    limit:
        Maximum rows to return (default 50, max 200).
    offset:
        Pagination offset.

    Returns
    -------
    list[dict]
        Matching rows with parsed JSON columns.
    """
    limit = min(limit, 200)
    clauses = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if from_date:
        clauses.append("proposed_at >= :from_date")
        params["from_date"] = from_date
    if to_date:
        # Include the full to_date day
        clauses.append("proposed_at < :to_date")
        # Add one day
        try:
            td = datetime.fromisoformat(to_date) + timedelta(days=1)
            params["to_date"] = td.date().isoformat()
        except ValueError:
            params["to_date"] = to_date
    if action_type:
        clauses.append("action_type = :action_type")
        params["action_type"] = action_type
    if decision:
        clauses.append("human_decision = :decision")
        params["decision"] = decision

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM decision_log {where} ORDER BY proposed_at DESC LIMIT :limit OFFSET :offset"

    async with _SessionLocal() as session:
        result = await session.execute(text(sql), params)
        rows = result.fetchall()
    return [_row_to_dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# Feedback insights
# ---------------------------------------------------------------------------


async def get_feedback_insights(
    company_code: str = "1000",
    days: int = 30,
) -> dict:
    """
    Aggregate approval / rejection / modification counts and detect patterns.

    Parameters
    ----------
    company_code:
        SAP company code to filter on.
    days:
        Rolling window in days (default 30).

    Returns
    -------
    dict
        ``{last_30_days: {...}, rejection_patterns: [...]}``.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    async with _SessionLocal() as session:
        # Summary counts
        result = await session.execute(
            text("""
                SELECT human_decision, COUNT(*) as cnt
                FROM decision_log
                WHERE company_code = :company_code
                  AND proposed_at >= :cutoff
                GROUP BY human_decision
            """),
            {"company_code": company_code, "cutoff": cutoff},
        )
        rows = result.fetchall()

    counts: dict[str, int] = {}
    for row in rows:
        decision = row[0] or "PENDING"
        counts[decision] = row[1]

    approved = counts.get("APPROVED", 0)
    rejected = counts.get("REJECTED", 0)
    modified = counts.get("MODIFIED", 0)
    total = approved + rejected + modified
    approval_rate = round(approved / total, 2) if total > 0 else 0.0

    last_30_days = {
        "total_proposals": total,
        "approved": approved,
        "rejected": rejected,
        "modified": modified,
        "approval_rate": approval_rate,
    }

    # Pattern detection — look for repeated long-term rejections
    patterns = []
    async with _SessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT modified_parameters, human_decision, action_type
                FROM decision_log
                WHERE company_code = :company_code
                  AND human_decision = 'REJECTED'
                  AND proposed_at >= :cutoff
                ORDER BY proposed_at DESC
                LIMIT 10
            """),
            {"company_code": company_code, "cutoff": cutoff},
        )
        rejected_rows = result.fetchall()

    long_term_rejected = 0
    for row in rejected_rows:
        params = _parse_json(row[0]) or {}
        if int(params.get("termDays", 0)) > 30:
            long_term_rejected += 1

    if long_term_rejected >= 3:
        patterns.append({
            "pattern": f"Long-term deposits (>30 days) rejected {long_term_rejected} times",
            "agent_adaptation": "Agent has capped default term at 30 days",
        })

    if rejected >= 3 and total > 0 and (rejected / total) > 0.5:
        patterns.append({
            "pattern": f"High rejection rate: {rejected} of {total} proposals rejected",
            "agent_adaptation": "Agent is adjusting confidence thresholds for future proposals",
        })

    return {"last_30_days": last_30_days, "rejection_patterns": patterns}


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------


async def seed_proposal(
    *,
    proposal_id: str,
    cycle_id: str = "test-cycle-001",
    company_code: str = "1000",
    action_type: str = "SURPLUS_ALLOCATION",
    description: str = "Move LKR 8,000,000 into a 14-day fixed deposit at 10%",
    rationale: str = "Surplus exceeds buffer. Short term chosen to avoid payroll conflict.",
    confidence_score: float = 0.87,
    flagged_ambiguities: list | None = None,
    parameter_bounds: dict | None = None,
    content_hash: str = "testhash001",
    human_decision: str = "PENDING",
) -> None:
    """
    Insert a synthetic ``decision_log`` row for tests.

    Used exclusively in ``tests/test_hitl_api.py`` — never in production code.

    Parameters
    ----------
    proposal_id:
        UUID to use for the row.
    ...all other fields:
        Match the ``decision_log`` schema.
    """
    ambiguities = flagged_ambiguities or []
    bounds = parameter_bounds or {"termDays": {"min": 1, "max": 14}}

    async with _SessionLocal() as session:
        await session.execute(
            text("DELETE FROM decision_log WHERE proposal_id = :proposal_id"),
            {"proposal_id": proposal_id},
        )
        await session.execute(
            text("""
                INSERT INTO decision_log (
                    cycle_id, proposal_id, company_code, action_type, description,
                    rationale, confidence_score, flagged_ambiguities,
                    disambiguation_path, proposed_at, human_decision,
                    content_hash, parameter_bounds
                ) VALUES (
                    :cycle_id, :proposal_id, :company_code, :action_type, :description,
                    :rationale, :confidence_score, :flagged_ambiguities,
                    NULL, :proposed_at, :human_decision,
                    :content_hash, :parameter_bounds
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
                "flagged_ambiguities": json.dumps(ambiguities),
                "proposed_at": datetime.now(timezone.utc).isoformat(),
                "human_decision": human_decision,
                "content_hash": content_hash,
                "parameter_bounds": json.dumps(bounds),
            },
        )
        await session.commit()

