# Testing Master Reference

> **What this is**: The complete test map for the entire project — aggregated from
> all component docs. Use this as the checklist to confirm each component is done
> and integrated before moving to the next.

---

## Test Philosophy

| Principle | Application |
|---|---|
| **Stub-first** | Build stub implementations before real models; test the stub so the contract is verified before the upgrade |
| **Mock external calls at the unit level** | Every node test mocks its HTTP clients — no real services needed |
| **Real services for integration tests** | `test_full_loop.py` requires all services running |
| **Chaos is scripted, not random** | Failure scenarios are deterministic and demo-ready |
| **Every terminal state has a test** | Happy path, stale data, infeasibility, write failure, timeout — all covered |

---

## Test Stack

| Tool | Purpose |
|---|---|
| `pytest` | All Python tests |
| `pytest-anyio` | Async test support |
| `httpx.AsyncClient` | ASGI test client (no server needed for unit tests) |
| `unittest.mock.patch` | Mocking HTTP clients and external calls |
| `Vitest` | React component tests |
| `@testing-library/react` | DOM interaction in component tests |

---

## Component Test Checklist

### Component 1: Mock SAP ERP

File: `services/erp-mock/tests/test_erp_mock.py`

- [ ] Health check returns all 6 entity counts > 0
- [ ] Cash positions return OData envelope shape
- [ ] `$filter` by `PaymentPriority eq 'FIXED'` returns only FIXED items
- [ ] `$filter` by `PaymentPriority eq 'FLEXIBLE'` returns only FLEXIBLE items
- [ ] `$filter` by `ClearingStatus eq 'OPEN'` returns only open AP documents
- [ ] `$top=2&$skip=0` returns 2 items; `$skip=2` returns different 2 items
- [ ] `__next` cursor present when more pages exist; absent on last page
- [ ] `$select=BankAccount,AvailableBalance` returns only those fields
- [ ] CSRF `Fetch` handshake returns token in response header
- [ ] Payroll postings: all `PaymentPriority == "FIXED"`
- [ ] Tax items: all `PaymentPriority == "FIXED"`
- [ ] Metadata endpoint returns all 6 service names
- [ ] Invalid filter param: 200 with empty results (not 500)

**Done when**: All 13 assertions green. `pytest services/erp-mock/tests/ -v`

---

### Component 2: Mock Bank API

File: `services/bank-mock/tests/test_bank_mock.py`

- [ ] Token issuance: valid credentials → 200 with `access_token`
- [ ] Token issuance: invalid credentials → 401
- [ ] Protected endpoint without token → 401
- [ ] Expired token → 401 with `TOKEN_EXPIRED` body
- [ ] Balance endpoint: correct shape with all required fields
- [ ] Balance for unknown account → 404
- [ ] Payment initiation (happy path): returns `PENDING_APPROVAL` status
- [ ] Payment initiation: balance is debited by payment amount
- [ ] Payment initiation: invalid signature → 401 `INVALID_SIGNATURE`
- [ ] Payment initiation: amount > balance → 422 `INSUFFICIENT_FUNDS`
- [ ] Payment initiation: invalid beneficiary → 422 `INVALID_BENEFICIARY`
- [ ] Payment status polling: eventually reaches `EXECUTED`
- [ ] Payment status: unknown ID → 404
- [ ] Deposit rates: all instrument types present with rate > 0
- [ ] `?simulate=timeout` delays response > 3s
- [ ] `?simulate=write_failure` returns 500, balance NOT changed
- [ ] Accounts list: returns seeded accounts with required fields

**Done when**: All 17 assertions green. `pytest services/bank-mock/tests/ -v`

---

### Component 3: Forecaster

File: `services/forecaster/tests/test_forecaster.py`

- [ ] Stub: correct schema (all required fields present)
- [ ] Stub: `len(forecast)` matches `horizonDays`
- [ ] Stub: forecast dates are sequential (no gaps, no duplicates)
- [ ] Stub: `dayConfidenceScore` decreases monotonically with horizon day
- [ ] Stub: `LOW_CONFIDENCE_BEYOND_DAY_10` flag triggered for 14-day horizon
- [ ] Stub: `overallConfidenceScore` in [0.0, 1.0]
- [ ] Stub: all `confidenceLow <= predictedNetCashFlow <= confidenceHigh`
- [ ] Stub: `modelType == "STUB_TRAILING_AVERAGE"` when no LSTM weights
- [ ] Invalid company code → 404
- [ ] `horizonDays > 90` → 400
- [ ] `horizonDays = 0` → 400
- [ ] LSTM (if weights present): schema identical to stub, `modelType == "LSTM_MC_DROPOUT"`
- [ ] LSTM (if weights present): `std > 0` for all days (MC Dropout active)
- [ ] Low-confidence output: correct flag for agent routing

