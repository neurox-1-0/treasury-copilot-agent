# Component 5: Agent Core (Multi-Agent Reasoning Loop)

**Status: To build. Highest-priority component after Component 1 is migrated and tested.**

---

## Purpose

Orchestrate the full reasoning loop — Perceive → Reason → Confidence & Conflict
Check → Disambiguate → Decide → Human Gate → Report — using a hierarchical
multi-agent pattern built on LangGraph. This is the component that makes the
system an **agent** rather than a pipeline.

The three pieces that prove genuine agency (protect these before anything else):
1. The Confidence & Conflict Check node **actually changes routing** based on data
   quality signals — not just always proceeding.
2. The Disambiguate node makes a **real stakes-based proceed-vs-escalate decision**
   using deterministic rules, with the LLM generating the rationale for either path.
3. The feedback loop **actually changes future Reason cycle behaviour** based on
   past human decisions — not just logging them.

---

## Canonical Location

```
agent/
├── graph.py               # LangGraph orchestration — defines the full StateGraph
├── state.py               # All shared Pydantic models (TreasuryState, ProposedAction, etc.)
├── nodes/
│   ├── perceive.py
│   ├── reason.py
│   ├── confidence_check.py
│   ├── disambiguate.py
│   ├── decide.py
│   └── report.py
├── tools/
│   ├── erp_client.py      # HTTP client wrapping services/erp-mock
│   ├── bank_client.py     # HTTP client wrapping services/bank-mock
│   ├── forecast_client.py # HTTP client wrapping services/forecaster
│   └── optimizer_client.py
├── memory/
│   ├── cache.py           # DataCache — stale-data tracking
│   └── feedback.py        # Query layer for decision_log table
├── db/
│   └── audit_log.py       # SQLite (dev) / PostgreSQL (prod) audit log
├── prompts/
│   └── rationale.py       # LLM prompt templates for rationale generation
├── tests/
│   ├── test_graph.py
│   ├── test_nodes/
│   │   ├── test_perceive.py
│   │   ├── test_reason.py
│   │   ├── test_confidence_check.py
│   │   ├── test_disambiguate.py
│   │   └── test_decide.py
│   └── test_feedback_loop.py
└── requirements.txt
```

**`requirements.txt`**:
```
langgraph>=0.2.0
langchain-google-genai>=1.0.0   # Gemini Flash
pydantic>=2.7.0
httpx>=0.27.0
tenacity>=8.3.0
sqlalchemy>=2.0.0
aiosqlite>=0.20.0               # async SQLite
python-dotenv>=1.0.0
pytest>=8.2.0
pytest-anyio>=0.0.0
```

---

## Shared State Models (`agent/state.py`)

These are the Pydantic models that flow between all nodes. Define all of them here
before building any node — every node depends on these contracts.

### `TreasuryGoal`

```python
from pydantic import BaseModel
from decimal import Decimal

class TreasuryGoal(BaseModel):
    company_code: str = "1000"
    currency: str = "LKR"
    minimum_liquidity_buffer: Decimal = Decimal("20000000.00")
    target_yield_minimum: float = 0.10      # 10% annualised
    max_payment_risk_days: int = 10         # days overdue before a FLEXIBLE payment is escalated
    goal_profile: str = "BALANCED"          # "CONSERVATIVE" | "BALANCED" | "YIELD_MAXIMISING"
```

### `DataFreshnessFlag`

```python
from enum import Enum

class DataFreshness(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"         # loaded from cache; ERP/bank call failed
    MISSING = "MISSING"     # no cached data available either

class DataSourceStatus(BaseModel):
    source: str             # e.g. "ERP_CASH_POSITION", "BANK_BALANCE"
    freshness: DataFreshness
    last_fresh_at: datetime | None
    stale_reason: str | None = None
```

### `Obligation`

