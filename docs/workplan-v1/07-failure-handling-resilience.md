# Component 7: Failure Handling & Resilience

**Status: Cuts across all components — implement alongside each component, not
as a final step.**

---

## Purpose

Prove the system is "safe by design" rather than just functional on the happy
path. Every external call the agent makes must have a defined failure mode, a
defined fallback, and explicit surface-level signalling — never silent guessing.

---

## Core Principle

> **The agent must always know the difference between "I tried and succeeded",
> "I tried and failed but used a fallback", and "I tried and don't know what
> happened." These three states have very different downstream consequences —
> especially when real money is involved.**

---

## The Cache Layer (Referenced Across All Components)

Define once in `agent/memory/cache.py`, use everywhere.

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

@dataclass
class CacheEntry:
    data: Any
    last_fresh_at: datetime
    is_fresh: bool = True

# In-process singleton — reset on service restart
# Key: string identifier (e.g. "ERP_CASH_POSITION_1000")
DataCache: dict[str, CacheEntry] = {}

def cache_set(key: str, data: Any) -> None:
    """Called after every successful external fetch."""
    DataCache[key] = CacheEntry(data=data, last_fresh_at=datetime.utcnow(), is_fresh=True)

def cache_get_stale(key: str) -> tuple[Any | None, datetime | None]:
    """Called as fallback when a live fetch fails. Returns (data, last_fresh_at) or (None, None)."""
    entry = DataCache.get(key)
    if entry is None:
        return None, None
    return entry.data, entry.last_fresh_at

def cache_has(key: str) -> bool:
    return key in DataCache
```

**Note**: This is intentionally an in-process `dict` — simple, no dependency on
Redis or any external store for the demo. On service restart, the cache is lost
and the first cycle's reads are cold. This is acceptable for a demo context.

---

## The Retry Wrapper Pattern

Use `tenacity` for transient failures on all external HTTP calls. Apply
consistently via decorator on the client functions in `agent/tools/`.

```python
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError
import httpx

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True
)
async def _fetch_with_retry(url: str, headers: dict) -> dict:
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
```

The caller (e.g. `erp_client.get_cash_positions`) wraps this in a try/except:

```python
async def get_cash_positions(company_code: str) -> tuple[Any, DataFreshness, datetime | None]:
    cache_key = f"ERP_CASH_POSITION_{company_code}"
    try:
        data = await _fetch_with_retry(ERP_CASH_POSITION_URL)
        cache_set(cache_key, data)
        return data, DataFreshness.FRESH, datetime.utcnow()
    except (RetryError, httpx.HTTPError) as e:
        cached_data, last_fresh_at = cache_get_stale(cache_key)
        freshness = DataFreshness.STALE if cached_data is not None else DataFreshness.MISSING
        return cached_data, freshness, last_fresh_at
```

This pattern is **identical across all read operations** (ERP and bank balance/statement
calls). Never bury `DataFreshness.STALE` inside a generic `except: pass`.

---

## Failure Modes by Component

### ERP Mock Failures (Perceive Node)

| Failure | Tenacity behaviour | Fallback | TreasuryState signal |
|---|---|---|---|
| Timeout (> 3s) | Retry 3x with backoff | Use cached response | `DataFreshness.STALE` + `stale_reason` |
| Non-200 HTTP response | Retry 3x | Use cached response | `DataFreshness.STALE` |
| Malformed JSON | Do not retry (not transient) | Use cached response | `DataFreshness.STALE + "MALFORMED_RESPONSE"` |
| All retries exhausted, no cache | Give up | None | `DataFreshness.MISSING`, `execution_blocked=True` |

When any ERP entity returns `STALE` or `MISSING`, the Perceive node sets
`treasury_state.has_stale_data = True`. The Confidence & Conflict Check node
then evaluates whether the stale data affects a material decision.

**Critical rule**: If `CashPosition` specifically is `MISSING` (no cache), set
`execution_blocked = True`. The agent cannot assess liquidity without this data.
All other entities being stale is recoverable; missing cash position is not.

---

### Bank Mock Failures (Perceive + Decide/Report Nodes)

#### Read failures (balance/statement checks)
Same cache-then-flag pattern as ERP. The key difference: bank balance is used to
cross-check ERP cash position. If bank balance is stale:
- Add `"BANK_BALANCE_STALE"` to conflict flags.
- The Confidence Check node will route to Disambiguate.

#### Write failure — payment initiation (most critical case)

```python
async def initiate_payment(payload: dict) -> tuple[str | None, PaymentWriteStatus]:
    try:
        response = await _post_with_no_retry(BANK_PAYMENT_URL, payload)
        # No retry on payment initiation — retrying a payment POST risks duplicating it
        return response["paymentId"], PaymentWriteStatus.SUBMITTED
    except httpx.TimeoutException:
        return None, PaymentWriteStatus.UNKNOWN   # might or might not have gone through
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (422, 400):
            return None, PaymentWriteStatus.REJECTED  # definitive rejection
        return None, PaymentWriteStatus.UNKNOWN       # 5xx — status unknown
