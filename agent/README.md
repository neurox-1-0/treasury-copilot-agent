# Agent Core — Component 5: Multi-Agent Reasoning Loop

> **Status**: ✅ Built and tested (28/28 tests passing)
> **Location**: `agent/`
> **Tech stack**: LangGraph · Pydantic v2 · SQLAlchemy async · aiosqlite · httpx · tenacity

The Agent Core is the heart of the Treasury Copilot system. It orchestrates a full
**Perceive → Reason → Confidence Check → Disambiguate → Decide → Human Gate → Report**
reasoning loop using LangGraph's `StateGraph`.

---

## Architecture: Three Proofs of Genuine Agency

This is not a pipeline. It is a genuine multi-agent system because:

1. **The Confidence & Conflict Check node actually changes routing** based on data quality
   signals — it does not always proceed to Decide.
2. **The Disambiguate node makes a real stakes-based decision** using a deterministic formula.
   The LLM generates rationale for the chosen path — it does not make the decision.
3. **The feedback loop actually changes future Reason cycle behaviour** based on past human
   rejections — it does not just log them.

---

## Directory Structure

```
agent/
├── graph.py                    # LangGraph StateGraph — build_graph(), run_cycle(), run_report()
├── state.py                    # All shared Pydantic models
├── requirements.txt
│
├── nodes/
│   ├── perceive.py             # Node 1: queries ERP + bank, builds TreasuryState
│   ├── reason.py               # Node 2: feedback loop, forecast, optimizer
│   ├── confidence_check.py     # Node 3: routing gate (4 checks)
│   ├── disambiguate.py         # Node 4: stakes-based ESCALATE / PROCEED_FLAGGED
│   ├── decide.py               # Node 5: constraint verification + ProposedAction
│   └── report.py               # Node 7: APPROVED / REJECTED / MODIFIED / TIMEOUT
│
├── tools/
│   ├── erp_client.py           # HTTP client — ERP mock (port 8001)
│   ├── bank_client.py          # HTTP client — Bank mock (port 8002, OAuth2 + HMAC)
│   ├── forecast_client.py      # HTTP client — Forecaster (port 8003)
│   └── optimizer_client.py     # HTTP client — Optimizer (port 8004)
│
├── memory/
│   ├── cache.py                # In-process stale-data cache
│   └── feedback.py             # Feedback loop — rejection pattern analysis
│
├── db/
│   └── audit_log.py            # SQLite async audit log (SQLAlchemy)
│
├── prompts/
│   └── rationale.py            # LLM prompts + graceful template fallback
│
└── tests/
    ├── conftest.py
    ├── test_graph.py            # 3 full-graph integration tests
    ├── test_feedback_loop.py    # 4 feedback loop unit tests
    └── test_nodes/
        ├── test_perceive.py     # 4 tests
        ├── test_reason.py       # 3 tests
        ├── test_confidence_check.py  # 5 tests
        ├── test_disambiguate.py # 4 tests
        └── test_decide.py       # 5 tests
```

---

## File-by-File Documentation

### `agent/state.py` — Shared Pydantic Models

The single source of truth for all data contracts. Every LangGraph node receives an
`AgentContext` and returns an updated `AgentContext`.

| Model | Purpose |
|---|---|
| `TreasuryGoal` | Static config: company, currency, minimum buffer, yield target |
| `DataFreshness` | Enum: `FRESH` / `STALE` / `MISSING` |
| `DataSourceStatus` | Per-source freshness record with timestamps |
| `Obligation` | A single upcoming payment with `FIXED`/`FLEXIBLE` priority |
| `TreasuryState` | Full financial snapshot (Perceive output) |
| `RejectedAlternative` | An allocation option evaluated but not chosen |
| `ProposedAction` | The concrete recommendation (Decide output) |
| `AgentContext` | The full graph state passed between all nodes |

**Key design choice**: All monetary fields use `Decimal`, not `float`. Financial
calculations must not accumulate floating-point drift.

---

### `agent/memory/cache.py` — Stale-Data Cache

A singleton in-process dict (`DataCache`) tracking the last successful live fetch
per data source. Survives across node calls within a single process but resets on
service restart (intentional — fresh agent should not inherit stale state).