```python
from datetime import date

class Obligation(BaseModel):
    obligation_id: str
    obligation_type: str    # "VENDOR_AP", "PAYROLL", "TAX_VAT", "TAX_WHT", "TAX_EPF", "LOAN"
    amount: Decimal
    due_date: date
    payment_priority: str   # "FIXED" | "FLEXIBLE"
    vendor_id: str | None = None
    description: str | None = None
    is_overdue: bool = False
```

### `TreasuryState`

```python
class TreasuryState(BaseModel):
    """Output of the Perceive agent. Input to all downstream nodes."""
    company_code: str
    as_of: datetime
    total_available_balance: Decimal
    accounts: list[dict]                    # raw account balance records
    obligations: list[Obligation]           # all upcoming obligations (sorted by due_date)
    fixed_obligations: list[Obligation]     # filtered: payment_priority == "FIXED"
    flexible_obligations: list[Obligation]  # filtered: payment_priority == "FLEXIBLE"
    next_fixed_obligation_date: date | None
    next_fixed_obligation_amount: Decimal | None
    available_surplus: Decimal              # total_available_balance - minimum_buffer
    data_source_statuses: list[DataSourceStatus]
    has_stale_data: bool
    unreconciled_large_credits: list[dict]  # credits in bank statement not in ERP
    execution_blocked: bool = False         # True if a critical data failure prevents action
    block_reason: str | None = None
```

### `ProposedAction`

```python
import hashlib, json

class RejectedAlternative(BaseModel):
    option: str
    reason_rejected: str
    expected_yield: Decimal | None = None

class ProposedAction(BaseModel):
    """Output of the Decide agent. Input to the HITL gate and Report node."""
    proposal_id: str                        # UUID
    action_type: str                        # "SURPLUS_ALLOCATION" | "PAYMENT_DEFERRAL" | "NO_ACTION" | "ESCALATE"
    description: str                        # Plain-language action description
    rationale: str                          # LLM-generated rationale (rules chose the path; LLM explains it)
    alternatives_rejected: list[RejectedAlternative]
    overall_confidence_score: float
    flagged_ambiguities: list[str]
    parameter_bounds: dict                  # e.g. {"termDays": {"min": 1, "max": 14}}
    requires_human_approval: bool = True    # Always True in this system
    content_hash: str                       # SHA256 of (action_type + key parameters) for idempotency
    created_at: datetime

    @classmethod
    def compute_hash(cls, action_type: str, parameters: dict) -> str:
        canonical = json.dumps({"action_type": action_type, **parameters}, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()
```

### `AgentContext`

```python
class AgentContext(BaseModel):
    """Full graph state — passed between all LangGraph nodes."""
    goal: TreasuryGoal
    treasury_state: TreasuryState | None = None
    forecast_result: dict | None = None
    optimizer_result: dict | None = None
    confidence_score: float | None = None
    conflict_flags: list[str] = []
    disambiguation_path: str | None = None  # "PROCEED_FLAGGED" | "ESCALATE"
    proposed_action: ProposedAction | None = None
    human_decision: str | None = None       # "APPROVED" | "REJECTED" | "MODIFIED"
    human_modified_parameters: dict | None = None
    human_note: str | None = None
    payment_result: dict | None = None
    cycle_id: str                           # UUID for this full loop run
```

---

## Cache Layer (`agent/memory/cache.py`)

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Any

@dataclass
class CacheEntry:
    data: Any
    last_fresh_at: datetime
    is_fresh: bool

# Singleton in-process cache — reset on service restart
DataCache: dict[str, CacheEntry] = {}

def cache_set(key: str, data: Any) -> None:
    DataCache[key] = CacheEntry(data=data, last_fresh_at=datetime.utcnow(), is_fresh=True)

def cache_get(key: str) -> tuple[Any, DataFreshness, datetime | None]:
    entry = DataCache.get(key)
    if entry is None:
        return None, DataFreshness.MISSING, None
    return entry.data, DataFreshness.STALE, entry.last_fresh_at