**Done when**: Stub tests all green. LSTM tests skipped if weights absent. `pytest services/forecaster/tests/ -v -k "not lstm"`

---

### Component 4: Optimizer

File: `services/optimizer/tests/test_optimizer.py`

- [ ] Correct output schema (all required fields)
- [ ] Recommended allocation has `maturityDate` field
- [ ] All alternatives in `alternativesConsidered` have non-empty `rejectedReason`
- [ ] Recommended instrument matures on or before `nextFixedObligationDate`
- [ ] Long-maturity instrument appears in alternatives, not recommendation
- [ ] `bufferAfterDeployment >= minimumBufferRequired`
- [ ] Sum of recommended allocation ≤ `availableSurplus`
- [ ] `availableSurplus = 0` → `constraintsSatisfied=false`, empty recommendation
- [ ] All instruments unsafe → `constraintsSatisfied=false`
- [ ] Empty instruments list → 400 `NO_INSTRUMENTS_PROVIDED`
- [ ] Higher-yield safe instrument chosen over lower-yield safe instrument
- [ ] Unsafe high-yield instrument NOT chosen despite better yield
- [ ] Greedy fallback produces valid output with same schema
- [ ] Greedy fallback respects maturity constraint
- [ ] No `nextFixedObligationDate` → highest-yield instrument chosen

**Done when**: All 15 assertions green. `pytest services/optimizer/tests/ -v`

---

### Component 5: Agent Core

Files: `agent/tests/test_nodes/` + `agent/tests/test_feedback_loop.py` + `agent/tests/test_graph.py`

#### Perceive Node
- [ ] Builds `TreasuryState` from mocked ERP + bank data
- [ ] `total_available_balance > 0`
- [ ] `fixed_obligations` all have `payment_priority == "FIXED"`
- [ ] Marks `DataFreshness.STALE` when ERP call fails (with cache available)
- [ ] Sets `execution_blocked=True` when cash position `MISSING` (no cache)
- [ ] Detects unreconciled large credits from bank statement

#### Confidence Check Node
- [ ] Routes to `DISAMBIGUATE` on `overallConfidenceScore < 0.6`
- [ ] Routes to `DECIDE` on `overallConfidenceScore >= 0.8`, no stale data
- [ ] Routes to `DISAMBIGUATE` on stale data with high-materiality surplus
- [ ] Routes to `DISAMBIGUATE` on unreconciled large credit
- [ ] Sets `STALE_DATA_PRESENT` flag when stale data present
- [ ] Sets `LOW_FORECAST_CONFIDENCE` flag when confidence low

#### Disambiguate Node
- [ ] High stakes (large amount + affects FIXED) → `ESCALATE`
- [ ] Low stakes (small amount, no FIXED impact) → `PROCEED_FLAGGED`
- [ ] Rationale string is generated (non-empty) for both paths

#### Decide Node
- [ ] Skips generation when duplicate `content_hash` already `PENDING`
- [ ] `constraintsSatisfied=false` → `action_type="NO_ACTION"`
- [ ] `execution_blocked=True` → `action_type="NO_ACTION"`
- [ ] Buffer breach → `action_type="CONSTRAINT_VIOLATION"` (not silent adjustment)
- [ ] Valid proposal has all required `ProposedAction` fields including `content_hash`
- [ ] Writes `PENDING` record to audit log on proposal generation

#### Feedback Loop
- [ ] Two long-term rejections in last 5 → `max_term_days=30`
- [ ] All approvals → no constraint added (`max_term_days=90`)

#### Full Graph
- [ ] Happy path: graph produces `SURPLUS_ALLOCATION` proposal
- [ ] Low confidence: graph passes through disambiguate node
- [ ] Execution blocked: graph produces `NO_ACTION` proposal
- [ ] `decision_log` has exactly one `PENDING` entry after graph run

**Done when**: All unit tests green (mocked services). `pytest agent/tests/test_nodes/ agent/tests/test_feedback_loop.py -v`

---

### Component 6: HITL Dashboard

#### Backend (`services/hitl-api/tests/test_hitl_api.py`)
- [ ] `GET /proposals?status=PENDING` returns correct shape
- [ ] `GET /proposals?status=APPROVED` returns only approved proposals
- [ ] Empty DB → `{"proposals": []}`
- [ ] Approve decision → record updated, `human_decision="APPROVED"`
- [ ] Reject decision → record updated with `human_note`
- [ ] Modify with valid parameters → 200 with `constraintsSatisfied=true`
- [ ] Modify outside bounds → 400 `CONSTRAINT_VIOLATION` with correct bounds
- [ ] Decision on unknown proposal → 404
- [ ] Audit log returns all completed proposals
- [ ] Audit log date filter works correctly
- [ ] Audit log export → `text/csv` with header row
- [ ] Feedback insights counts correct
- [ ] Feedback insights detects rejection pattern
- [ ] Chaos endpoint → 200