| Function | Behaviour |
|---|---|
| `cache_set(key, data)` | Store fresh data; sets `is_fresh=True` |
| `cache_get(key)` | Returns `(data, DataFreshness, last_fresh_at)` |
| `mark_stale(key)` | Sets `is_fresh=False` without removing data |
| `clear_cache()` | Test teardown only |

---

### `agent/db/audit_log.py` — Async SQLite Audit Log

Persists every proposal lifecycle event. Powers both the HITL gate's idempotency
check and the feedback loop's rejection pattern queries.

**Table**: `decision_log`

| Column | Purpose |
|---|---|
| `proposal_id` | UUID, unique per proposal |
| `content_hash` | SHA-256 of (action_type + key params) — idempotency key |
| `human_decision` | `PENDING` → `APPROVED` / `REJECTED` / `MODIFIED` / `TIMEOUT` |
| `payment_status` | `EXECUTED` / `FAILED` / `UNKNOWN` |

Key functions:
- `insert_proposal(...)` — inserts new `PENDING` record
- `update_decision(...)` — updates with human decision + payment outcome
- `is_duplicate_pending(content_hash)` — idempotency check
- `query_last_n_decisions(company_code, n)` — feedback loop input

---

### `agent/memory/feedback.py` — Feedback Loop

Queries the last N decisions to detect rejection patterns and derive optimizer
constraints for the next Reason cycle.

**Rules (v1)**:
- **Rule 1**: If `termDays > 30` proposals were rejected ≥ 2 times in last 5 cycles → `max_term_days = 30`
- **Rule 2**: If no pattern detected → default `max_term_days = 90`

Returns `FeedbackAdjustments` which the Reason node passes to the optimizer.

---

### `agent/prompts/rationale.py` — LLM Rationale

The **only** place the LLM is called. It generates a 2–4 sentence plain-English
explanation for the human approver. It does **not** make routing decisions.

**Graceful degradation**: If `GEMINI_API_KEY` is unset (or the LLM call fails),
a deterministic template string is returned. All tests run against this fallback.
The fallback output is labelled `[TEMPLATE]` for transparency.

---

### `agent/tools/erp_client.py` — ERP HTTP Client

Wraps the Mock SAP ERP (port 8001). All methods use `tenacity` retry (3 attempts,
exponential back-off). Raises `ERPClientError` on final failure so the Perceive node
can trigger the cache fallback.

| Method | Endpoint | Used by |
|---|---|---|
| `get_cash_positions()` | `ZAPI_CASH_POSITION_SRV` | Perceive |
| `get_open_payables()` | `API_ACCOUNTINGDOCUMENTITEM_SRV` | Perceive |
| `get_payroll_postings()` | `ZAPI_PAYROLL_POSTING_SRV` | Perceive |
| `get_tax_items()` | `ZAPI_TAX_LIABILITY_SRV` | Perceive |
| `get_loan_items()` | `ZAPI_LOAN_SCHEDULE_SRV` | Perceive |

---

### `agent/tools/bank_client.py` — Bank HTTP Client

Wraps the Mock Sampath Bank API (port 8002). Handles:
- **OAuth2 token refresh** — cached in-process with 60-second safety margin
- **HMAC-SHA256 payment signing** — computed over canonical JSON body

| Method | Endpoint | Used by |
|---|---|---|
| `get_account_balances()` | `GET /accounts` + balance per account | Perceive |
| `get_statement(...)` | `GET /accounts/{id}/statement` | Perceive (reconciliation) |
| `get_deposit_rates()` | `GET /rates/deposits` | Reason |
| `initiate_payment(...)` | `POST /payments/initiate` | Report |
| `get_payment_status(...)` | `GET /payments/{id}/status` | Report (polling) |

---

### `agent/nodes/perceive.py` — Perceive Node

**Input**: `AgentContext` with `goal`
**Output**: `AgentContext` with `treasury_state` populated

1. Fetches 7 data sources in sequence (with cache fallback on each)
2. Builds obligation lists (FIXED + FLEXIBLE)
3. Detects unreconciled large credits via bank-vs-ERP comparison (±5% tolerance)
4. Sets `execution_blocked=True` if cash position is `MISSING` with no cache