def mark_stale(key: str) -> None:
    if key in DataCache:
        DataCache[key].is_fresh = False
```

---

## Decision Log (`agent/db/audit_log.py`)

SQLite (dev) / PostgreSQL (prod). Use SQLAlchemy with async support.

### Table: `decision_log`

```sql
CREATE TABLE decision_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id        TEXT NOT NULL,
    proposal_id     TEXT NOT NULL UNIQUE,
    company_code    TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    description     TEXT,
    rationale       TEXT,
    confidence_score REAL,
    flagged_ambiguities TEXT,           -- JSON array
    disambiguation_path TEXT,
    proposed_at     TIMESTAMP NOT NULL,
    human_decision  TEXT,               -- APPROVED | REJECTED | MODIFIED | PENDING | TIMEOUT
    modified_parameters TEXT,           -- JSON object
    human_note      TEXT,
    decided_at      TIMESTAMP,
    payment_id      TEXT,               -- from bank mock
    payment_status  TEXT,               -- EXECUTED | FAILED | UNKNOWN
    closed_at       TIMESTAMP,
    content_hash    TEXT NOT NULL       -- for idempotency checks
);
```

### Key queries the feedback loop runs

```python
# Check if this exact proposal already has a PENDING approval
def is_duplicate_pending(content_hash: str) -> bool:
    return db.query("SELECT 1 FROM decision_log WHERE content_hash=? AND human_decision='PENDING'", content_hash)

# Check recent rejection patterns
def count_rejections_for_type(action_type: str, term_days: int, lookback_n: int = 5) -> int:
    return db.query("""
        SELECT COUNT(*) FROM decision_log
        WHERE action_type=? AND human_decision='REJECTED'
        AND JSON_EXTRACT(modified_parameters, '$.termDays') IS NOT NULL
        ORDER BY proposed_at DESC LIMIT ?
    """, action_type, lookback_n)
```

---

## Node-by-Node Specification

### Node 1: Perceive (`agent/nodes/perceive.py`)

**Input**: `AgentContext` (goal only)
**Output**: `AgentContext` with `treasury_state` populated

Calls (with tenacity retry — see Component 7):
1. `erp_client.get_cash_positions()` — ERP balances
2. `bank_client.get_account_balances()` — live bank balances
3. `erp_client.get_open_payables()` — vendor AP
4. `erp_client.get_payroll_postings()` — payroll
5. `erp_client.get_tax_items()` — all statutory taxes
6. `erp_client.get_loan_items()` — loan schedule
7. `bank_client.get_statement(last_30_days)` — detect unreconciled credits

Build the `TreasuryState` from the combined data. Detect unreconciled large credits:
items in bank statement that appear as credits but have no matching AR document in
the ERP within the same date and amount (±5%). Flag these in
`unreconciled_large_credits`.

If any ERP call fails: use cache + `DataFreshness.STALE`. If critical cash position
data is `MISSING` (no cache either): set `execution_blocked=True`,
`block_reason="Cash position data unavailable and no cache exists. Cannot assess liquidity."`.

---

### Node 2: Reason (`agent/nodes/reason.py`)

**Input**: `AgentContext` with `treasury_state`
**Output**: `AgentContext` with `forecast_result`, `optimizer_result`

1. Check `treasury_state.execution_blocked` — if True, skip and pass through.
2. Call `forecast_client.get_forecast(company_code, horizon_days=14)`.
3. Call `optimizer_client.get_allocation(treasury_state, instruments_from_bank)`.

**Feedback loop integration**:
Before calling the optimizer, query `feedback.py` for recent rejection patterns:
```python
recent_rejections = count_rejections_for_type("SURPLUS_ALLOCATION", lookback_n=5)
# If >60-day deposits were rejected twice in last 5 cycles, cap term at 30 days
adjusted_max_term = 30 if recent_rejections.long_term_rejected >= 2 else 90
```
Pass `adjusted_max_term` as a filter to the optimizer's instrument list.

---

### Node 3: Confidence & Conflict Check (`agent/nodes/confidence_check.py`)

**Input**: `AgentContext` with forecast and optimizer results
**Output**: `AgentContext` with `confidence_score`, `conflict_flags`; routes to either
`decide` or `disambiguate`

This is **the core reasoning gate**. Implement all four checks:

```python
def check_confidence_and_conflicts(ctx: AgentContext) -> tuple[float, list[str], str]:
    flags = []
    route = "DECIDE"  # default

    # 1. Low forecast confidence
    score = ctx.forecast_result["overallConfidenceScore"]
    if score < 0.6:
        flags.append("LOW_FORECAST_CONFIDENCE")
        route = "DISAMBIGUATE"

    # 2. Stale data check + materiality
    if ctx.treasury_state.has_stale_data:
        flags.append("STALE_DATA_PRESENT")
        materiality_threshold = ctx.goal.minimum_liquidity_buffer * Decimal("0.1")
        if ctx.treasury_state.available_surplus > materiality_threshold:
            route = "DISAMBIGUATE"

    # 3. Conflicting signal: unreconciled credits above a threshold
    large_unreconciled = [
        c for c in ctx.treasury_state.unreconciled_large_credits
        if Decimal(c["amount"]) > Decimal("1000000.00")
    ]
    if large_unreconciled:
        flags.append("UNRECONCILED_LARGE_CREDIT")
        route = "DISAMBIGUATE"

    # 4. Optimizer infeasibility
    if not ctx.optimizer_result.get("constraintsSatisfied", True):
        flags.append("OPTIMIZER_INFEASIBLE")
        route = "DECIDE"   # still proceed — Decide will surface the infeasibility

    return score, flags, route
