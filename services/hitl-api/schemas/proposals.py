"""
services/hitl-api/schemas/proposals.py
=======================================

Pydantic models for the HITL Approval API.

These models mirror the ``ProposedAction`` / ``AgentContext`` shapes defined in
``agent/state.py`` and the ``decision_log`` table schema defined in
``agent/db/audit_log.py``.  They are intentionally kept as a separate schema
layer so the HITL service can evolve independently of the agent core.

Models
------
RejectedAlternativeOut
    Read-only view of a rejected alternative for API responses.
ProposalRecord
    Full serialised view of a ``decision_log`` row, returned by GET /proposals
    and GET /audit-log.
DecisionRequest
    Body for ``POST /proposals/{id}/decision``.
VerificationResult
    Nested object in DecisionResponse confirming constraint satisfaction.
DecisionResponse
    Success body returned after recording a human decision.
ConstraintViolationDetail
    Body for 400 responses when a MODIFIED decision violates parameter bounds.
Last30DayStats
    Approval / rejection / modification counts over a rolling 30-day window.
RejectionPattern
    A detected pattern in human rejections and the agent's adaptation.
FeedbackInsightsResponse
    Full response body for GET /feedback/insights.
ChaosRequest
    Body for POST /chaos — proxies to mock-service chaos endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------


class RejectedAlternativeOut(BaseModel):
    """
    Read-only view of an alternative option that the agent evaluated and
    rejected.

    Attributes
    ----------
    option:
        Human-readable description, e.g. ``"Sampath 90-day FD at 14.5%"``.
    reason_rejected:
        Short explanation for the rejection.
    expected_yield:
        Annualised yield in LKR (string to preserve Decimal precision), or
        ``None`` if the option was rejected before yield calculation.
    """

    option: str
    reason_rejected: str
    expected_yield: str | None = None


# ---------------------------------------------------------------------------
# Proposal read models
# ---------------------------------------------------------------------------


class ProposalRecord(BaseModel):
    """
    Full serialised view of a ``decision_log`` row.

    Returned by ``GET /proposals`` and ``GET /audit-log``.  All timestamps
    are ISO 8601 strings.

    Attributes
    ----------
    proposal_id:
        UUID from ``decision_log.proposal_id``.
    cycle_id:
        UUID of the agent reasoning cycle that generated this proposal.
    company_code:
        SAP company code, e.g. ``"1000"``.
    status:
        Current state: ``PENDING`` | ``APPROVED`` | ``REJECTED`` | ``MODIFIED``
        | ``TIMEOUT``.  Alias for ``human_decision``; ``PENDING`` when no
        decision has been recorded yet.
    action_type:
        e.g. ``"SURPLUS_ALLOCATION"``, ``"PAYMENT_DEFERRAL"``, ``"NO_ACTION"``.
    description:
        Plain-language action description.
    rationale:
        LLM or template-generated rationale text (2–4 sentences).
    alternatives_rejected:
        List of options considered and rejected by the agent (may be empty).
    confidence_score:
        Scalar 0–1 from the Confidence Check node.
    flagged_ambiguities:
        Active conflict flags, e.g. ``["STALE_DATA_PRESENT"]``.
    parameter_bounds:
        Safe ranges for human-modifiable parameters, e.g.
        ``{"termDays": {"min": 1, "max": 14}}``.  Empty dict when no
        modifiable parameters exist.
    proposed_at:
        ISO 8601 timestamp when the agent produced this proposal.
    decided_at:
        ISO 8601 timestamp of the human decision, or ``None`` if still pending.
    human_decision:
        ``"APPROVED"`` | ``"REJECTED"`` | ``"MODIFIED"`` | ``"TIMEOUT"`` |
        ``None`` (pending).
    modified_parameters:
        Parameters the human submitted on a ``MODIFIED`` decision.
    human_note:
        Free-text note from the reviewer.
    payment_status:
        ``"EXECUTED"`` | ``"FAILED"`` | ``"UNKNOWN"`` | ``None``.
    """

    proposal_id: str
    cycle_id: str
    company_code: str
    status: str
    action_type: str
    description: str
    rationale: str
    alternatives_rejected: list[RejectedAlternativeOut] = Field(default_factory=list)
    confidence_score: float
    flagged_ambiguities: list[str] = Field(default_factory=list)
    parameter_bounds: dict[str, Any] = Field(default_factory=dict)
    proposed_at: str
    decided_at: str | None = None
    human_decision: str | None = None
    modified_parameters: dict[str, Any] | None = None
    human_note: str | None = None
    payment_status: str | None = None
    approved_by: str | None = None
    approver_role: str | None = None
    previous_hash: str | None = None



# ---------------------------------------------------------------------------
# Decision request / response models
# ---------------------------------------------------------------------------


class DecisionRequest(BaseModel):
    """
    Request body for ``POST /proposals/{id}/decision``.

    Accepts both camelCase (from the browser/tests) and snake_case field names
    via Pydantic v2 alias generation with ``populate_by_name=True``.

    Attributes
    ----------
    decision:
        One of ``"APPROVED"``, ``"REJECTED"``, ``"MODIFIED"``.
    modified_parameters:
        Required when ``decision == "MODIFIED"``.  Must fall within the
        ``parameterBounds`` stored on the proposal, otherwise the API returns
        a ``400 CONSTRAINT_VIOLATION``.  Accepted as ``modifiedParameters``
        (camelCase) or ``modified_parameters`` (snake_case).
    human_note:
        Optional free-text note from the reviewer (shown in audit log).
        Accepted as ``humanNote`` (camelCase) or ``human_note`` (snake_case).
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    decision: str = Field(..., pattern="^(APPROVED|REJECTED|MODIFIED)$")
    modified_parameters: dict[str, Any] | None = None
    human_note: str | None = None


