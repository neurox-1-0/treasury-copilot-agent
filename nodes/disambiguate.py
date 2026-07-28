"""
agent/nodes/disambiguate.py
============================

**Node 4: Disambiguate** — Stakes-based escalation decision.

Purpose
-------
When the Confidence Check routes here, the Disambiguate node decides whether
to **proceed with the proposal (flagged)** or **escalate to human judgment**
before any action is proposed.

This is **one of the three proofs of genuine agency**:
- The path decision (ESCALATE vs PROCEED_FLAGGED) is made by **deterministic
  rules** (the stakes score formula).
- The LLM is called **only** to generate a human-readable rationale for the
  chosen path — not to make the decision itself.

Stakes score formula
--------------------
::

    amount_ratio = available_surplus / minimum_liquidity_buffer
    affects_fixed = "UNRECONCILED_LARGE_CREDIT" or "STALE_DATA_PRESENT" in flags
    stakes_score = min(1.0, amount_ratio * 0.5 + (0.5 if affects_fixed else 0.0))

The threshold is 0.6 (configurable via ``_STAKES_THRESHOLD``).

- ``stakes_score >= 0.6`` → ``ESCALATE``
- ``stakes_score < 0.6``  → ``PROCEED_FLAGGED``

Escalation path
---------------
``ESCALATE`` sends the ``AgentContext`` to the Decide node with
``action_type = "ESCALATE"``.  The agent does **not** attempt a recommendation;
it surfaces the ambiguity for human judgment.

Proceed-flagged path
--------------------
``PROCEED_FLAGGED`` continues to the Decide node normally.  All active
``conflict_flags`` are copied to ``ProposedAction.flagged_ambiguities``.

Input / Output
--------------
- Input:  ``AgentContext`` with ``conflict_flags``, ``treasury_state``.
- Output: ``AgentContext`` with ``disambiguation_path``, ``disambiguation_rationale``.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from agent.prompts.rationale import llm_generate_rationale
from agent.state import AgentContext

logger = logging.getLogger(__name__)

_STAKES_THRESHOLD = 0.6  # escalate if stakes_score >= this


def compute_stakes_score(ctx: AgentContext) -> float:
    """
    Compute a normalised stakes score (0–1) for this disambiguation decision.

    Higher score = higher stakes = lean toward escalation.

    Parameters
    ----------
    ctx:
        Current ``AgentContext``.

    Returns
    -------
    float
        Stakes score clamped to [0.0, 1.0].

    Formula
    -------
    ::

        amount_ratio = available_surplus / minimum_liquidity_buffer
        affects_fixed = UNRECONCILED_LARGE_CREDIT or STALE_DATA_PRESENT in flags
        score = min(1.0, amount_ratio * 0.5 + (0.5 if affects_fixed else 0.0))

    Rationale
    ---------
    - A large surplus relative to the buffer means more money is at stake.
    - Unreconciled credits or stale data affecting fixed obligations adds 0.5
      to the score (fixed obligations cannot be deferred — accuracy is critical).
    """
    ts = ctx.treasury_state
    if ts is None:
        return 0.0

    buffer = ctx.goal.minimum_liquidity_buffer
    if buffer == 0:
        amount_ratio = 0.0
    else:
        amount_ratio = float(ts.available_surplus / buffer)

    affects_fixed = any(
        f in ctx.conflict_flags
        for f in ["UNRECONCILED_LARGE_CREDIT", "STALE_DATA_PRESENT"]
    )

    score = min(1.0, amount_ratio * 0.5 + (0.5 if affects_fixed else 0.0))
    return score


def disambiguate_node(ctx: AgentContext) -> AgentContext:
    """
    LangGraph node function for the Disambiguate step.

    Computes the stakes score, applies the threshold rule, then calls the
    LLM (or template fallback) to generate a rationale string.

    Parameters
    ----------
    ctx:
        ``AgentContext`` with ``conflict_flags`` and ``treasury_state`` populated.

    Returns
    -------
    AgentContext
        Updated with:
        - ``disambiguation_path``: ``"ESCALATE"`` | ``"PROCEED_FLAGGED"``
        - ``disambiguation_rationale``: human-readable explanation (LLM or template)
    """
    logger.info("[Disambiguate] Starting — cycle %s", ctx.cycle_id)

    stakes = compute_stakes_score(ctx)
    logger.info(
        "[Disambiguate] Stakes score: %.3f (threshold=%.1f) flags=%s",
        stakes, _STAKES_THRESHOLD, ctx.conflict_flags,
    )

    if stakes >= _STAKES_THRESHOLD:
        path = "ESCALATE"
        logger.info("[Disambiguate] → ESCALATE (stakes %.3f >= %.1f)", stakes, _STAKES_THRESHOLD)
    else:
        path = "PROCEED_FLAGGED"
        logger.info("[Disambiguate] → PROCEED_FLAGGED (stakes %.3f < %.1f)", stakes, _STAKES_THRESHOLD)

    ctx.disambiguation_path = path

    # LLM generates the rationale — does NOT make the decision
    rationale = llm_generate_rationale(ctx, path)
    ctx.disambiguation_rationale = rationale

    logger.info("[Disambiguate] Done — path=%s", path)
    return ctx
