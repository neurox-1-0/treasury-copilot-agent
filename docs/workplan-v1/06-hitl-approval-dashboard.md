# Component 6: Human-in-the-Loop Approval Dashboard

**Status: To build. Frontend: React + Vite. Backend: FastAPI.**

---

## Purpose

The control point where a treasury analyst reviews, approves, rejects, or modifies
every proposed action before execution — and where their decisions are logged in a
form the agent can learn from.

**Design rationale** (surface this in the UI itself, not just here):
The approval gate sits *after* the agent reasons and *before* any money moves. It
does not gate every forecast (too much friction) and does not allow fully autonomous
execution (unacceptable risk for real capital). Every screen should reinforce that
this is "informed oversight by design" — not a rubber-stamp click.

---

## Tech Stack Decision

**Frontend**: React + Vite + TypeScript (not Streamlit).
Streamlit is acceptable for internal prototypes. For a B2B treasury product, the
UI is a product surface — it must look and function enterprise-grade.

**Backend**: FastAPI service (`services/hitl-api/`) — a separate service from the
agent, so the dashboard can run independently and the agent can be restarted
without losing pending proposals.

**Real-time**: Server-Sent Events (SSE) for pushing new proposals to the dashboard
without polling. WebSocket is overkill for this one-directional flow.

**Persistence**: Same `decision_log` SQLite/PostgreSQL table as Component 5 —
the HITL API reads and writes this table directly.

---

## Canonical Location

```
services/hitl-api/
├── main.py
├── schemas/
│   └── proposals.py       # Pydantic models mirroring Component 5's ProposedAction
├── db/
│   └── models.py          # SQLAlchemy ORM for decision_log table
├── tests/
│   └── test_hitl_api.py
└── requirements.txt

dashboard/
├── src/
│   ├── components/
│   │   ├── ProposalCard.tsx
│   │   ├── ModifyPanel.tsx
│   │   ├── DecisionLog.tsx
│   │   ├── FeedbackInsightPanel.tsx
│   │   └── ChaosPanel.tsx
│   ├── pages/
│   │   ├── Pending.tsx
│   │   └── AuditTrail.tsx
│   ├── hooks/
│   │   └── useProposals.ts    # SSE hook
│   ├── App.tsx
│   └── main.tsx
├── package.json
├── vite.config.ts
└── tsconfig.json
```

---

## HITL API Endpoints

### `GET /proposals?status=PENDING`

Returns all proposals awaiting human action. Supports filtering by `status`:
`PENDING | APPROVED | REJECTED | MODIFIED | TIMEOUT`.

Response:
```json
{
  "proposals": [
    {
      "proposalId": "...",
      "status": "PENDING",
      "actionType": "SURPLUS_ALLOCATION",
      "description": "Move LKR 8,000,000 into a 14-day fixed deposit at 10%",
      "rationale": "...",
      "alternativesRejected": [...],
      "confidenceScore": 0.87,
      "flaggedAmbiguities": [],
      "parameterBounds": { "termDays": { "min": 1, "max": 14 } },
      "proposedAt": "2026-07-13T09:31:00+05:30",
      "cycleId": "..."
    }
  ]
}
```

### `GET /proposals/stream` (SSE)

Server-Sent Events stream for real-time proposal delivery. The dashboard opens
this connection on load and keeps it alive.

```python
# FastAPI SSE endpoint
from sse_starlette.sse import EventSourceResponse

@router.get("/proposals/stream")
async def stream_proposals(request: Request):
    async def generator():
        while not await request.is_disconnected():
            pending = get_pending_proposals()
            if pending:
                yield {"event": "proposal", "data": json.dumps(pending)}
            await asyncio.sleep(5)  # check every 5 seconds
    return EventSourceResponse(generator())
```

### `POST /proposals/{id}/decision`

Submit a human decision. The agent's Report node polls for this (or is notified via
an internal event queue).

Request:
```json
{
  "decision": "APPROVED | REJECTED | MODIFIED",
  "modifiedParameters": { "termDays": 14 },
  "humanNote": "Board meeting Friday — prefer shorter lock-up"
}
```

Response:
```json
{
  "proposalId": "...",
  "decision": "MODIFIED",
  "verificationResult": {
    "constraintsSatisfied": true,
    "bufferAfterModification": "20000000.00"
  },
  "recordedAt": "2026-07-13T10:15:00+05:30"
}
```

**Modification constraint re-verification**: When decision is `MODIFIED`, the
HITL API re-runs the optimizer's constraint check on the modified parameters
before accepting. If the modification violates a hard constraint, return:
```json
{
  "error": "CONSTRAINT_VIOLATION",
  "message": "Modified term of 60 days extends past next payroll date (2026-07-28). Maximum safe term is 14 days.",
  "parameterBounds": { "termDays": { "min": 1, "max": 14 } }
}
```
The frontend displays this error inline — the human must correct their modification
or reject instead.