class VerificationResult(BaseModel):
    """
    Nested object confirming constraint re-verification on MODIFIED decisions.

    Attributes
    ----------
    constraints_satisfied:
        ``True`` if the modified parameters are within ``parameterBounds``.
    buffer_after_modification:
        Formatted LKR string showing the projected liquidity buffer after the
        modification.  Informational only — not recomputed from scratch; uses
        the stored ``decision_log`` surplus figure as a proxy.
    """

    constraints_satisfied: bool
    buffer_after_modification: str | None = None


class DecisionResponse(BaseModel):
    """
    Success body returned by ``POST /proposals/{id}/decision``.

    Attributes
    ----------
    proposal_id:
        UUID of the proposal that was decided.
    decision:
        The decision that was recorded.
    verification_result:
        Only present when ``decision == "MODIFIED"``.
    recorded_at:
        ISO 8601 timestamp when the decision was written to the DB.
    """

    proposal_id: str
    decision: str
    verification_result: VerificationResult | None = None
    recorded_at: str


class ConstraintViolationDetail(BaseModel):
    """
    Error body for 400 responses when a MODIFIED decision violates bounds.

    Returned when ``modifiedParameters`` fall outside the proposal's
    ``parameterBounds``.

    Attributes
    ----------
    error:
        Always ``"CONSTRAINT_VIOLATION"``.
    message:
        Human-readable explanation, e.g. ``"Modified term of 60 days extends
        past next payroll date..."``.
    parameter_bounds:
        The safe bounds the human should stay within.
    """

    error: str = "CONSTRAINT_VIOLATION"
    message: str
    parameter_bounds: dict[str, Any]


# ---------------------------------------------------------------------------
# Feedback insights models
# ---------------------------------------------------------------------------


class Last30DayStats(BaseModel):
    """
    Approval / rejection / modification counts over a rolling 30-day window.

    Attributes
    ----------
    total_proposals:
        Total proposals produced in the last 30 days.
    approved:
        Number approved by the human.
    rejected:
        Number rejected.
    modified:
        Number accepted with modifications.
    approval_rate:
        ``approved / total_proposals``, or ``0.0`` if no proposals.
    """

    total_proposals: int
    approved: int
    rejected: int
    modified: int
    approval_rate: float


class RejectionPattern(BaseModel):
    """
    A detected pattern in human rejections and the agent's adaptation.

    Attributes
    ----------
    pattern:
        Plain-language description, e.g. ``"Long-term deposits (>30 days)
        rejected 3 times"``.
    agent_adaptation:
        What the agent has (or will) do differently, e.g. ``"Agent has capped
        default term at 30 days"``.
    """

    pattern: str
    agent_adaptation: str


class FeedbackInsightsResponse(BaseModel):
    """
    Full response body for ``GET /feedback/insights``.

    Attributes
    ----------
    last_30_days:
        Rolling 30-day summary statistics.
    rejection_patterns:
        Detected patterns in recent rejections (may be empty if no patterns
        detected yet).
    """

    last_30_days: Last30DayStats
    rejection_patterns: list[RejectionPattern] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Chaos panel model
# ---------------------------------------------------------------------------


class ChaosRequest(BaseModel):
    """
    Request body for ``POST /chaos``.

    Proxies a chaos mode toggle to the target mock service.

    Attributes
    ----------
    service:
        Target service: ``"bank-mock"`` or ``"erp-mock"``.
    mode:
        Failure mode to activate: ``"timeout"``, ``"auth_failure"``,
        ``"write_failure"``, or ``"none"`` to clear any active mode.
    """

    service: str = Field(..., pattern="^(bank-mock|erp-mock)$")
    mode: str = Field(..., pattern="^(timeout|auth_failure|write_failure|none)$")


# ---------------------------------------------------------------------------
# Auth & Governance Models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str
    display_name: str
    expires_in: int


class UserOut(BaseModel):
    username: str
    role: str
    display_name: str
    company_code: str


class GoalParametersUpdate(BaseModel):
    minimum_liquidity_buffer: str | float | int | None = None
    target_yield_minimum: float | None = None
    max_payment_risk_days: int | None = None
    goal_profile: str | None = None


class GoalParametersResponse(BaseModel):
    company_code: str
    minimum_liquidity_buffer: str
    target_yield_minimum: float
    max_payment_risk_days: int
    goal_profile: str
    updated_by: str | None = None
    updated_at: str | None = None