```

The LangGraph edge from this node uses `route` as the conditional:
```python
graph.add_conditional_edges("confidence_check", lambda ctx: ctx.route, {
    "DECIDE": "decide",
    "DISAMBIGUATE": "disambiguate",
})
```

---

### Node 4: Disambiguate (`agent/nodes/disambiguate.py`)

**Input**: `AgentContext` with `conflict_flags`
**Output**: `AgentContext` with `disambiguation_path` set

This node makes a **stakes-based decision using deterministic rules**. The LLM is
called only to generate a human-readable rationale for the chosen path — not to
make the decision itself.

```python
def compute_stakes_score(ctx: AgentContext) -> float:
    """
    Normalised score 0–1 representing decision stakes.
    Higher score = higher stakes = lean toward escalation.
    """
    amount_ratio = float(ctx.treasury_state.available_surplus / ctx.goal.minimum_liquidity_buffer)
    affects_fixed = any(
        f in ctx.conflict_flags for f in ["UNRECONCILED_LARGE_CREDIT", "STALE_DATA_PRESENT"]
    )
    score = min(1.0, amount_ratio * 0.5 + (0.5 if affects_fixed else 0.0))
    return score

def disambiguate(ctx: AgentContext) -> AgentContext:
    stakes = compute_stakes_score(ctx)
    threshold = 0.6  # configurable via goal_profile

    if stakes >= threshold:
        ctx.disambiguation_path = "ESCALATE"
        # Escalate = send to HITL with the ambiguity itself surfaced, not just the action
    else:
        ctx.disambiguation_path = "PROCEED_FLAGGED"
        # Proceed but flag ambiguities in the ProposedAction

    # Call LLM to generate rationale string (not to make the decision)
    ctx.disambiguation_rationale = llm_generate_rationale(ctx, ctx.disambiguation_path)
    return ctx
