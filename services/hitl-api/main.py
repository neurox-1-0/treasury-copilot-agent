"""
services/hitl-api/main.py
==========================

FastAPI service for the Human-in-the-Loop Approval Dashboard (Component 6).

Purpose
-------
This service is the bridge between the agent's ``decision_log`` table and the
React dashboard.  It is a **separate process** from the agent so the dashboard
stays responsive even when the agent is restarted or slow.

Key design decisions
--------------------
- **Shared DB, separate process**: Both this service and ``agent/db/audit_log.py``
  read/write the same SQLite file (controlled by ``DATABASE_URL`` env var,
  default ``agent_audit.db`` in the working directory).  For production, point
  both to the same PostgreSQL instance.
- **SSE over WebSocket**: Proposals flow one-way (server → browser).  SSE is
  simpler to implement and debug; WebSocket is not needed.
- **Constraint re-verification on MODIFIED**: When the human submits modified
  parameters, the API validates them against the stored ``parameter_bounds``
  before writing to the DB.  This is intentionally lightweight — a full
  re-run of the optimizer is out of scope for the HITL service.
- **Chaos proxy**: ``POST /chaos`` proxies to the bank-mock / erp-mock chaos
  endpoint.  ``BANK_MOCK_URL`` and ``ERP_MOCK_URL`` are read from env vars.

Ports (default)
---------------
- HITL API:  http://localhost:8006
- ERP mock:  http://localhost:8001
- Bank mock: http://localhost:8002
- Dashboard: http://localhost:5173 (Vite dev server)

Environment variables
---------------------
DATABASE_URL           Async SQLAlchemy URL (default: sqlite+aiosqlite:///agent_audit.db)
BANK_MOCK_URL          Base URL of bank-mock service (default: http://localhost:8002)
ERP_MOCK_URL           Base URL of erp-mock service  (default: http://localhost:8001)
NOTIFICATION_WEBHOOK_URL  Optional webhook for new-proposal notifications
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from db.models import (
    get_admin_audit_log,
    get_audit_log,
    get_feedback_insights,
    get_goal_parameters,
    get_proposal_by_id,
    get_proposals,
    init_db,
    record_decision,
    update_goal_parameters,
)
from schemas.proposals import (
    ChaosRequest,
    ConstraintViolationDetail,
    DecisionRequest,
    DecisionResponse,
    FeedbackInsightsResponse,
    GoalParametersResponse,
    GoalParametersUpdate,
    Last30DayStats,
    LoginRequest,
    ProposalRecord,
    RejectedAlternativeOut,
    RejectionPattern,
    TokenResponse,
    UserOut,
    VerificationResult,
)
from auth.users import authenticate_user, User
from auth.tokens import create_access_token
from auth.dependencies import get_current_user, require_analyst, require_admin

try:
    from agent.db.audit_log import verify_audit_chain
except ImportError:
    verify_audit_chain = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------


try:
    from agent.timeout_checker import process_expired_approvals
except ImportError:
    process_expired_approvals = None


async def _background_timeout_checker_loop():
    """Periodically scan for expired PENDING proposals."""
    if process_expired_approvals is None:
        return
    logger.info("Background approval timeout scanner active.")
    while True:
        try:
            await process_expired_approvals(timeout_hours=24)
        except Exception as exc:
            logger.warning("Timeout scanner cycle failed: %s", exc)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise DB and start background tasks on startup."""
    await init_db()
    logger.info("HITL API started. DB: %s", os.getenv("DATABASE_URL", "agent_audit.db"))
    
    timeout_task = asyncio.create_task(_background_timeout_checker_loop())
    try:
        yield
    finally:
        timeout_task.cancel()
        try:
            await timeout_task
        except asyncio.CancelledError:
            pass