#### Frontend (`dashboard/src/__tests__/`)
- [ ] `ProposalCard`: green confidence for score ≥ 0.8
- [ ] `ProposalCard`: amber confidence for score 0.6–0.79
- [ ] `ProposalCard`: flags render as visible warning badges
- [ ] `ModifyPanel`: slider constrained to `parameterBounds`
- [ ] Approve button: calls decision API with `{"decision": "APPROVED"}`

**Done when**: All backend tests green + all frontend tests green.

---

### Component 7: Resilience

File: `agent/tests/test_resilience.py`

- [ ] `cache_set` + `cache_get_stale` round-trip works
- [ ] `cache_get_stale` returns `(None, None)` for missing key
- [ ] ERP timeout → `DataFreshness.STALE` with cached data returned
- [ ] ERP timeout + no cache → `DataFreshness.MISSING`
- [ ] ERP call does not raise unhandled exception on any failure
- [ ] Payment write failure (timeout) → `PaymentWriteStatus.UNKNOWN`
- [ ] Payment 422 response → `PaymentWriteStatus.REJECTED`
- [ ] Payment POST called exactly once (no retry on write)
- [ ] Report node: `UNKNOWN` status written to audit log on write failure
- [ ] Approval timeout: proposal marked `TIMEOUT` in audit log
- [ ] Approval timeout: notification webhook called once

**Done when**: All 11 assertions green. `pytest agent/tests/test_resilience.py -v`

---

## Integration Test: Full Loop

File: `tests/integration/test_full_loop.py`

**Requires all services running:**
```bash
# Start all services (use docker-compose or manual):
uvicorn main:app --port 8001 &  # erp-mock
uvicorn main:app --port 8002 &  # bank-mock
uvicorn main:app --port 8003 &  # forecaster
uvicorn main:app --port 8004 &  # optimizer
uvicorn main:app --port 8005 &  # hitl-api
```

```bash
pytest tests/integration/ -v --integration
```

### Integration Scenarios

#### Happy Path
- [ ] ERP and bank services return fresh data
- [ ] Forecaster returns confidence ≥ 0.8
- [ ] Optimizer returns feasible allocation
- [ ] Agent produces `SURPLUS_ALLOCATION` proposal
- [ ] HITL API shows the proposal as `PENDING`
- [ ] Approve via HITL API
- [ ] Bank mock processes payment → `EXECUTED`
- [ ] Audit log entry shows `EXECUTED`

#### Stale Data Path
- [ ] Trigger ERP timeout via chaos endpoint
- [ ] Agent uses cached data, proposal has `STALE_DATA_PRESENT` flag
- [ ] HITL dashboard shows the flag prominently

#### Low Confidence Path
- [ ] Trigger low confidence via chaos endpoint
- [ ] Agent reaches disambiguate node
- [ ] Proposal has `flaggedAmbiguities` populated

#### Infeasibility Path
- [ ] Trigger infeasible optimizer via chaos endpoint
- [ ] Agent produces `NO_ACTION` proposal
- [ ] HITL dashboard shows "No action taken" state

#### Write Failure Path
- [ ] Approve a proposal
- [ ] Trigger bank write failure via chaos endpoint
- [ ] Audit log shows `payment_status="UNKNOWN"`
- [ ] HITL dashboard shows "Manual verification required"

---

## Running All Tests at Once

```bash
# From project root — run all unit tests (no services required):
pytest services/ agent/tests/ -v --ignore=tests/integration

# Run frontend tests:
cd dashboard && npm run test

# Run everything including integration (requires all services):
pytest --integration -v
```

Add `--integration` flag via conftest.py:
```python
# conftest.py (project root)
def pytest_addoption(parser):
    parser.addoption("--integration", action="store_true", default=False)

def pytest_collection_modifyitems(config, items):
    if not config.getoption("--integration"):
        skip = pytest.mark.skip(reason="Integration tests require --integration flag")
        for item in items:
            if "integration" in str(item.fspath):
                item.add_marker(skip)
```

---

## Build Confidence Criteria

A component is considered **done** when:
1. Its test file exists and all tests pass.
2. Its service starts and the health/metadata endpoint responds.
3. The agent can call it and receive the expected output contract.
4. At least one failure scenario works correctly (stale/infeasible/rejected).

The full system is **demo-ready** when:
1. All 5 integration scenarios above pass (not just the happy path).
2. The Chaos Panel can trigger each failure mode in real-time during a demo.
3. The Feedback Insight Panel shows at least one non-trivial adaptation
   (requires 2+ decision cycles to have run).