```

**Escalation path**: `disambiguation_path == "ESCALATE"` sends the proposal to
HITL with `action_type="ESCALATE"` and `description="Human review required: [flags]"`.
The agent does not attempt a recommendation — it surfaces the ambiguity for human
judgment.

**Proceed-flagged path**: `disambiguation_path == "PROCEED_FLAGGED"` continues to
the Decide node. All active flags are added to `ProposedAction.flagged_ambiguities`.

---

### Node 5: Decide (`agent/nodes/decide.py`)

**Input**: `AgentContext` with all Reason and disambiguation results
**Output**: `AgentContext` with `proposed_action`

**Step 1: Idempotency check**
```python
content_hash = ProposedAction.compute_hash(action_type, key_parameters)
if is_duplicate_pending(content_hash):
    # This exact proposal already awaits human approval — skip generation
    ctx.proposed_action = None
    ctx.skip_reason = "DUPLICATE_PENDING"
    return ctx
```

**Step 2: Constraint verification**
If `treasury_state.execution_blocked`:
- `action_type = "NO_ACTION"`, `description = block_reason`, skip to Report.

If `optimizer_result["constraintsSatisfied"] == false`:
- `action_type = "NO_ACTION"`, `description = infeasibilityReason`.

Otherwise verify:
- Recommended allocation amount ≤ `available_surplus`.
- Recommended allocation maturity date ≤ `next_fixed_obligation_date` (re-check).
- `buffer_after_deployment >= minimum_liquidity_buffer`.

If any verification fails: **block the action explicitly** — do not silently adjust.
Set `action_type = "CONSTRAINT_VIOLATION"`, surface the specific constraint that
failed and why.

**Step 3: Build `ProposedAction`**
- Assemble from optimizer output + forecast + disambiguation results.
- Call LLM once to generate `rationale` (a 2–4 sentence plain-English explanation
  connecting the numbers to the decision — not bullet points).
- Set `parameter_bounds` from the safe instrument's term range.
- Set `requires_human_approval = True` always.

**Step 4: Write to audit log**
```python
audit_log.insert(cycle_id, proposal_id, action_type, ..., human_decision="PENDING")
```

---

### Node 6: Human Gate (external — see Component 6)

The agent yields after producing `ProposedAction`. Execution resumes when the HITL
API POSTs a decision back to the agent.

**Approval timeout**: If no human decision is received within `APPROVAL_TIMEOUT_HOURS`
(default: 24, configurable), the Report node auto-closes the proposal as `TIMEOUT`
and sends an escalation alert (POST to a configured webhook URL or email — even a
log line is acceptable for the demo). The agent loop then restarts a fresh Perceive
cycle.

---

### Node 7: Report (`agent/nodes/report.py`)

**Input**: `AgentContext` with `human_decision` populated
**Output**: Closed audit log entry + (if approved) payment trigger

On `APPROVED`:
1. Call `bank_client.initiate_payment(proposed_action)`.
2. Poll `bank_client.get_payment_status(payment_id)` until `EXECUTED`, `FAILED`,
   or timeout (5 minutes, polling every 30 seconds).
3. On `EXECUTED`: update audit log `payment_status="EXECUTED"`, `closed_at=now`.
4. On `FAILED` or timeout: update `payment_status="UNKNOWN"`,
   add note: `"Manual verification required: payment status unknown after 5 minutes"`.
   **Do not retry the payment** — this is the key safety rule.

On `REJECTED`:
1. Update audit log `human_decision="REJECTED"`.
2. Write feedback record to `decision_log` for the feedback loop.

On `MODIFIED`:
1. Re-run constraint verification on the modified parameters.
2. If constraints still pass: proceed to payment execution with modified parameters.
3. If constraints fail: reject the modification, surface reason back to HITL.

---

## LLM Usage Pattern (`agent/prompts/rationale.py`)

The LLM (Gemini Flash) is called **only for rationale generation** — never for
control-flow decisions. All routing, branching, and verification is deterministic.

```python
RATIONALE_PROMPT = """
You are a treasury analyst generating a concise explanation for a treasury decision.

Decision made: {decision_path}
Proposed action: {action_description}
Key numbers:
  - Available surplus: {surplus} LKR
  - Minimum buffer required: {buffer} LKR
  - Next fixed obligation: {obligation_amount} LKR on {obligation_date}
  - Recommended instrument: {instrument} at {rate}% for {term_days} days
  - Expected yield: {yield_amount} LKR

Rejected alternatives:
{alternatives}

Confidence flags: {flags}

Write a 2-4 sentence explanation of why this recommendation was chosen, in plain
language suitable for a CFO. Reference the specific numbers above. Do not use
bullet points. Do not hedge excessively.
"""
```

---

## Feedback Loop (`agent/memory/feedback.py`)

The feedback loop adjusts the Reason node's optimizer inputs based on past human
decisions. It is queried at the start of every Reason cycle.

```python
class FeedbackAdjustments(BaseModel):
    max_term_days: int = 90             # default unconstrained
    excluded_instruments: list[str] = []
    note: str | None = None