app = FastAPI(
    title="Treasury Copilot — HITL Approval API",
    description=(
        "Human-in-the-Loop approval gateway for the Treasury Copilot Agent. "
        "Reads proposals from the agent's decision_log table and records human "
        "decisions (approve / reject / modify) back before any money moves."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite dev server
        "http://localhost:4173",   # Vite preview
        "http://localhost:3000",   # Fallback CRA / other dev ports
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_BANK_MOCK_URL = os.getenv("BANK_MOCK_URL", "http://localhost:8002")
_ERP_MOCK_URL = os.getenv("ERP_MOCK_URL", "http://localhost:8001")
_NOTIFICATION_WEBHOOK_URL = os.getenv("NOTIFICATION_WEBHOOK_URL", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_proposal_record(row: dict) -> ProposalRecord:
    """
    Convert a raw ``decision_log`` row dict to a ``ProposalRecord``.

    The ``status`` field is derived from ``human_decision``: a ``None``
    ``human_decision`` maps to ``"PENDING"`` to match the filter semantics.
    """
    status = row.get("human_decision") or "PENDING"

    # alternatives_rejected is not stored in the current decision_log schema;
    # we return an empty list.  A future schema migration can add this column.
    alternatives: list[RejectedAlternativeOut] = []

    proposed_at = row.get("proposed_at") or ""
    if isinstance(proposed_at, datetime):
        proposed_at = proposed_at.isoformat()

    decided_at = row.get("decided_at")
    if isinstance(decided_at, datetime):
        decided_at = decided_at.isoformat()

    return ProposalRecord(
        proposal_id=row["proposal_id"],
        cycle_id=row.get("cycle_id", ""),
        company_code=row.get("company_code", "1000"),
        status=status,
        action_type=row.get("action_type", ""),
        description=row.get("description") or "",
        rationale=row.get("rationale") or "",
        alternatives_rejected=alternatives,
        confidence_score=float(row.get("confidence_score") or 0.0),
        flagged_ambiguities=row.get("flagged_ambiguities") or [],
        parameter_bounds=row.get("parameter_bounds") or {},
        proposed_at=proposed_at,
        decided_at=decided_at,
        human_decision=row.get("human_decision"),
        modified_parameters=row.get("modified_parameters"),
        human_note=row.get("human_note"),
        payment_status=row.get("payment_status"),
        approved_by=row.get("approved_by"),
        approver_role=row.get("approver_role"),
        previous_hash=row.get("previous_hash"),
    )



def _verify_modified_parameters(
    bounds: dict, modified_params: dict
) -> tuple[bool, str | None]:
    """
    Validate ``modified_params`` against ``bounds``.

    Returns
    -------
    (ok, error_message)
        ``ok=True`` if all provided params are within their bounds.
        ``error_message`` describes the first violation found.
    """
    for param, value in modified_params.items():
        if param not in bounds:
            continue  # Unknown params are accepted (not constrained)
        param_bound = bounds[param]
        lo = param_bound.get("min")
        hi = param_bound.get("max")
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        if lo is not None and num < float(lo):
            return False, (
                f"Modified {param} of {value} is below the minimum safe value of {lo}."
            )
        if hi is not None and num > float(hi):
            return False, (
                f"Modified {param} of {value} exceeds the maximum safe value of {hi}. "
                f"Setting this value would conflict with the next fixed obligation date."
            )
    return True, None


async def _notify_webhook(proposal_id: str, description: str) -> None:
    """POST a new-proposal notification to the optional webhook URL."""
    if not _NOTIFICATION_WEBHOOK_URL:
        return
    payload = {
        "event": "NEW_PROPOSAL",
        "proposalId": proposal_id,
        "description": description,
        "proposedAt": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(_NOTIFICATION_WEBHOOK_URL, json=payload)
    except Exception as exc:
        logger.warning("Webhook notification failed: %s", exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health", tags=["infra"])
async def health_check() -> dict:
    """
    Liveness probe.

    Returns ``{"status": "ok"}`` when the service is running.
    """
    return {"status": "ok"}


# ── Proposals ────────────────────────────────────────────────────────────────


@app.get(
    "/proposals",
    response_model=dict,
    tags=["proposals"],
    summary="List proposals filtered by status",
)
async def list_proposals(
    status: str | None = Query(
        default=None,
        description="Filter by status: PENDING | APPROVED | REJECTED | MODIFIED | TIMEOUT",
    ),
) -> dict:
    """
    Return all proposals matching the given ``status``.

    If ``status`` is omitted, all proposals are returned.

    Response shape
    --------------
    ``{"proposals": [ProposalRecord, ...]}``.
    """
    rows = await get_proposals(status)
    records = [_row_to_proposal_record(r) for r in rows]
    return {"proposals": [r.model_dump() for r in records]}


@app.get(
    "/proposals/stream",
    tags=["proposals"],
    summary="Server-Sent Events stream of pending proposals",
)
async def stream_proposals(request: Request) -> EventSourceResponse:
    """
    SSE endpoint that pushes new ``PENDING`` proposals to the dashboard.

    The dashboard opens this connection on load and keeps it alive.  Every
    5 seconds the server checks for pending proposals and emits a
    ``"proposal"`` event if any exist.

    Event format
    ------------
    ``event: proposal``
    ``data: <JSON array of ProposalRecord>``
    """

    async def generator() -> AsyncGenerator[dict, None]:
        last_ids: set[str] = set()
        while not await request.is_disconnected():
            rows = await get_proposals("PENDING")
            new_rows = [r for r in rows if r["proposal_id"] not in last_ids]
            if new_rows:
                records = [_row_to_proposal_record(r).model_dump() for r in new_rows]
                for r in new_rows:
                    last_ids.add(r["proposal_id"])
                yield {"event": "proposal", "data": json.dumps(records)}
            await asyncio.sleep(5)

    return EventSourceResponse(generator())


# ── Authentication ────────────────────────────────────────────────────────────


@app.post(
    "/auth/login",
    response_model=TokenResponse,
    tags=["auth"],
    summary="Authenticate and receive JWT access token",
)
async def login(body: LoginRequest) -> TokenResponse:
    """
    Authenticate a user with username and password.
    Returns a signed JWT access token containing the user's role.
    """
    user = authenticate_user(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_access_token({"sub": user.username, "role": user.role, "company_code": user.company_code})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        role=user.role,
        username=user.username,
        display_name=user.display_name,
        expires_in=8 * 3600,
    )


@app.get(
    "/auth/me",
    response_model=UserOut,
    tags=["auth"],
    summary="Get current authenticated user profile",
)
async def get_me(user: User = Depends(get_current_user)) -> UserOut:
    """
    Return the authenticated user profile derived from the Authorization header.
    """
    return UserOut(
        username=user.username,
        role=user.role,
        display_name=user.display_name,
        company_code=user.company_code,
    )


@app.post(
    "/proposals/{proposal_id}/decision",
    tags=["proposals"],
    summary="Submit a human decision (approve / reject / modify)",
)
async def submit_decision(
    proposal_id: str,
    body: DecisionRequest,
    user: User = Depends(require_analyst),
) -> Response:
    """
    Record a human decision for a proposal. Requires Treasury Analyst or Admin role.
    """
    row = await get_proposal_by_id(proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id!r} not found.")

    verification: VerificationResult | None = None

    if body.decision == "MODIFIED":
        if not body.modified_parameters:
            raise HTTPException(
                status_code=422,
                detail="modifiedParameters is required when decision is MODIFIED.",
            )
        bounds = row.get("parameter_bounds") or {}
        ok, err_msg = _verify_modified_parameters(bounds, body.modified_parameters)
        if not ok:
            violation = ConstraintViolationDetail(
                error="CONSTRAINT_VIOLATION",
                message=err_msg or "Modified parameters violate constraint bounds.",
                parameter_bounds=bounds,
            )
            return Response(
                content=violation.model_dump_json(),
                status_code=400,
                media_type="application/json",
            )
        verification = VerificationResult(constraints_satisfied=True)

    await record_decision(
        proposal_id=proposal_id,
        decision=body.decision,
        modified_parameters=body.modified_parameters,
        human_note=body.human_note,
        approved_by=user.username,
        approver_role=user.role,
    )


    response = DecisionResponse(
        proposal_id=proposal_id,
        decision=body.decision,
        verification_result=verification,
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )
    return Response(
        content=response.model_dump_json(),
        status_code=200,
        media_type="application/json",
    )


# ── Audit log ────────────────────────────────────────────────────────────────


@app.get(
    "/audit-log",
    response_model=dict,
    tags=["audit"],
    summary="Full decision history with filters",
)
async def get_audit_log_endpoint(
    from_date: str | None = Query(default=None, description="ISO date: YYYY-MM-DD"),
    to_date: str | None = Query(default=None, description="ISO date: YYYY-MM-DD"),
    action_type: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """
    Return a paginated, filtered slice of ``decision_log``.

    Supports filtering by date range, action type, and decision outcome.
    """
    rows = await get_audit_log(
        from_date=from_date,
        to_date=to_date,
        action_type=action_type,
        decision=decision,
        limit=limit,
        offset=offset,
    )
    records = [_row_to_proposal_record(r).model_dump() for r in rows]
    return {"records": records, "count": len(records), "offset": offset}


@app.get(
    "/audit-log/export",
    tags=["audit"],
    summary="Export full audit log as CSV",
)
async def export_audit_log() -> StreamingResponse:
    """
    Download the complete ``decision_log`` as a CSV file.

    Returns
    -------
    ``text/csv`` with ``Content-Disposition: attachment; filename=audit_log.csv``

    Columns
    -------
    proposal_id, cycle_id, company_code, action_type, description,
    confidence_score, human_decision, modified_parameters, human_note,
    proposed_at, decided_at, payment_status
    """
    rows = await get_audit_log(limit=200)

    output = io.StringIO()
    fieldnames = [
        "proposal_id",
        "cycle_id",
        "company_code",
        "action_type",
        "description",
        "confidence_score",
        "human_decision",
        "modified_parameters",
        "human_note",
        "proposed_at",
        "decided_at",
        "payment_status",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        # Flatten modified_parameters to a string for CSV
        row_copy = dict(r)
        if isinstance(row_copy.get("modified_parameters"), dict):
            row_copy["modified_parameters"] = json.dumps(row_copy["modified_parameters"])
        writer.writerow({k: row_copy.get(k, "") for k in fieldnames})

    csv_content = output.getvalue()
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )


# ── Feedback insights ─────────────────────────────────────────────────────────


@app.get(
    "/feedback/insights",
    response_model=FeedbackInsightsResponse,
    tags=["feedback"],
    summary="Aggregated decision patterns for the Feedback Insight Panel",
)
async def feedback_insights(
    company_code: str = Query(default="1000"),
    days: int = Query(default=30, ge=1, le=365),
) -> FeedbackInsightsResponse:
    """
    Return approval / rejection / modification counts and detected rejection
    patterns over the last ``days`` days.

    This endpoint powers the Feedback Insight Panel on the dashboard, making
    the agent's adaptive feedback loop visible to the human reviewer.
    """
    raw = await get_feedback_insights(company_code=company_code, days=days)
    ld = raw["last_30_days"]
    patterns = [
        RejectionPattern(pattern=p["pattern"], agent_adaptation=p["agent_adaptation"])
        for p in raw.get("rejection_patterns", [])
    ]
    return FeedbackInsightsResponse(
        last_30_days=Last30DayStats(**ld),
        rejection_patterns=patterns,
    )


# ── Chaos panel ───────────────────────────────────────────────────────────────


@app.post(
    "/chaos",
    tags=["developer"],
    summary="Toggle failure simulation mode on a mock service",
)
async def chaos_toggle(body: ChaosRequest) -> dict:
    """
    Proxy a chaos mode toggle to the target mock service.

    The chaos panel in the dashboard calls this endpoint so demo operators
    can trigger failure scenarios without editing config files.

    Supported services
    ------------------
    - ``bank-mock``: proxied to ``BANK_MOCK_URL/chaos``
    - ``erp-mock``: proxied to ``ERP_MOCK_URL/chaos``

    Supported modes
    ---------------
    ``timeout`` | ``auth_failure`` | ``write_failure`` | ``none``
    """
    if body.service == "bank-mock":
        target_url = f"{_BANK_MOCK_URL}/chaos"
    else:
        target_url = f"{_ERP_MOCK_URL}/chaos"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                target_url,
                json={"mode": body.mode},
            )
        return {"service": body.service, "mode": body.mode, "upstream_status": resp.status_code}
    except httpx.ConnectError:
        # Service not running — still return 200 so the UI panel works during
        # standalone dashboard development
        logger.warning("Chaos proxy: %s not reachable at %s", body.service, target_url)
        return {
            "service": body.service,
            "mode": body.mode,
            "upstream_status": 503,
            "warning": "Mock service not reachable — chaos mode noted locally only.",
        }


# ── Treasury Admin Governance ──────────────────────────────────────────────────


@app.get(
    "/admin/goal-parameters",
    response_model=GoalParametersResponse,
    tags=["admin"],
    summary="Get current standing treasury goal parameters (Admin only)",
)
async def get_goal_params(
    company_code: str = Query(default="1000"),
    user: User = Depends(require_admin),
) -> GoalParametersResponse:
    """
    Retrieve standing treasury goal parameters. Requires Treasury Admin role.
    """
    params = await get_goal_parameters(company_code)
    return GoalParametersResponse(**params)


@app.put(
    "/admin/goal-parameters",
    response_model=GoalParametersResponse,
    tags=["admin"],
    summary="Update standing treasury goal parameters (Admin only)",
)
async def update_goal_params(
    body: GoalParametersUpdate,
    company_code: str = Query(default="1000"),
    user: User = Depends(require_admin),
) -> GoalParametersResponse:
    """
    Update standing goal parameters (buffer, target yield, max risk days). Requires Treasury Admin role.
    """
    updates = body.model_dump(exclude_unset=True)
    updated = await update_goal_parameters(
        company_code=company_code,
        updates=updates,
        admin_username=user.username,
    )
    return GoalParametersResponse(**updated)


@app.get(
    "/admin/audit-log",
    tags=["admin"],
    summary="Get history of admin goal parameter modifications (Admin only)",
)
async def admin_audit_log_endpoint(
    company_code: str = Query(default="1000"),
    user: User = Depends(require_admin),
) -> dict:
    """
    Retrieve audit history of standing goal parameter changes made by Admins.
    """
    logs = await get_admin_audit_log(company_code)
    return {"admin_audit_logs": logs}


@app.get(
    "/admin/audit/verify-chain",
    tags=["admin"],
    summary="Verify cryptographic hash chain integrity of decision_log (Admin only)",
)
async def verify_chain_endpoint(user: User = Depends(require_admin)) -> dict:
    """
    Verify tamper-evident hash chain across all proposal records in decision_log.
    """
    if verify_audit_chain is None:
        raise HTTPException(status_code=500, detail="Audit chain verification function unavailable.")

    result = await verify_audit_chain()
    return result

