"""
agent/timeout_checker.py
=========================

Approval timeout scanner for the Treasury Copilot HITL system.

Purpose
-------
Scans ``PENDING`` proposals in the ``decision_log`` table and marks them
``TIMEOUT`` when they have been awaiting a human decision for longer than
``APPROVAL_TIMEOUT_HOURS`` (default 24 hours).

Spec reference
--------------
From ``docs/workplan-v1/07-failure-handling-resilience.md``:

    If the human has not acted on a proposal within ``APPROVAL_TIMEOUT_HOURS``
    (default 24 hours):
    1. The Report node marks the proposal ``TIMEOUT`` in the audit log.
    2. Sends a notification: ``POST NOTIFICATION_WEBHOOK_URL {"event": "APPROVAL_TIMEOUT", ...}``
    3. The Orchestrator restarts a fresh Perceive cycle (does not re-propose the
       same action — fresh data may have changed the situation).

How to run
----------
This is designed to be called from a scheduler (APScheduler, cron, or the
LangGraph orchestration loop).  In tests it is called directly with an
overrideable ``timeout_hours`` argument.

Example (scheduler)::

    # In the orchestration loop, check every 10 minutes:
    asyncio.get_event_loop().run_until_complete(
        process_expired_approvals(timeout_hours=24)
    )
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.resilience import send_notification

logger = logging.getLogger(__name__)

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


# Lazy engine/session — created once on first use.
# Reuses the same shared DB as the HITL API and the Report node.
_engine = None
_SessionLocal = None


def _get_session_factory() -> async_sessionmaker:
    """Lazy-initialise the SQLAlchemy async session factory."""
    global _engine, _SessionLocal
    if _SessionLocal is None:
        _engine = create_async_engine(_DATABASE_URL, echo=False)
        _SessionLocal = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
    return _SessionLocal


async def process_expired_approvals(
    timeout_hours: int | float = 24,
    *,
    session_factory: async_sessionmaker | None = None,
) -> list[str]:
    """
    Scan for PENDING proposals that have exceeded the approval timeout.

    For each expired proposal:
    1. Updates ``human_decision = "TIMEOUT"`` and ``decided_at`` in the DB.
    2. Sends a ``POST`` notification to ``NOTIFICATION_WEBHOOK_URL``.

    Parameters
    ----------
    timeout_hours:
        Number of hours after which a PENDING proposal is considered expired.
        Default is 24.  Pass a smaller value in tests to avoid real waits.
    session_factory:
        Provide a custom ``async_sessionmaker`` (for test injection).
        Defaults to the shared production factory.

    Returns
    -------
    list[str]
        List of proposal IDs that were marked TIMEOUT.

    Examples
    --------
    >>> # In tests — inject an in-memory DB:
    >>> timed_out = await process_expired_approvals(
    ...     timeout_hours=24, session_factory=test_session_factory
    ... )
    >>> assert "some-proposal-id" in timed_out
    """
    factory = session_factory or _get_session_factory()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=timeout_hours)

    timed_out_ids: list[str] = []

    async with factory() as session:
        # Find all PENDING proposals proposed before the cutoff
        result = await session.execute(
            sa.text(
                """
                SELECT proposal_id, company_code, description, proposed_at
                FROM decision_log
                WHERE human_decision IS NULL
                  AND proposed_at < :cutoff
                """
            ),
            {"cutoff": cutoff.isoformat()},
        )
        rows = result.fetchall()

        for row in rows:
            proposal_id = row[0]
            company_code = row[1]
            description = row[2]
            proposed_at_str = row[3]

            logger.warning(
                "[timeout_checker] Proposal %s has been PENDING since %s — marking TIMEOUT.",
                proposal_id,
                proposed_at_str,
            )

            # Update to TIMEOUT
            await session.execute(
                sa.text(
                    """
                    UPDATE decision_log
                    SET human_decision = 'TIMEOUT',
                        decided_at = :now,
                        human_note = 'Auto-closed: no human decision received within the approval window.'
                    WHERE proposal_id = :pid
                      AND human_decision IS NULL
                    """
                ),
                {
                    "now": datetime.now(timezone.utc).isoformat(),
                    "pid": proposal_id,
                },
            )

            timed_out_ids.append(proposal_id)

            # Send notification (non-blocking on failure)
            await send_notification(
                "APPROVAL_TIMEOUT",
                {
                    "proposal_id": proposal_id,
                    "company_code": company_code,
                    "description": description,
                    "proposed_at": proposed_at_str,
                    "timeout_hours": timeout_hours,
                    "message": (
                        f"Proposal '{description}' has been pending for >{timeout_hours}h "
                        "without a human decision. It has been auto-closed. "
                        "A fresh Perceive cycle will be started."
                    ),
                },
            )

        await session.commit()

    if timed_out_ids:
        logger.info(
            "[timeout_checker] Marked %d proposal(s) as TIMEOUT: %s",
            len(timed_out_ids),
            timed_out_ids,
        )
    else:
        logger.debug("[timeout_checker] No expired proposals found.")

    return timed_out_ids