**Data freshness cascade**:
```
Live fetch succeeds → DataFreshness.FRESH → cache_set
Live fetch fails   → cache_get → DataFreshness.STALE (if cached)
                              → DataFreshness.MISSING (no cache)
MISSING cash position AND bank balance → execution_blocked = True
```

---

### `agent/nodes/reason.py` — Reason Node

**Input**: `AgentContext` with `treasury_state`
**Output**: `AgentContext` with `forecast_result`, `optimizer_result`

1. Skips immediately if `execution_blocked`
2. Calls `compute_feedback_adjustments()` — gets `max_term_days` constraint
3. Fetches deposit rates from bank; filters by `max_term_days`
4. Calls forecaster for 14-day cash-flow prediction
5. Calls optimizer with filtered instruments

On any service failure, sets degraded results (confidence=0.0, constraintsSatisfied=False)
so the Confidence Check node routes appropriately.

---

### `agent/nodes/confidence_check.py` — Confidence & Conflict Check Node

**Input**: `AgentContext` with forecast, optimizer, treasury_state
**Output**: `AgentContext` with `confidence_score`, `conflict_flags`, `route`

The routing gate. Four deterministic checks:

| Check | Condition | Flag | Route |
|---|---|---|---|
| Low forecast confidence | `overallConfidenceScore < 0.6` | `LOW_FORECAST_CONFIDENCE` | DISAMBIGUATE |
| Stale data + materiality | stale AND `surplus > 10% of buffer` | `STALE_DATA_PRESENT` | DISAMBIGUATE |
| Unreconciled large credit | any credit > LKR 1M unreconciled | `UNRECONCILED_LARGE_CREDIT` | DISAMBIGUATE |
| Optimizer infeasible | `constraintsSatisfied == False` | `OPTIMIZER_INFEASIBLE` | DECIDE ← explicit |

Note: infeasible optimizer routes to DECIDE (not DISAMBIGUATE) — the Decide node handles
infeasibility with an explicit `NO_ACTION`.

---

### `agent/nodes/disambiguate.py` — Disambiguate Node

**Input**: `AgentContext` with `conflict_flags`, `treasury_state`
**Output**: `AgentContext` with `disambiguation_path`, `disambiguation_rationale`

Stakes score formula:
```python
amount_ratio = available_surplus / minimum_liquidity_buffer
affects_fixed = "UNRECONCILED_LARGE_CREDIT" or "STALE_DATA_PRESENT" in flags
stakes_score = min(1.0, amount_ratio * 0.5 + (0.5 if affects_fixed else 0.0))

if stakes_score >= 0.6:
    disambiguation_path = "ESCALATE"   # surface ambiguity to human
else:
    disambiguation_path = "PROCEED_FLAGGED"  # continue to Decide with flags attached
```

Then calls `llm_generate_rationale()` for the human-readable explanation only.

---

### `agent/nodes/decide.py` — Decide Node

**Input**: `AgentContext` with all Reason + Confidence + Disambiguation outputs
**Output**: `AgentContext` with `proposed_action`

Processing steps:
1. **Blocked** → `NO_ACTION` immediately
2. **Infeasible** → `NO_ACTION` with infeasibility reason
3. **ESCALATE** path → `ESCALATE` action type
4. **Financial constraint checks** (all three must pass):
   - `allocation_amount ≤ available_surplus`
   - `maturity_date ≤ next_fixed_obligation_date`
   - `buffer_after_deployment ≥ minimum_liquidity_buffer`
5. **Idempotency check** → `DUPLICATE_PENDING` if hash already exists
6. **Build** `ProposedAction` with rationale, alternatives, parameter_bounds
7. **Write** to audit log as `PENDING`

**Key safety rule**: Constraint violations are surfaced as `CONSTRAINT_VIOLATION` — never
silently adjusted or capped.

---

### `agent/nodes/report.py` — Report Node

**Input**: `AgentContext` with `human_decision` populated by HITL
**Output**: `AgentContext` with `payment_result`; audit log closed

| Decision | Behaviour |
|---|---|
| `APPROVED` | Initiates payment → polls status every 30s (max 5 min) → EXECUTED or UNKNOWN |
| `REJECTED` | Logs for feedback loop → closed |
| `MODIFIED` | Re-verifies constraints → executes if valid → rejects modification if not |
| `TIMEOUT` | Auto-closes → logs escalation alert |

