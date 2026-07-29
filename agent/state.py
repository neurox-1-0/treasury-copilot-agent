"""
agent/state.py
==============

Shared Pydantic state models for the Treasury Copilot Agent reasoning loop.

These models define the *data contracts* between every LangGraph node.
**Define all models here before building any node** — every node depends on these
contracts to be independently testable, replayable, and LLM-hallucination-resistant.

Hierarchy
---------
TreasuryGoal          — static goal config (company, currency, buffer, yield target)
DataFreshness         — enum: FRESH | STALE | MISSING
DataSourceStatus      — per-source freshness tracking (ERP, bank)
Obligation            — a single upcoming payment obligation
TreasuryState         — output of the Perceive node; input to all downstream nodes
RejectedAlternative   — an allocation option that was evaluated but rejected
ProposedAction        — output of the Decide node; sent to HITL and Report node
AgentContext          — the full LangGraph graph state; passed between all nodes

Design principles
-----------------
- All monetary fields use ``Decimal`` (not float) to avoid floating-point drift
  in financial calculations.
- ``TreasuryState.has_stale_data`` is a computed convenience flag; the detailed
  breakdown is in ``data_source_statuses``.
- ``ProposedAction.content_hash`` is a SHA-256 of (action_type + key parameters)
  used for idempotency — prevents duplicate pending proposals.
- ``AgentContext.cycle_id`` is a UUID generated once per full reasoning loop run
  and threaded through every audit-log entry.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Goal Configuration
# ---------------------------------------------------------------------------


class TreasuryGoal(BaseModel):
    """
    Static treasury configuration.

    Loaded once at agent startup (from env / config file) and injected into
    the first ``AgentContext`` before the graph runs.  All nodes read from
    this; none write back to it.

    Attributes
    ----------
    company_code:
        SAP company code — matches the ERP mock seed (default ``"1000"``).
    currency:
        ISO 4217 currency code for all monetary amounts (``"LKR"``).
    minimum_liquidity_buffer:
        Hard lower bound on the cash position.  The Decide node will not
        recommend any allocation that would drop the balance below this.
    target_yield_minimum:
        Annualised yield threshold (fractional).  Proposals below this are
        flagged but not blocked.
    max_payment_risk_days:
        Days overdue before a ``FLEXIBLE`` payment is escalated.
    goal_profile:
        ``"CONSERVATIVE"`` | ``"BALANCED"`` | ``"YIELD_MAXIMISING"``.
        Affects the disambiguation stakes threshold (future: pluggable).
    """

    company_code: str = "1000"
    currency: str = "LKR"
    minimum_liquidity_buffer: Decimal = Decimal("20000000.00")
    target_yield_minimum: float = 0.10
    max_payment_risk_days: int = 10
    goal_profile: str = "BALANCED"


# ---------------------------------------------------------------------------
# Data Freshness
# ---------------------------------------------------------------------------


class DataFreshness(str, Enum):
    """
    Freshness state for a data source.

    FRESH   — data was fetched live during this cycle.
    STALE   — live fetch failed; data loaded from cache.
    MISSING — live fetch failed AND no cache entry exists.
    """

    FRESH = "FRESH"
    STALE = "STALE"
    MISSING = "MISSING"


class PaymentWriteStatus(str, Enum):
    """
    Result of a bank payment initiation call.

    **This enum drives the most critical safety policy in the system.**
    The Report node maps each status to a different audit-log outcome and
    never retries a payment — guessing wrong about whether real money moved
    is categorically worse than pausing for manual verification.

    SUBMITTED
        Bank accepted the instruction.  Payment is in the bank's processing
        queue.  The Report node polls for EXECUTED confirmation.
    REJECTED
        Bank definitively rejected the instruction (HTTP 400 / 422).
        Reason codes include INSUFFICIENT_FUNDS, INVALID_ACCOUNT, etc.
        A definitive rejection means money did **not** move.
    UNKNOWN
        The request timed out or returned a 5xx error.  The payment may or
        may not have been processed by the bank.
        → Audit log: ``payment_status = "UNKNOWN"``.
        → Dashboard: "Manual bank verification required".
        → Report node: does NOT retry, does NOT close the cycle.
    """

    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"



class DataSourceStatus(BaseModel):
    """
    Freshness record for a single data source (e.g. ERP cash position, bank balance).

    Attributes
    ----------
    source:
        Human-readable source identifier, e.g. ``"ERP_CASH_POSITION"``,
        ``"BANK_BALANCE"``, ``"ERP_OPEN_PAYABLES"``.
    freshness:
        ``DataFreshness`` enum value.
    last_fresh_at:
        UTC timestamp of the last successful live fetch, or ``None`` if this
        source has never been fetched successfully.
    stale_reason:
        The exception message or short description of why the live fetch
        failed, for inclusion in audit logs.
    """

    source: str
    freshness: DataFreshness
    last_fresh_at: datetime | None = None
    stale_reason: str | None = None


# ---------------------------------------------------------------------------
# Obligations
# ---------------------------------------------------------------------------


class Obligation(BaseModel):
    """
    A single upcoming payment obligation.

    Both ``FIXED`` and ``FLEXIBLE`` obligations flow through the system, but
    only ``FLEXIBLE`` obligations can be deferred by the agent.  ``FIXED``
    obligations (payroll, statutory taxes, loan covenants) are treated as
    hard constraints in the Decide node.

    Attributes
    ----------
    obligation_id:
        Stable identifier (SAP accounting document number or generated UUID).
    obligation_type:
        One of ``"VENDOR_AP"`` | ``"PAYROLL"`` | ``"TAX_VAT"`` |
        ``"TAX_WHT"`` | ``"TAX_EPF"`` | ``"LOAN"``.
    amount:
        Obligation amount in ``TreasuryGoal.currency`` (LKR).
    due_date:
        The date payment is due.
    payment_priority:
        ``"FIXED"`` — cannot be deferred.
        ``"FLEXIBLE"`` — can be deferred by the agent within limits.
    vendor_id:
        SAP business partner number (only for ``VENDOR_AP`` obligations).
    description:
        Optional human-readable label for the obligation.
    is_overdue:
        ``True`` if ``due_date < today`` and payment has not been confirmed.
    """

    obligation_id: str
    obligation_type: str
    amount: Decimal
    due_date: date
    payment_priority: str
    vendor_id: str | None = None
    description: str | None = None
    is_overdue: bool = False


class MarketRates(BaseModel):
    """
    Assembled market rate snapshot from Market Data Tool (Component 9).
    """

    best_fd_rates: dict[str, dict] = Field(default_factory=dict)
    call_deposit_best: dict = Field(default_factory=dict)
    overnight_policy_rate: float | None = None
    inflation_ccpi: float | None = None
    usd_lkr_buy: float | None = None
    usd_lkr_sell: float | None = None
    as_of: datetime
    cbsl_stale: bool = False


# ---------------------------------------------------------------------------
# Treasury State (Perceive output)
# ---------------------------------------------------------------------------


class TreasuryState(BaseModel):

    """
    Output of the Perceive node — the complete financial snapshot.

    Populated by ``agent/nodes/perceive.py`` and passed read-only to all
    downstream nodes (Reason, Confidence Check, Disambiguate, Decide, Report).

    Attributes
    ----------
    company_code:
        SAP company code (copied from ``TreasuryGoal``).
    as_of:
        UTC timestamp at which this snapshot was assembled.
    total_available_balance:
        Sum of ``AvailableBalance`` across all bank accounts.
    accounts:
        Raw account balance records (list of dicts from bank_client).
    obligations:
        All upcoming obligations sorted by ``due_date`` ascending.
    fixed_obligations:
        Filtered subset where ``payment_priority == "FIXED"``.
    flexible_obligations:
        Filtered subset where ``payment_priority == "FLEXIBLE"``.
    next_fixed_obligation_date:
        ``due_date`` of the soonest ``FIXED`` obligation, or ``None``.
    next_fixed_obligation_amount:
        Amount of that obligation.
    available_surplus:
        ``total_available_balance - minimum_liquidity_buffer`` — the amount
        the optimizer is allowed to invest.  Can be negative (liquidity
        shortfall).
    data_source_statuses:
        Freshness record for each data source queried by Perceive.
    has_stale_data:
        ``True`` if any source has ``freshness != FRESH``.
    unreconciled_large_credits:
        Bank credits > LKR 100K with no matching ERP AR document (±5%
        amount, same day).  Surfaced to the confidence check node.
    execution_blocked:
        ``True`` if cash position data is ``MISSING`` (no cache).  When
        ``True`` the Decide node must produce ``action_type = "NO_ACTION"``.
    block_reason:
        Plain-language explanation of why execution is blocked.
    market_rates:
        Market rate snapshot.
    cbsl_rates_stale:
        True if the CBSL data source is stale or missing.
    """

    company_code: str
    as_of: datetime
    total_available_balance: Decimal
    accounts: list[dict]
    obligations: list[Obligation]
    fixed_obligations: list[Obligation]
    flexible_obligations: list[Obligation]
    next_fixed_obligation_date: date | None = None
    next_fixed_obligation_amount: Decimal | None = None
    available_surplus: Decimal
    data_source_statuses: list[DataSourceStatus]
    has_stale_data: bool
    unreconciled_large_credits: list[dict]
    execution_blocked: bool = False
    block_reason: str | None = None
    market_rates: MarketRates | None = None
    cbsl_rates_stale: bool = False


# ---------------------------------------------------------------------------
# Proposed Action (Decide output)
# ---------------------------------------------------------------------------


class RejectedAlternative(BaseModel):
    """
    An allocation option that was evaluated but rejected.

    Included in ``ProposedAction.alternatives_rejected`` so the CFO can see
    what else was considered and why it was ruled out.

    Attributes
    ----------
    option:
        Human-readable description, e.g. ``"Sampath 90-day FD at 14.5%"``.
    reason_rejected:
        Short explanation, e.g. ``"Maturity exceeds next fixed obligation date"``.
    expected_yield:
        Annual yield in LKR at the proposed allocation amount, or ``None``
        if it was rejected before yield calculation.
    """

    option: str
    reason_rejected: str
    expected_yield: Decimal | None = None


class ProposedAction(BaseModel):
    """
    Output of the Decide node — the concrete recommended action.

    Sent to the HITL gate, then to the Report node.  **Always**
    requires human approval (``requires_human_approval = True``).

    Attributes
    ----------
    proposal_id:
        UUID generated at decision time.  Stable across retries.
    action_type:
        ``"SURPLUS_ALLOCATION"`` | ``"PAYMENT_DEFERRAL"`` |
        ``"NO_ACTION"`` | ``"ESCALATE"`` | ``"CONSTRAINT_VIOLATION"``.
    description:
        Plain-language one-liner, e.g.
        ``"Allocate LKR 15M to Sampath 30-day FD at 12.5%"``.
    rationale:
        2–4 sentence LLM-generated (or template-generated) explanation
        connecting the numbers to the decision.
    alternatives_rejected:
        List of other options considered and why they were ruled out.
    overall_confidence_score:
        Forwarded from the Confidence Check node (0–1).
    flagged_ambiguities:
        Active conflict flags from the Confidence Check node (e.g.
        ``["STALE_DATA_PRESENT", "LOW_FORECAST_CONFIDENCE"]``).
    parameter_bounds:
        Safe range for human-modifiable parameters, e.g.
        ``{"termDays": {"min": 1, "max": 30}}``.
    requires_human_approval:
        Always ``True`` in this system.  Never set to ``False``.
    content_hash:
        SHA-256 of ``(action_type + sorted key parameters)``.  Used by
        the idempotency check to detect duplicate pending proposals.
    created_at:
        UTC timestamp when this proposal was assembled.
    """

    proposal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action_type: str
    description: str
    rationale: str
    alternatives_rejected: list[RejectedAlternative] = Field(default_factory=list)
    overall_confidence_score: float = 0.0
    flagged_ambiguities: list[str] = Field(default_factory=list)
    parameter_bounds: dict = Field(default_factory=dict)
    requires_human_approval: bool = True
    content_hash: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @classmethod
    def compute_hash(cls, action_type: str, parameters: dict) -> str:
        """
        Compute a stable SHA-256 fingerprint for idempotency checks.

        The hash is over ``(action_type + sorted key parameters)`` so that
        two proposals recommending the exact same action produce the same
        hash, regardless of timestamps or UUIDs.

        Parameters
        ----------
        action_type:
            e.g. ``"SURPLUS_ALLOCATION"``
        parameters:
            Key decision parameters, e.g. ``{"termDays": 30, "amount": "15000000"}``.

        Returns
        -------
        str
            64-character lowercase hex SHA-256 digest.
        """
        canonical = json.dumps({"action_type": action_type, **parameters}, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Full Graph State
# ---------------------------------------------------------------------------


class AgentContext(BaseModel):
    """
    Full LangGraph graph state — the single object passed between all nodes.

    Every node receives the full ``AgentContext`` and returns a (possibly
    modified) ``AgentContext``.  Fields are populated incrementally as the
    graph runs:

    - After Perceive:          ``treasury_state`` is set
    - After Reason:            ``forecast_result``, ``optimizer_result`` are set
    - After Confidence Check:  ``confidence_score``, ``conflict_flags``, ``route`` are set
    - After Disambiguate:      ``disambiguation_path``, ``disambiguation_rationale`` are set
    - After Decide:            ``proposed_action`` is set
    - After HITL:              ``human_decision``, ``human_modified_parameters``, ``human_note``
    - After Report:            ``payment_result`` is set

    Attributes
    ----------
    goal:
        Static treasury goal configuration.
    cycle_id:
        UUID generated once per full loop run.  Threads through all audit entries.
    treasury_state:
        Populated by Perceive.  ``None`` before Perceive runs.
    forecast_result:
        Raw dict from forecast_client (``ForecastResponse``-shaped).
    optimizer_result:
        Raw dict from optimizer_client (``OptimizationResult``-shaped).
    confidence_score:
        Scalar 0–1 from the Confidence Check node.
    conflict_flags:
        List of active flag strings (``"LOW_FORECAST_CONFIDENCE"``, etc.).
    route:
        Internal routing signal set by Confidence Check: ``"DECIDE"`` or
        ``"DISAMBIGUATE"``.  Not surfaced to the user.
    disambiguation_path:
        ``"PROCEED_FLAGGED"`` | ``"ESCALATE"`` — set by Disambiguate.
    disambiguation_rationale:
        Human-readable explanation for the disambiguation decision.
    proposed_action:
        Set by Decide.  ``None`` if skipped (duplicate pending).
    skip_reason:
        ``"DUPLICATE_PENDING"`` if idempotency check fired.
    human_decision:
        ``"APPROVED"`` | ``"REJECTED"`` | ``"MODIFIED"`` | ``"TIMEOUT"``.
        Populated externally by the HITL API.
    human_modified_parameters:
        Only present on ``"MODIFIED"`` decision.
    human_note:
        Free-text note from the human reviewer.
    payment_result:
        Bank payment status dict, populated by Report on APPROVED.
    """

    goal: TreasuryGoal
    cycle_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # Populated incrementally
    treasury_state: TreasuryState | None = None
    forecast_result: dict | None = None
    optimizer_result: dict | None = None

    # Confidence check outputs
    confidence_score: float | None = None
    conflict_flags: list[str] = Field(default_factory=list)
    route: str | None = None  # "DECIDE" | "DISAMBIGUATE"

    # Disambiguate outputs
    disambiguation_path: str | None = None  # "PROCEED_FLAGGED" | "ESCALATE"
    disambiguation_rationale: str | None = None

    # Decide outputs
    proposed_action: ProposedAction | None = None
    skip_reason: str | None = None  # "DUPLICATE_PENDING"

    # HITL inputs (populated externally)
    human_decision: str | None = None  # "APPROVED" | "REJECTED" | "MODIFIED" | "TIMEOUT"
    human_modified_parameters: dict | None = None
    human_note: str | None = None

    # Report output
    payment_result: dict | None = None

    class Config:
        # Allow Decimal and other non-JSON-native types
        arbitrary_types_allowed = True