```

**On `PaymentWriteStatus.UNKNOWN`** — the Report node must:
1. Set `payment_status = "UNKNOWN"` in the audit log.
2. Surface the state in the HITL dashboard as: *"Payment status unknown — manual
   bank verification required before closing this cycle."*
3. **Do not retry the payment.** Do not guess. Do not close the audit trail.
4. Send a notification via the webhook.

This is the most important failure policy in the system: guessing wrong about
whether real money moved is categorically worse than pausing.

#### Insufficient funds (not a technical failure)
The Decide node's constraint check should catch insufficient funds *before* calling
the bank API by comparing the approved allocation against the last-known balance.
If the bank API returns `422 INSUFFICIENT_FUNDS` anyway (e.g. due to a concurrent
debit not yet reflected in the agent's cache):
- Treat as a constraint violation.
- Set `payment_status = "REJECTED"`, `block_reason = "Insufficient funds at execution time"`.
- Report to HITL dashboard. Do not retry.

---

### Forecasting Tool Failures (Reason Node)

| Failure | Behaviour |
|---|---|
| `overallConfidenceScore < 0.6` | Not a failure — route to Disambiguate per Confidence Check |
| Forecaster service unavailable (HTTP error after retries) | Fall back to 30-day trailing average heuristic; set `fallbackUsed=true`, `modelType="HEURISTIC_FALLBACK"` |
| Insufficient historical data (400 response) | Fall back to heuristic with a reduced lookback window; flag `INSUFFICIENT_HISTORY` |
| Degenerate output (all predictions = 0) | Flag `DEGENERATE_MODEL_OUTPUT`, route to Disambiguate |

The heuristic fallback is defined in `services/forecaster/model/stub.py` (same as
the stub forecaster). The forecaster service itself handles the fallback and always
returns a valid output contract — the agent does not need to implement forecasting
logic itself.

---

### Optimizer Tool Failures (Reason Node)

| Failure | Behaviour |
|---|---|
| `constraintsSatisfied: false` | Valid output. Decide node surfaces `"NO_ACTION"` with infeasibility reason |
| LP solver returns failure status | Optimizer auto-falls back to greedy; marks `solverUsed="GREEDY_FALLBACK"` |
| Optimizer service unavailable | Reason node produces `NO_ACTION` with reason `"Optimizer unavailable — cannot safely recommend allocation"`. Do not guess. |
| Empty instruments list | Optimizer returns `400`. Reason node: `NO_ACTION`, `"No deposit instruments available from bank"`. |

---

## Approval Timeout (Agent → HITL)

If the human has not acted on a proposal within `APPROVAL_TIMEOUT_HOURS` (default
24 hours):

1. The Report node marks the proposal `TIMEOUT` in the audit log.
2. Sends a notification: `POST NOTIFICATION_WEBHOOK_URL {"event": "APPROVAL_TIMEOUT", ...}`.
3. The Orchestrator restarts a fresh Perceive cycle (does not re-propose the same
   action — fresh data may have changed the situation).

This prevents proposals from sitting in `PENDING` state indefinitely while the
agent loop stalls.

---

## LangGraph Terminal States

The Report node must handle these explicitly — no unhandled exceptions that
silently terminate the graph:

| State | `execution_blocked` | `payment_status` | Dashboard display |
|---|---|---|---|
| Happy path: executed | false | `EXECUTED` | ✅ Completed |
| Payment status unknown | false | `UNKNOWN` | ⚠️ Manual verification required |
| Payment rejected | false | `REJECTED` | ❌ Rejected by bank |
| No action (infeasible) | false | `N/A` | ℹ️ No action taken |
| Execution blocked | true | `N/A` | 🔴 Data unavailable |
| Approval timeout | false | `N/A` | ⏱️ Timed out — new cycle started |

All terminal states update the audit log. No state is silently swallowed.

---

## Chaos Panel Integration

The demo failure scenarios are triggered via the Chaos Panel in the HITL dashboard
(`POST /chaos`), which proxies to mock service chaos endpoints:

### Scripted Failure Demo Scenarios

These are the specific scenarios to demonstrate — prepare a script for each:

#### Scenario 1: ERP Timeout → Stale Data → Materiality-Gated Routing
1. Chaos panel: set ERP to `timeout`.
2. Run agent cycle.
3. **Expected**: Perceive uses cached cash position (STALE). Confidence Check detects
   stale data and evaluates materiality. If `available_surplus > 10% of buffer`,
   routes to Disambiguate. Proposal includes `STALE_DATA_PRESENT` flag.

#### Scenario 2: Bank Write Failure → Manual Verification State
1. Human approves a proposal.
2. Chaos panel: set bank-mock to `write_failure`.
3. Report node calls payment initiation.
4. **Expected**: Status `UNKNOWN` in audit log. Dashboard shows "Manual verification
   required" banner. Agent does not retry or make assumptions.

#### Scenario 3: Low Forecast Confidence → Stakes-Based Routing
1. Chaos panel: set forecaster to override `overallConfidenceScore=0.3`.
2. Run agent cycle.
3. **Expected**: Confidence Check routes to Disambiguate. Disambiguate evaluates
   stakes score. If high stakes → `action_type="ESCALATE"`. If low stakes →
   `PROCEED_FLAGGED` with `LOW_FORECAST_CONFIDENCE` in ambiguities.

#### Scenario 4: Infeasible Optimisation
1. Chaos panel: set optimizer to return `constraintsSatisfied=false`.
2. Run agent cycle.
3. **Expected**: Decide node produces `action_type="NO_ACTION"`,
   `description="No valid allocation exists without breaching liquidity buffer"`.
   Dashboard shows this clearly — no forced recommendation.

#### Scenario 5: Unreconciled Large Credit → Conflict Detection
1. Mock bank statement to include a LKR 5M credit not in ERP.
2. Run agent cycle.
3. **Expected**: Perceive detects unreconciled credit. Confidence Check adds
   `UNRECONCILED_LARGE_CREDIT` flag and routes to Disambiguate.

---

## Testing Requirements

### Test file: `agent/tests/test_resilience.py`

#### Cache behaviour
```python
async def test_cache_set_and_get():
    cache_set("TEST_KEY", {"balance": "1000"})
    data, last_fresh = cache_get_stale("TEST_KEY")
    assert data == {"balance": "1000"}
    assert last_fresh is not None