### `GET /audit-log`

Full decision history. Query params: `from_date`, `to_date`, `action_type`,
`decision`, `limit` (default 50), `offset`.

Response includes all `decision_log` fields plus a computed `outcomeStatus`
field for display.

### `GET /audit-log/export`

Export audit log as CSV (for compliance/board reporting). Returns `text/csv`
with Content-Disposition header.

### `GET /feedback/insights`

Aggregated data for the Feedback Insight Panel:
```json
{
  "last30Days": {
    "totalProposals": 18,
    "approved": 12,
    "rejected": 4,
    "modified": 2,
    "approvalRate": 0.67
  },
  "rejectionPatterns": [
    {
      "pattern": "Long-term deposits (>30 days) rejected 3 times",
      "agentAdaptation": "Agent has capped default term at 30 days"
    }
  ]
}
```

### `POST /chaos`

Toggle failure simulation modes in the mock services (Chaos Panel). Proxies to
the relevant service's chaos endpoint.

```json
{
  "service": "bank-mock | erp-mock",
  "mode": "timeout | auth_failure | write_failure | none"
}
```

---

## Frontend Screens

### Screen 1: Pending Approvals (`/`)

The main screen. Renders a `ProposalCard` for each `PENDING` proposal.

**`ProposalCard` must display:**
- Action description (plain language, large typography)
- Confidence score (visual indicator: green ≥ 0.8, amber 0.6–0.79, red < 0.6)
- Flagged ambiguities (if any) — displayed prominently as warnings
- Rationale (full text, readable)
- Alternatives Rejected (collapsible list — each alternative with `rejectedReason`)
- Parameter bounds (if `action_type == "SURPLUS_ALLOCATION"`: show term slider with
  `min`/`max` from `parameterBounds`)
- Three action buttons: **Approve**, **Reject**, **Modify**
- Timestamp proposed

**Modify flow:**
1. User clicks Modify.
2. `ModifyPanel` slides in showing an adjustable slider for `termDays` (bounded
   by `parameterBounds`) and a free-text note field.
3. On submit: POST `/proposals/{id}/decision` with `decision: "MODIFIED"`.
4. If `CONSTRAINT_VIOLATION` returned: display error inline with the safe bounds.
5. If success: card moves to completed state.

### Screen 2: Audit Trail (`/audit`)

Table view of all past decisions. Columns: Date, Description, Decision,
Modified Parameters, Human Note, Payment Status.

**Search and filter**: by date range, action type, and decision (Approved /
Rejected / Modified / Timeout).

**Export button**: triggers `GET /audit-log/export` — downloads CSV.

### Screen 3: Feedback Insight Panel (sidebar or drawer)

Shows the aggregated `FeedbackInsights` response:
- A bar chart: approval / rejection / modification counts over last 30 days.
- Detected rejection patterns as plain-language chips: *"You've rejected 3 of the
  last 4 proposals over 30 days — the agent has capped default term at 30 days."*

This makes the adaptive feedback loop **visible** rather than silently happening in
the backend. For a B2B demo, this is the most compelling UI element — it shows
genuine learning.

### Screen 4: Chaos Panel (developer/demo tool, accessible via `/chaos`)

Simple toggles for each mock service's failure modes. Useful for running the
scripted failure scenarios during a demo without touching config files.

| Toggle | Effect |
|---|---|
| ERP Timeout | Sets `simulate=timeout` on erp-mock |
| Bank Write Failure | Sets `simulate=write_failure` on bank-mock |
| Low Forecast Confidence | Overrides forecaster to return score=0.3 |
| Infeasible Optimiser | Overrides optimizer to return constraintsSatisfied=false |

---

## Notification on New Proposal

When a new `PENDING` proposal is inserted into the database, the HITL API:
1. Pushes an SSE event to all connected dashboard clients.
2. POSTs a JSON payload to `NOTIFICATION_WEBHOOK_URL` (env var, optional):
   ```json
   { "event": "NEW_PROPOSAL", "proposalId": "...", "description": "...", "proposedAt": "..." }
   ```
   This can connect to Slack, email, or any webhook receiver in a real deployment.

---

## Data Contract (Read / Write)

