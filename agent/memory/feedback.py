"""
agent/memory/feedback.py
========================

Feedback loop — queries the audit log to derive optimizer input adjustments.

Purpose
-------
The feedback loop is one of the three proofs of genuine agency in this system.
Every time a human rejects a proposal, that decision is persisted in
``decision_log``.  At the **start of the next Reason cycle**, this module is
queried to detect patterns and adjust the optimizer's instrument list
*before* calling it.

This means the agent genuinely changes future behaviour based on past human
decisions — not just logging them.

Rule set (v1)
-------------
Rule 1 — **Cap term after repeated long-term rejections**:
    If proposals with ``termDays > 30`` were rejected 2 or more times in
    the last 5 cycles, cap ``max_term_days = 30``.
    Rationale: the human has signalled they are uncomfortable locking cash
    for long periods; the agent respects this preference automatically.

Rule 2 — **No constraint when all recent decisions approved**:
    If all last 5 decisions were approved, return the default
    ``FeedbackAdjustments()`` with no caps.

Design
------
- All queries are delegated to ``agent/db/audit_log.py`` — this module
  contains only the **business logic** for interpreting the query results.
- ``FeedbackAdjustments`` is a simple Pydantic model passed to the Reason node,
  which forwards it to the optimizer call.
- The threshold (2 rejections) and lookback (5 cycles) are configurable via
  module-level constants.

Usage
-----
In ``agent/nodes/reason.py``::

    adjustments = await compute_feedback_adjustments(ctx.goal.company_code)
    # Filter instruments to respect max_term_days
    instruments = [i for i in instruments if i["termDays"] <= adjustments.max_term_days]
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from agent.db.audit_log import count_rejections_for_type, query_last_n_decisions

logger = logging.getLogger(__name__)

# Configurable constants
_LOOKBACK_N = 5
_LONG_TERM_REJECTION_THRESHOLD = 2  # ≥ this many → cap term
_LONG_TERM_CUTOFF_DAYS = 30          # "long term" = termDays > this
_CAPPED_MAX_TERM_DAYS = 30           # cap applied when threshold exceeded
_DEFAULT_MAX_TERM_DAYS = 90          # unconstrained default


class FeedbackAdjustments(BaseModel):
    """
    Optimizer input adjustments derived from the human decision history.

    Attributes
    ----------
    max_term_days:
        Upper bound on ``termDays`` passed to the optimizer.
        Default 90 (unconstrained).  Reduced to 30 if long-term proposals
        have been repeatedly rejected.
    excluded_instruments:
        Instrument types to exclude entirely (currently unused in v1).
    note:
        Human-readable explanation of which rule triggered the adjustment,
        included in the audit log for transparency.
    """

    max_term_days: int = _DEFAULT_MAX_TERM_DAYS
    excluded_instruments: list[str] = []
    note: str | None = None


async def compute_feedback_adjustments(company_code: str) -> FeedbackAdjustments:
    """
    Query the last ``_LOOKBACK_N`` decisions and derive optimizer adjustments.

    This function is the heart of the feedback loop.  It is called once per
    Reason cycle before the optimizer is invoked.

    Parameters
    ----------
    company_code:
        SAP company code (``"1000"``).  Used to scope the decision_log query.

    Returns
    -------
    FeedbackAdjustments
        The adjustments to apply.  If no patterns are detected, returns
        a default ``FeedbackAdjustments()`` with no constraints.

    Examples
    --------
    After 2 long-term deposit rejections in the last 5 cycles::

        adj = await compute_feedback_adjustments("1000")
        assert adj.max_term_days == 30
        assert "30 days" in adj.note

    After 5 consecutive approvals::

        adj = await compute_feedback_adjustments("1000")
        assert adj.max_term_days == 90
        assert adj.note is None
    """
    try:
        recent = await query_last_n_decisions(company_code, n=_LOOKBACK_N)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Feedback query failed (%s) — using default adjustments.", exc)
        return FeedbackAdjustments()

    # Rule 1: Count long-term rejections (termDays > 30)
    long_term_rejections = 0
    for decision in recent:
        if decision.get("human_decision") != "REJECTED":
            continue
        params = decision.get("modified_parameters") or {}
        term_days = int(params.get("termDays", 0))
        if term_days > _LONG_TERM_CUTOFF_DAYS:
            long_term_rejections += 1

    if long_term_rejections >= _LONG_TERM_REJECTION_THRESHOLD:
        note = (
            f"Capped at {_CAPPED_MAX_TERM_DAYS} days: long-term deposits rejected "
            f"{long_term_rejections} time(s) in last {_LOOKBACK_N} cycles."
        )
        logger.info("Feedback: applying term cap — %s", note)
        return FeedbackAdjustments(max_term_days=_CAPPED_MAX_TERM_DAYS, note=note)

    # Rule 2: No constraint (all recent approved, or no history yet)
    return FeedbackAdjustments()
