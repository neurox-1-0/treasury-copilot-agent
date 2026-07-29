"""
agent/nodes/decide.py
=====================

**Node 5: Decide** — Constraint verification and ``ProposedAction`` assembly.

Purpose
-------
The Decide node is the agent's "brain" that converts the Reason node's
recommendations into a concrete, auditable ``ProposedAction``.  It is strictly
rules-based — no LLM is used for any routing or constraint decision.

Steps (in order)
----------------
1. **Idempotency check** — if the same proposal already has ``PENDING`` status
   in the audit log (detected via ``content_hash``), skip to avoid duplicate HITL
   requests.
2. **Constraint verification** — check for blocked execution, infeasible optimizer,
   and hard financial constraints (allocation ≤ surplus, maturity ≤ next fixed
   obligation, buffer preserved).
3. **Build ProposedAction** — assemble the full proposal from optimizer + forecast
   data, call LLM once for rationale text.
4. **Write to audit log** — persist the proposal as ``PENDING`` before yielding.

Constraint violation handling
------------------------------
**Violations are surfaced explicitly, never silently adjusted.**

If the optimizer's recommended allocation would breach any constraint:
- ``action_type = "CONSTRAINT_VIOLATION"``
- ``description`` names the specific constraint that failed
- The proposal is still written to the audit log for traceability

This is deliberate — silent adjustment would hide errors and make the audit
trail unreliable.

Input / Output
--------------
- Input:  ``AgentContext`` with treasury_state, forecast_result, optimizer_result,
          and optionally disambiguation_path.
- Output: ``AgentContext`` with ``proposed_action`` populated (or
          ``skip_reason = "DUPLICATE_PENDING"``).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal

from agent.db.audit_log import insert_proposal, is_duplicate_pending
from agent.prompts.rationale import llm_generate_rationale
from agent.state import AgentContext, ProposedAction, RejectedAlternative

logger = logging.getLogger(__name__)


async def decide_node(ctx: AgentContext) -> AgentContext:
    """
    LangGraph node function for the Decide step.

    Parameters
    ----------
    ctx:
        ``AgentContext`` with all Reason and Confidence Check outputs.

    Returns
    -------
    AgentContext
        Updated with ``proposed_action`` or ``skip_reason``.
    """
    logger.info("[Decide] Starting — cycle %s", ctx.cycle_id)

    ts = ctx.treasury_state
    opt = ctx.optimizer_result or {}
    forecast = ctx.forecast_result or {}

    # ------------------------------------------------------------------
    # Step 1: Blocked execution → NO_ACTION immediately
    # ------------------------------------------------------------------
    if ts and ts.execution_blocked:
        logger.warning("[Decide] Execution blocked — producing NO_ACTION")
        return await _build_no_action(
            ctx,
            reason=ts.block_reason or "Execution blocked: data unavailable.",
        )

    # ------------------------------------------------------------------
    # Step 2: Infeasible optimizer → NO_ACTION
    # ------------------------------------------------------------------
    if not opt.get("constraintsSatisfied", True):
        logger.warning("[Decide] Optimizer infeasible — producing NO_ACTION")
        return await _build_no_action(
            ctx,
            reason=opt.get("infeasibilityReason") or "Optimizer could not find a feasible allocation.",
        )

    # ------------------------------------------------------------------
    # Step 3: ESCALATE path (set by Disambiguate)
    # ------------------------------------------------------------------
    if ctx.disambiguation_path == "ESCALATE":
        logger.info("[Decide] Disambiguation path = ESCALATE — producing ESCALATE action")
        return await _build_escalate(ctx)

    # ------------------------------------------------------------------
    # Step 4: Verify financial constraints
    # ------------------------------------------------------------------
    allocations = opt.get("recommendedAllocation", [])
    if not allocations:
        return await _build_no_action(ctx, reason="Optimizer returned no allocations.")

    first_alloc = allocations[0]
    alloc_amount = Decimal(str(first_alloc.get("amount", "0")))
    alloc_maturity = _parse_date(str(first_alloc.get("maturityDate", "")))

    # Check: allocation ≤ available_surplus
    if ts and alloc_amount > ts.available_surplus:
        violation = (
            f"Recommended allocation (LKR {alloc_amount:,.2f}) exceeds "
            f"available surplus (LKR {ts.available_surplus:,.2f})."
        )
        logger.warning("[Decide] CONSTRAINT_VIOLATION: %s", violation)
        return await _build_constraint_violation(ctx, violation)

    # Check: maturity ≤ next_fixed_obligation_date
    if ts and ts.next_fixed_obligation_date and alloc_maturity:
        if alloc_maturity > ts.next_fixed_obligation_date:
            violation = (
                f"Allocation maturity ({alloc_maturity}) exceeds next fixed "
                f"obligation date ({ts.next_fixed_obligation_date})."
            )
            logger.warning("[Decide] CONSTRAINT_VIOLATION: %s", violation)
            return await _build_constraint_violation(ctx, violation)

    # Check: buffer preserved after deployment
    buffer_after = Decimal(str(opt.get("bufferAfterDeployment", "0")))
    if buffer_after < ctx.goal.minimum_liquidity_buffer:
        violation = (
            f"Buffer after deployment (LKR {buffer_after:,.2f}) would fall below "
            f"minimum liquidity buffer (LKR {ctx.goal.minimum_liquidity_buffer:,.2f})."
        )
        logger.warning("[Decide] CONSTRAINT_VIOLATION: %s", violation)
        return await _build_constraint_violation(ctx, violation)

    # ------------------------------------------------------------------
    # Step 5: Build action description
    # ------------------------------------------------------------------
    action_type = "SURPLUS_ALLOCATION"
    description = (
        f"Allocate LKR {alloc_amount:,.2f} to "
        f"{first_alloc.get('bank', 'Bank')} "
        f"{first_alloc.get('instrument', 'FD')} "
        f"at {first_alloc.get('yieldRate', 0) * 100:.2f}% "
        f"for {first_alloc.get('termDays', 0)} days "
        f"(expected yield: LKR {float(first_alloc.get('expectedYield', 0)):,.2f})"
    )

    key_params = {
        "termDays": first_alloc.get("termDays"),
        "amount": str(alloc_amount),
        "bank": first_alloc.get("bank"),
        "instrument": first_alloc.get("instrument"),
    }
    content_hash = ProposedAction.compute_hash(action_type, key_params)

    # ------------------------------------------------------------------
    # Step 6: Idempotency check
    # ------------------------------------------------------------------
    try:
        if await is_duplicate_pending(content_hash):
            logger.info("[Decide] Duplicate PENDING detected for hash %s — skipping.", content_hash)
            ctx.skip_reason = "DUPLICATE_PENDING"
            ctx.proposed_action = None
            return ctx
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Decide] Idempotency check failed (%s) — proceeding.", exc)

    # ------------------------------------------------------------------
    # Step 7: Build rejected alternatives list
    # ------------------------------------------------------------------
    rejected_alts = []
    for alt in opt.get("alternativesConsidered", []):
        rejected_alts.append(RejectedAlternative(
            option=f"{alt.get('bank', '')} {alt.get('instrument', '')} {alt.get('termDays', '')}d",
            reason_rejected=alt.get("rejectedReason", "Not selected by optimizer"),
            expected_yield=Decimal(str(alt.get("expectedYield", "0"))),
        ))

    # ------------------------------------------------------------------
    # Step 8: Build parameter_bounds
    # ------------------------------------------------------------------
    term_days = int(first_alloc.get("termDays", 30))
    parameter_bounds = {
        "termDays": {"min": 1, "max": term_days},
        "amount": {"min": str(Decimal("1000000")), "max": str(alloc_amount)},
    }

    # ------------------------------------------------------------------
    # Step 9: Generate rationale (LLM or template)
    # ------------------------------------------------------------------
    rationale = llm_generate_rationale(ctx, action_type)

    # ------------------------------------------------------------------
    # Step 10: Assemble ProposedAction
    # ------------------------------------------------------------------
    proposal = ProposedAction(
        proposal_id=str(uuid.uuid4()),
        action_type=action_type,
        description=description,
        rationale=rationale,
        alternatives_rejected=rejected_alts,
        overall_confidence_score=ctx.confidence_score or 0.0,
        flagged_ambiguities=list(ctx.conflict_flags),
        parameter_bounds=parameter_bounds,
        requires_human_approval=True,
        content_hash=content_hash,
        created_at=datetime.utcnow(),
    )

    # ------------------------------------------------------------------
    # Step 11: Write to audit log
    # ------------------------------------------------------------------
    try:
        await insert_proposal(
            cycle_id=ctx.cycle_id,
            proposal_id=proposal.proposal_id,
            company_code=ctx.goal.company_code,
            action_type=proposal.action_type,
            description=proposal.description,
            rationale=proposal.rationale,
            confidence_score=proposal.overall_confidence_score,
            flagged_ambiguities=proposal.flagged_ambiguities,
            disambiguation_path=ctx.disambiguation_path,
            content_hash=proposal.content_hash,
        )
        logger.info("[Decide] Proposal %s written to audit log (PENDING).", proposal.proposal_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("[Decide] Audit log insert failed: %s", exc)

    ctx.proposed_action = proposal
    logger.info("[Decide] Done — action_type=%s proposal_id=%s", action_type, proposal.proposal_id)
    return ctx


# ---------------------------------------------------------------------------
# Helper builders for special-case actions
# ---------------------------------------------------------------------------

async def _build_no_action(ctx: AgentContext, reason: str) -> AgentContext:
    """Build a NO_ACTION proposal and write it to the audit log."""
    proposal = _make_special_proposal(ctx, "NO_ACTION", reason)
    await _write_proposal(ctx, proposal)
    ctx.proposed_action = proposal
    return ctx


async def _build_escalate(ctx: AgentContext) -> AgentContext:
    """Build an ESCALATE proposal surfacing the active conflict flags."""
    flag_desc = ", ".join(ctx.conflict_flags) if ctx.conflict_flags else "unspecified"
    desc = f"Human review required: {flag_desc}"
    rationale = ctx.disambiguation_rationale or llm_generate_rationale(ctx, "ESCALATE")
    proposal = _make_special_proposal(ctx, "ESCALATE", desc, rationale=rationale)
    await _write_proposal(ctx, proposal)
    ctx.proposed_action = proposal
    return ctx


async def _build_constraint_violation(ctx: AgentContext, violation: str) -> AgentContext:
    """Build a CONSTRAINT_VIOLATION proposal with the specific constraint named."""
    proposal = _make_special_proposal(ctx, "CONSTRAINT_VIOLATION", violation)
    await _write_proposal(ctx, proposal)
    ctx.proposed_action = proposal
    return ctx


def _make_special_proposal(
    ctx: AgentContext, action_type: str, description: str, rationale: str | None = None
) -> ProposedAction:
    content_hash = ProposedAction.compute_hash(action_type, {"description": description[:100]})
    return ProposedAction(
        proposal_id=str(uuid.uuid4()),
        action_type=action_type,
        description=description,
        rationale=rationale or llm_generate_rationale(ctx, action_type),
        overall_confidence_score=ctx.confidence_score or 0.0,
        flagged_ambiguities=list(ctx.conflict_flags),
        content_hash=content_hash,
        created_at=datetime.utcnow(),
    )


async def _write_proposal(ctx: AgentContext, proposal: ProposedAction) -> None:
    """Write a proposal to the audit log, ignoring failures."""
    try:
        await insert_proposal(
            cycle_id=ctx.cycle_id,
            proposal_id=proposal.proposal_id,
            company_code=ctx.goal.company_code,
            action_type=proposal.action_type,
            description=proposal.description,
            rationale=proposal.rationale,
            confidence_score=proposal.overall_confidence_score,
            flagged_ambiguities=proposal.flagged_ambiguities,
            disambiguation_path=ctx.disambiguation_path,
            content_hash=proposal.content_hash,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[Decide] Audit log write failed: %s", exc)


def _parse_date(s: str) -> date | None:
    """Parse an ISO date string; return None on failure."""
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None
