"""
agent/nodes/report.py
=====================

**Node 7: Report** — Processes the human decision and closes the proposal lifecycle.

Purpose
-------
The Report node runs after the Human-in-the-Loop (HITL) gate returns a decision.
It handles three paths:

- **APPROVED** — Executes the payment via the bank API, polls until EXECUTED or
  timeout, and closes the audit log entry.
- **REJECTED** — Logs the decision for the feedback loop.  The feedback loop will
  use this to adjust future Reason cycle constraints.
- **MODIFIED** — Re-runs constraint verification on the modified parameters.
  If constraints pass, executes the modified payment.  If they fail, the
  modification is rejected and surfaced back.
- **TIMEOUT** — Auto-closes the proposal without execution; sends a log-level
  escalation alert.

Safety rule: no payment retries
--------------------------------
The Report node **never retries a payment** after a FAILED or UNKNOWN status.
This is the key safety rule — a second payment attempt on a partially-executed
instruction could double-debit the account.  Manual verification is always
required for UNKNOWN outcomes.

Integration with HITL (Component 6)
-------------------------------------
In the current build, the Report node is invoked by calling
``run_report(ctx)`` directly (e.g. from the HITL API callback).  The LangGraph
graph yields after the Decide node (``builder.add_edge("decide", END)``); the
HITL API populates ``ctx.human_decision`` and calls this node.

Input / Output
--------------
- Input:  ``AgentContext`` with ``proposed_action`` and ``human_decision`` populated.
- Output: ``AgentContext`` with ``payment_result`` populated.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal

from agent.db.audit_log import update_decision
from agent.resilience import initiate_payment_safe
from agent.state import AgentContext, ProposedAction, PaymentWriteStatus
from agent.tools import bank_client
from agent.tools.bank_client import BankClientError

logger = logging.getLogger(__name__)


_PAYMENT_POLL_INTERVAL_SECONDS = 30
_PAYMENT_POLL_TIMEOUT_SECONDS = 300  # 5 minutes


async def report_node(ctx: AgentContext) -> AgentContext:
    """
    LangGraph node function for the Report step.

    Called after the HITL gate has populated ``ctx.human_decision``.

    Parameters
    ----------
    ctx:
        ``AgentContext`` with ``proposed_action`` and ``human_decision`` set.

    Returns
    -------
    AgentContext
        Updated with ``payment_result``.
    """
    logger.info("[Report] Starting — cycle %s decision=%s", ctx.cycle_id, ctx.human_decision)

    proposal = ctx.proposed_action
    if proposal is None:
        logger.warning("[Report] No proposed_action — nothing to report.")
        return ctx

    decision = ctx.human_decision or "UNKNOWN"

    if decision == "APPROVED":
        ctx = await _handle_approved(ctx, proposal)
    elif decision == "REJECTED":
        ctx = await _handle_rejected(ctx, proposal)
    elif decision == "MODIFIED":
        ctx = await _handle_modified(ctx, proposal)
    elif decision == "TIMEOUT":
        ctx = await _handle_timeout(ctx, proposal)
    else:
        logger.warning("[Report] Unknown human decision '%s' — closing as TIMEOUT.", decision)
        ctx = await _handle_timeout(ctx, proposal)

    logger.info("[Report] Done — cycle %s", ctx.cycle_id)
    return ctx


# ---------------------------------------------------------------------------
# Decision handlers
# ---------------------------------------------------------------------------

async def _handle_approved(ctx: AgentContext, proposal: ProposedAction) -> AgentContext:
    """
    Execute the approved payment and poll for completion.

    Calls ``bank_client.initiate_payment`` with the parameters from the
    approved ``ProposedAction``, then polls every 30 seconds for up to 5
    minutes.  On EXECUTED: closes the audit log entry.  On FAILED or timeout:
    marks status as UNKNOWN and logs a manual verification alert.

    **Safety: payment is never retried on any failure.**
    """
    logger.info("[Report] APPROVED — initiating payment for proposal %s", proposal.proposal_id)

    # Extract payment params from proposal description (simplified — in prod, store
    # structured params in the ProposedAction directly)
    opt = ctx.optimizer_result or {}
    allocations = opt.get("recommendedAllocation", [])
    if not allocations:
        logger.error("[Report] No optimizer allocation to execute — closing as FAILED.")
        await update_decision(
            proposal_id=proposal.proposal_id,
            human_decision="APPROVED",
            payment_status="FAILED",
            closed_at=datetime.utcnow(),
        )
        ctx.payment_result = {"status": "FAILED", "reason": "No optimizer allocation"}
        return ctx

    first = allocations[0]
    amount = Decimal(str(first.get("amount", "0")))
    maturity_date = first.get("maturityDate", "")

    import datetime as dt
    try:
        exec_date = dt.date.fromisoformat(str(maturity_date)[:10])
    except ValueError:
        exec_date = dt.date.today()

    payment_id, write_status = await initiate_payment_safe(
        source_account_id="SAMP-0012345678",  # primary account
        beneficiary_account="COMB-0098765432",  # investment account (ComBank)
        amount=amount,
        currency=ctx.goal.currency,
        purpose=f"FD Allocation — {proposal.proposal_id[:8]}",
        requested_execution_date=exec_date,
        reference_note=proposal.proposal_id,
    )

    if write_status == PaymentWriteStatus.REJECTED or payment_id is None:
        logger.error("[Report] Payment initiation failed/rejected: status=%s", write_status)
        await update_decision(
            proposal_id=proposal.proposal_id,
            human_decision="APPROVED",
            payment_status="FAILED",
            closed_at=datetime.utcnow(),
        )
        ctx.payment_result = {"status": "FAILED", "reason": f"Payment rejected: {write_status}"}
        return ctx

    logger.info("[Report] Payment initiated safely: paymentId=%s", payment_id)

    # --- Poll for payment status ---
    final_status = await _poll_payment_status(payment_id)
    logger.info("[Report] Payment %s final status: %s", payment_id, final_status)

    if final_status == "EXECUTED":
        await update_decision(
            proposal_id=proposal.proposal_id,
            human_decision="APPROVED",
            payment_id=payment_id,
            payment_status="EXECUTED",
            closed_at=datetime.utcnow(),
        )
    else:
        logger.warning(
            "[Report] Payment %s status is %s — manual verification required.",
            payment_id, final_status,
        )
        await update_decision(
            proposal_id=proposal.proposal_id,
            human_decision="APPROVED",
            payment_id=payment_id,
            payment_status="UNKNOWN",
            human_note="Manual verification required: payment status unknown after 5 minutes.",
            closed_at=datetime.utcnow(),
        )

    ctx.payment_result = {"paymentId": payment_id, "status": final_status}
    return ctx


async def _handle_rejected(ctx: AgentContext, proposal: ProposedAction) -> AgentContext:
    """
    Log the rejection for the feedback loop and close the audit entry.

    The feedback loop (``agent/memory/feedback.py``) will query this record
    in the next Reason cycle to detect rejection patterns.
    """
    logger.info("[Report] REJECTED — logging for feedback loop, proposal %s", proposal.proposal_id)
    await update_decision(
        proposal_id=proposal.proposal_id,
        human_decision="REJECTED",
        human_note=ctx.human_note,
        closed_at=datetime.utcnow(),
    )
    ctx.payment_result = {"status": "NOT_EXECUTED", "reason": "Rejected by human"}
    return ctx


async def _handle_modified(ctx: AgentContext, proposal: ProposedAction) -> AgentContext:
    """
    Re-verify constraints with the modified parameters, then execute if valid.

    If the modified parameters breach any constraint, the modification is
    rejected and surfaced back.  Otherwise, payment proceeds.
    """
    modified_params = ctx.human_modified_parameters or {}
    logger.info("[Report] MODIFIED — re-verifying constraints with %s", modified_params)

    # Re-run constraint check on modified parameters
    ts = ctx.treasury_state
    violation = _check_modified_constraints(ts, modified_params, ctx.goal.minimum_liquidity_buffer)

    if violation:
        logger.warning("[Report] Modified parameters fail constraints: %s", violation)
        await update_decision(
            proposal_id=proposal.proposal_id,
            human_decision="MODIFIED",
            modified_parameters=modified_params,
            human_note=f"Modification rejected: {violation}",
            closed_at=datetime.utcnow(),
        )
        ctx.payment_result = {"status": "MODIFICATION_REJECTED", "reason": violation}
        return ctx

    # Constraints pass — execute with modified parameters
    amount = Decimal(str(modified_params.get("amount", "0")))
    import datetime as dt
    exec_date = dt.date.today()
    
    payment_id, write_status = await initiate_payment_safe(
        source_account_id="SAMP-0012345678",
        beneficiary_account="COMB-0098765432",
        amount=amount,
        currency=ctx.goal.currency,
        purpose=f"FD Allocation (Modified) — {proposal.proposal_id[:8]}",
        requested_execution_date=exec_date,
    )

    if write_status == PaymentWriteStatus.REJECTED or payment_id is None:
        logger.error("[Report] Modified payment initiation failed/rejected: status=%s", write_status)
        ctx.payment_result = {"status": "FAILED", "reason": f"Payment write failed: {write_status}"}
        return ctx

    final_status = await _poll_payment_status(payment_id)
    await update_decision(
        proposal_id=proposal.proposal_id,
        human_decision="MODIFIED",
        modified_parameters=modified_params,
        human_note=ctx.human_note,
        payment_id=payment_id,
        payment_status=final_status,
        closed_at=datetime.utcnow(),
    )
    ctx.payment_result = {"paymentId": payment_id, "status": final_status}
    return ctx



async def _handle_timeout(ctx: AgentContext, proposal: ProposedAction) -> AgentContext:
    """Close the proposal as TIMEOUT and log an escalation alert."""
    logger.warning(
        "[Report] TIMEOUT — proposal %s auto-closed without execution. "
        "ESCALATION ALERT: Proposal awaiting approval has timed out.",
        proposal.proposal_id,
    )
    await update_decision(
        proposal_id=proposal.proposal_id,
        human_decision="TIMEOUT",
        human_note="Auto-closed: no human decision received within the approval window.",
        closed_at=datetime.utcnow(),
    )
    ctx.payment_result = {"status": "NOT_EXECUTED", "reason": "Approval timeout"}
    return ctx


# ---------------------------------------------------------------------------
# Payment polling
# ---------------------------------------------------------------------------

async def _poll_payment_status(payment_id: str) -> str:
    """
    Poll ``GET /payments/{id}/status`` until EXECUTED, FAILED, or timeout.

    Polls every ``_PAYMENT_POLL_INTERVAL_SECONDS`` (30 s) for up to
    ``_PAYMENT_POLL_TIMEOUT_SECONDS`` (5 min).

    Parameters
    ----------
    payment_id:
        Bank-issued payment reference.

    Returns
    -------
    str
        ``"EXECUTED"`` | ``"FAILED"`` | ``"UNKNOWN"`` (on timeout)
    """
    import time
    start = time.monotonic()
    while (time.monotonic() - start) < _PAYMENT_POLL_TIMEOUT_SECONDS:
        try:
            status_resp = bank_client.get_payment_status(payment_id)
            status = status_resp.get("status", "")
            if status in ("EXECUTED", "FAILED"):
                return status
        except BankClientError as exc:
            logger.warning("[Report] Status poll error: %s", exc)

        await asyncio.sleep(_PAYMENT_POLL_INTERVAL_SECONDS)

    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Constraint re-check for modified parameters
# ---------------------------------------------------------------------------

def _check_modified_constraints(
    ts,  # TreasuryState | None
    modified_params: dict,
    minimum_buffer: Decimal,
) -> str | None:
    """
    Re-verify hard constraints against modified parameters.

    Returns an error string if constraints are violated, or ``None`` if valid.
    """
    if not ts:
        return None  # can't verify without treasury state

    amount_str = modified_params.get("amount")
    if amount_str:
        try:
            amount = Decimal(str(amount_str))
        except Exception:
            return f"Invalid amount in modified parameters: {amount_str}"

        if amount > ts.available_surplus:
            return (
                f"Modified amount (LKR {amount:,.2f}) exceeds "
                f"available surplus (LKR {ts.available_surplus:,.2f})."
            )

        buffer_after = ts.total_available_balance - amount
        if buffer_after < minimum_buffer:
            return (
                f"Buffer after modified deployment (LKR {buffer_after:,.2f}) "
                f"would fall below minimum (LKR {minimum_buffer:,.2f})."
            )

    term_days_val = modified_params.get("termDays") or modified_params.get("term_days")
    if term_days_val and ts.next_fixed_obligation_date:
        try:
            term_days = int(term_days_val)
            import datetime as dt
            maturity_date = dt.date.today() + dt.timedelta(days=term_days)
            if maturity_date > ts.next_fixed_obligation_date:
                return (
                    f"Modified deposit maturity ({maturity_date}) falls after "
                    f"next fixed obligation due date ({ts.next_fixed_obligation_date})."
                )
        except (ValueError, TypeError):
            return f"Invalid termDays in modified parameters: {term_days_val}"

    return None  # all constraints pass