async def test_cache_get_returns_none_for_missing_key():
    data, last_fresh = cache_get_stale("NONEXISTENT")
    assert data is None
    assert last_fresh is None
```

#### ERP client resilience
```python
async def test_erp_client_returns_stale_on_timeout(mock_erp_timeout):
    # Mock ERP to timeout on all 3 retry attempts
    # Pre-populate cache with stale data
    data, freshness, _ = await get_cash_positions("1000")
    assert freshness == DataFreshness.STALE
    assert data is not None   # returned from cache

async def test_erp_client_returns_missing_when_no_cache(mock_erp_timeout):
    # Mock ERP to timeout, no cached data
    data, freshness, _ = await get_cash_positions("1000")
    assert freshness == DataFreshness.MISSING
    assert data is None

async def test_erp_client_does_not_raise_on_failure(mock_erp_500):
    # Mock ERP to return 500
    # Assert function returns gracefully (no unhandled exception)
    result = await get_cash_positions("1000")
    assert result is not None  # returns a tuple, not raises
```

#### Bank client — write failure
```python
async def test_payment_write_failure_returns_unknown_status(mock_bank_write_timeout):
    payment_id, status = await initiate_payment(valid_payload)
    assert status == PaymentWriteStatus.UNKNOWN
    assert payment_id is None

async def test_payment_definitive_rejection_returns_rejected(mock_bank_422):
    payment_id, status = await initiate_payment(valid_payload)
    assert status == PaymentWriteStatus.REJECTED
    assert payment_id is None

async def test_payment_initiation_not_retried(mock_bank_write_timeout):
    # Assert that the payment POST is called exactly once (no retry)
    # (Use mock call count assertion)
    with patch("bank_client._post_with_no_retry") as mock_post:
        await initiate_payment(valid_payload)
        assert mock_post.call_count == 1   # never retried
```

#### Report node — UNKNOWN state
```python
async def test_report_node_sets_unknown_in_audit_log(mock_bank_write_unknown):
    # Run report node with APPROVED decision and bank write failure
    ctx = await report_node(approved_context)
    log_entry = audit_log.get(ctx.proposed_action.proposal_id)
    assert log_entry.payment_status == "UNKNOWN"
    # Assert no payment retry attempted
    assert mock_bank_client.initiate_payment.call_count == 1
```

#### Approval timeout
```python
async def test_approval_timeout_marks_proposal_as_timeout():
    # Insert PENDING proposal with proposed_at 25 hours ago
    # Run timeout checker (simulate time passage)
    process_expired_approvals(timeout_hours=24)
    log_entry = audit_log.get(proposal_id)
    assert log_entry.human_decision == "TIMEOUT"

async def test_approval_timeout_sends_notification(mock_webhook):
    process_expired_approvals(timeout_hours=24)
    mock_webhook.assert_called_once()
    payload = mock_webhook.call_args[0][0]
    assert payload["event"] == "APPROVAL_TIMEOUT"
```

#### Integration — full failure scenario (requires mocks)
```python
async def test_full_cycle_erp_timeout_produces_stale_flagged_proposal():
    # Mock ERP to fail, cache pre-populated, forecaster high confidence
    ctx = await graph.invoke(initial_context)
    assert ctx.treasury_state.has_stale_data
    if ctx.proposed_action:
        assert "STALE_DATA_PRESENT" in ctx.proposed_action.flagged_ambiguities

async def test_full_cycle_low_confidence_reaches_disambiguate():
    # Mock forecaster to return score=0.3
    ctx = await graph.invoke(initial_context)
    assert ctx.disambiguation_path is not None  # graph passed through disambiguate
```

### Running Tests

```bash
cd agent
# Unit tests only (no external services):
pytest tests/test_resilience.py -v

# All agent tests:
pytest tests/ -v
```