def compute_feedback_adjustments(company_code: str) -> FeedbackAdjustments:
    """
    Query last 5 decisions and derive adjustments for the optimizer.
    """
    recent = query_last_n_decisions(company_code, n=5)

    # Rule 1: if long-term (>30 day) proposals were rejected 2+ times, cap term
    long_term_rejections = sum(
        1 for d in recent
        if d.human_decision == "REJECTED" and d.get_term_days() > 30
    )
    if long_term_rejections >= 2:
        return FeedbackAdjustments(
            max_term_days=30,
            note="Capped at 30 days: long-term deposits rejected 2+ times in last 5 cycles."
        )

    # Rule 2: if all proposals were approved, no constraint needed
    return FeedbackAdjustments()
```

---

## Graph Definition (`agent/graph.py`)

```python
from langgraph.graph import StateGraph, END

def build_graph() -> CompiledGraph:
    builder = StateGraph(AgentContext)

    builder.add_node("perceive", perceive_node)
    builder.add_node("reason", reason_node)
    builder.add_node("confidence_check", confidence_check_node)
    builder.add_node("disambiguate", disambiguate_node)
    builder.add_node("decide", decide_node)
    builder.add_node("report", report_node)

    builder.set_entry_point("perceive")
    builder.add_edge("perceive", "reason")
    builder.add_edge("reason", "confidence_check")
    builder.add_conditional_edges(
        "confidence_check",
        route_after_confidence_check,  # returns "decide" or "disambiguate"
        {"decide": "decide", "disambiguate": "disambiguate"}
    )
    builder.add_edge("disambiguate", "decide")
    builder.add_edge("decide", END)   # agent yields; resumes when HITL decision arrives
    # report is triggered externally when HITL decision is received

    return builder.compile()
```

---

## Testing Requirements

### Test files: `agent/tests/`

#### `test_nodes/test_perceive.py`
```python
async def test_perceive_builds_treasury_state_from_mocked_services():
    # Mock erp_client and bank_client to return valid seed data
    # Assert treasury_state.total_available_balance > 0
    # Assert treasury_state.obligations is non-empty
    # Assert fixed_obligations all have payment_priority == "FIXED"
    # Assert flexible_obligations all have payment_priority == "FLEXIBLE"

async def test_perceive_marks_stale_when_erp_fails():
    # Mock erp_client.get_cash_positions() to raise an exception
    # Assert treasury_state.has_stale_data == True
    # Assert data_source_statuses contains STALE entry for ERP_CASH_POSITION

async def test_perceive_sets_execution_blocked_when_cash_missing():
    # Mock all ERP calls to fail AND cache is empty
    # Assert treasury_state.execution_blocked == True
    # Assert treasury_state.block_reason is non-empty

async def test_perceive_detects_unreconciled_large_credit():
    # Mock bank statement to include a LKR 5M credit with no ERP match
    # Assert treasury_state.unreconciled_large_credits is non-empty
```

#### `test_nodes/test_confidence_check.py`
```python
async def test_routes_to_disambiguate_on_low_confidence():
    # Inject forecast with overallConfidenceScore=0.4
    # Assert route == "DISAMBIGUATE"
    # Assert "LOW_FORECAST_CONFIDENCE" in conflict_flags