**Key safety rule**: A payment is **never retried** after `FAILED` or `UNKNOWN`. Manual
verification is always required.

---

### `agent/graph.py` — Graph Orchestration

Builds and compiles the full `StateGraph(AgentContext)`:

```
perceive → reason → confidence_check →(conditional)→ decide → END
                                            ↓
                                       disambiguate → decide → END
```

**Main entry points**:
- `build_graph()` — builds and compiles the graph (called once)
- `run_cycle(goal)` — initialises fresh `AgentContext`, runs perceive→decide
- `run_report(ctx)` — called by HITL API with human decision populated

---

## Running Tests

```bash
# All 28 tests (no live services required)
cd cashflow-copilot-agent
.venv/Scripts/python -m pytest agent/tests/ -v

# Node unit tests only (fast, ~1s)
.venv/Scripts/python -m pytest agent/tests/test_nodes/ -v

# Feedback loop tests
.venv/Scripts/python -m pytest agent/tests/test_feedback_loop.py -v

# Full graph integration tests
.venv/Scripts/python -m pytest agent/tests/test_graph.py -v
```

**Test results**: 28 passed, 0 failed (as of build date)

---

## Running the Agent (with live services)

1. Start all services:
   ```bash
   # ERP mock (port 8001)
   cd services/erp_mock && uvicorn main:app --port 8001

   # Bank mock (port 8002)
   cd services/bank_mock && uvicorn main:app --port 8002

   # Forecaster (port 8003)
   cd services/forecaster && uvicorn main:app --port 8003

   # Optimizer (port 8004)
   cd services/optimizer && uvicorn main:app --port 8004
   ```

2. Run a reasoning cycle:
   ```python
   import asyncio
   from agent.graph import run_cycle
   from agent.state import TreasuryGoal

   async def main():
       goal = TreasuryGoal()
       ctx = await run_cycle(goal)
       print(f"Action: {ctx.proposed_action.action_type}")
       print(f"Description: {ctx.proposed_action.description}")

   asyncio.run(main())
   ```

3. Submit a human decision (simulating HITL):
   ```python
   from agent.graph import run_report

   ctx.human_decision = "APPROVED"
   ctx = await run_report(ctx)
   print(f"Payment: {ctx.payment_result}")
   ```

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `ERP_BASE_URL` | `http://localhost:8001` | ERP mock URL |
| `BANK_BASE_URL` | `http://localhost:8002` | Bank mock URL |
| `FORECASTER_BASE_URL` | `http://localhost:8003` | Forecaster URL |
| `OPTIMIZER_BASE_URL` | `http://localhost:8004` | Optimizer URL |
| `DATABASE_URL` | `sqlite+aiosqlite:///agent_audit.db` | Audit log DB |
| `BANK_CLIENT_ID` | `treasury-agent` | Bank OAuth2 client ID |
| `BANK_CLIENT_SECRET` | `demo-secret-1234` | Bank OAuth2 secret |
| `PAYMENT_SIGNING_SECRET` | `dev-signing-secret` | Bank HMAC signing key |
| `GEMINI_API_KEY` | _(unset)_ | LLM API key (optional; uses template fallback if absent) |

---

## Design Principles

### 1. Rules Make Decisions, LLM Generates Rationale
The LLM is called **exactly once per cycle** in the Decide node to generate the
`ProposedAction.rationale` string. All routing, branching, and constraint verification
is deterministic and auditable.

### 2. Failure Is a First-Class Output
Every error state surfaces explicitly:
- Stale data → `DataFreshness.STALE` in the state
- Missing data → `execution_blocked = True`
- Infeasible optimizer → `NO_ACTION` with reason
- Constraint violation → `CONSTRAINT_VIOLATION` with the specific constraint named

### 3. PaymentPriority Is Sacred
`FIXED` obligations (payroll, taxes, loans) are treated as hard constraints throughout.
The agent cannot defer them. `FLEXIBLE` (vendor AP) obligations can be deferred within
the `max_payment_risk_days` window.

### 4. HITL Is Mandatory
`requires_human_approval = True` is always set on every `ProposedAction`. The graph
yields after the Decide node. No money moves without a human decision.