**HITL API reads** from `decision_log` table (Component 5's audit log).

**HITL API writes** human decisions back to `decision_log`:
```python
UPDATE decision_log
SET human_decision = ?,
    modified_parameters = ?,
    human_note = ?,
    decided_at = ?
WHERE proposal_id = ?
```

The agent's Report node monitors this table for its own proposals' `human_decision`
field changing from `PENDING`.

---

## Testing Requirements

### Test file: `services/hitl-api/tests/test_hitl_api.py`

#### 1. Proposals list
```python
async def test_get_pending_proposals_returns_correct_shape():
    # Seed one PENDING proposal in decision_log
    # GET /proposals?status=PENDING
    # Assert response contains proposal with required fields

async def test_get_proposals_filtered_by_status():
    # Seed one PENDING and one APPROVED proposal
    # GET /proposals?status=APPROVED
    # Assert only APPROVED proposal returned

async def test_get_proposals_empty_when_no_pending():
    # GET /proposals?status=PENDING with empty DB
    # Assert {"proposals": []}
```

#### 2. Decision submission
```python
async def test_approve_decision_updates_status():
    # Seed PENDING proposal
    # POST /proposals/{id}/decision {"decision": "APPROVED"}
    # GET /proposals?status=APPROVED
    # Assert proposal now has decision == "APPROVED" and decided_at set

async def test_reject_decision_updates_status():
    # Seed PENDING proposal
    # POST /proposals/{id}/decision {"decision": "REJECTED", "humanNote": "Too long"}
    # Assert decision_log record updated with REJECTED + human_note

async def test_modify_with_valid_parameters_succeeds():
    # Seed PENDING proposal with parameterBounds termDays min=1, max=14
    # POST decision {"decision": "MODIFIED", "modifiedParameters": {"termDays": 7}}
    # Assert 200 with verificationResult.constraintsSatisfied == true

async def test_modify_outside_bounds_returns_constraint_violation():
    # POST decision {"decision": "MODIFIED", "modifiedParameters": {"termDays": 60}}
    # (60 > max of 14)
    # Assert 400 with CONSTRAINT_VIOLATION error

async def test_decision_on_unknown_proposal_returns_404():
    # POST /proposals/NONEXISTENT/decision
    # Assert 404
```

#### 3. Audit log
```python
async def test_audit_log_returns_all_completed_decisions():
    # Seed 3 completed proposals (1 APPROVED, 1 REJECTED, 1 MODIFIED)
    # GET /audit-log
    # Assert all 3 returned

async def test_audit_log_date_filter():
    # Seed proposals across different dates
    # GET /audit-log?from_date=2026-07-01&to_date=2026-07-07
    # Assert only proposals in that range returned

async def test_audit_log_export_returns_csv():
    # GET /audit-log/export
    # Assert Content-Type: text/csv
    # Assert response body is valid CSV with header row
```

#### 4. Feedback insights
```python
async def test_feedback_insights_counts_are_correct():
    # Seed 3 APPROVED, 2 REJECTED in last 30 days
    # GET /feedback/insights
    # Assert last30Days.approved == 3, rejected == 2

async def test_feedback_insights_detects_rejection_pattern():
    # Seed 3 REJECTED decisions for long-term deposits
    # GET /feedback/insights
    # Assert rejectionPatterns is non-empty with relevant description
```

#### 5. Chaos endpoint
```python
async def test_chaos_toggle_sets_mode_on_bank_mock():
    # POST /chaos {"service": "bank-mock", "mode": "timeout"}
    # Assert 200
    # (Does not test the actual bank-mock behaviour — that's bank-mock's test)
```

### Frontend Tests (Vitest + React Testing Library)

Create `dashboard/src/__tests__/` directory.

```typescript
// ProposalCard.test.tsx
test("renders confidence score with correct colour for high confidence", () => {
    // Render ProposalCard with confidenceScore=0.87
    // Assert confidence indicator has green colour class
});

test("renders amber colour for medium confidence (0.65)", () => {
    // Assert amber colour class
});

test("renders flagged ambiguities as warning badges", () => {
    // Render card with flaggedAmbiguities: ["LOW_FORECAST_CONFIDENCE"]
    // Assert warning badge is visible with text
});

test("modify panel validates term within bounds", async () => {
    // Render with parameterBounds: {termDays: {min: 1, max: 14}}
    // Set slider to 60 (out of range)
    // Assert submit button disabled or error shown
});

test("approve button calls decision API", async () => {
    // Mock POST /proposals/{id}/decision
    // Click Approve
    // Assert API was called with {"decision": "APPROVED"}
});
```

### Running Tests

```bash
# Backend
cd services/hitl-api
pytest tests/ -v

# Frontend
cd dashboard
npm install
npm run test
```

---

## B2B Enhancements (Future Scope, Note in UI)

These are out of scope for the initial build but should be noted in the dashboard
UI as "coming soon" to signal production-readiness:

- **Role-based approval**: CFO approves > LKR 5M; treasury analyst approves < LKR 1M.
- **Multi-approver workflow**: second sign-off for proposals above board-level
  materiality threshold.
- **Regulatory calendar overlay**: surface upcoming EPF/ETF/WHT dates on the
  audit trail view.
- **PDF export of individual proposals**: for board meeting minutes.