async def test_routes_to_decide_on_high_confidence():
    # Inject forecast with overallConfidenceScore=0.9, no stale data
    # Assert route == "DECIDE"

async def test_routes_to_disambiguate_on_stale_data_with_high_materiality():
    # Inject stale data AND available_surplus > 10% of buffer
    # Assert route == "DISAMBIGUATE"
    # Assert "STALE_DATA_PRESENT" in conflict_flags

async def test_routes_to_disambiguate_on_unreconciled_large_credit():
    # Inject unreconciled credit of LKR 2M
    # Assert "UNRECONCILED_LARGE_CREDIT" in conflict_flags
    # Assert route == "DISAMBIGUATE"
```

#### `test_nodes/test_disambiguate.py`
```python
async def test_high_stakes_routes_to_escalate():
    # available_surplus = 50% of buffer (high ratio), affects_fixed = True
    # Assert disambiguation_path == "ESCALATE"

async def test_low_stakes_routes_to_proceed_flagged():
    # available_surplus = 5% of buffer (low ratio), affects_fixed = False
    # Assert disambiguation_path == "PROCEED_FLAGGED"

async def test_rationale_string_is_generated():
    # Assert disambiguation_rationale is non-empty string after disambiguate runs
```

#### `test_nodes/test_decide.py`
```python
async def test_idempotency_skips_duplicate_pending():
    # Pre-insert a PENDING decision_log record with the expected content_hash
    # Run decide node
    # Assert proposed_action is None and skip_reason == "DUPLICATE_PENDING"

async def test_infeasible_optimizer_produces_no_action():
    # Inject optimizer result with constraintsSatisfied=false
    # Assert proposed_action.action_type == "NO_ACTION"

async def test_execution_blocked_produces_no_action():
    # Inject treasury_state.execution_blocked == True
    # Assert proposed_action.action_type == "NO_ACTION"

async def test_constraint_violation_is_surfaced_not_silently_adjusted():
    # Inject allocation that would breach buffer
    # Assert proposed_action.action_type == "CONSTRAINT_VIOLATION"
    # Assert rationale explains which constraint failed

async def test_valid_proposal_has_required_fields():
    # Valid treasury_state, forecast, optimizer result
    # Assert proposed_action has: proposal_id, action_type, description, rationale,
    #   alternatives_rejected, confidence_score, content_hash, parameter_bounds
```

#### `test_feedback_loop.py`
```python
async def test_feedback_caps_term_after_two_long_term_rejections():
    # Insert 2 REJECTED decisions for 90-day FD in decision_log
    # Call compute_feedback_adjustments()
    # Assert result.max_term_days == 30

async def test_feedback_no_constraint_when_all_approved():
    # Insert 5 APPROVED decisions
    # Call compute_feedback_adjustments()
    # Assert result.max_term_days == 90 (no constraint)
```

#### `test_graph.py`
```python
async def test_full_graph_runs_happy_path():
    # Mock all external services (ERP, bank, forecaster, optimizer) to return valid data
    # Run graph.invoke(initial_context)
    # Assert proposed_action is generated with action_type == "SURPLUS_ALLOCATION"
    # Assert audit log has one PENDING entry

async def test_full_graph_reaches_disambiguate_on_low_confidence():
    # Mock forecaster to return overallConfidenceScore=0.3
    # Run graph
    # Assert disambiguation_path is set (graph passed through disambiguate node)

async def test_full_graph_no_action_on_execution_blocked():
    # Mock all ERP calls to fail, no cache
    # Run graph
    # Assert proposed_action.action_type == "NO_ACTION"
```

### Running Tests

```bash
cd agent
pytest tests/ -v

# Run only unit tests (no external services needed — all mocked):
pytest tests/test_nodes/ -v

# Run integration test (requires erp-mock and bank-mock running):
pytest tests/test_graph.py -v --integration
```
