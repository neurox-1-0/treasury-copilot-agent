# Component 4: SciPy Surplus Allocation Optimizer

**Status: To build — start with greedy fallback, upgrade to LP solver.**

---

## Purpose

An **internal tool the agent calls**. Given available surplus cash and a set of
short-term instrument options (from the bank mock's `/rates/deposits`), determine
the allocation that maximises yield subject to hard constraints — minimum liquidity
buffer and maximum acceptable lock-up given upcoming fixed obligations.

The optimizer's most important output is **not the recommended allocation alone**:
it is the `alternativesConsidered` list with `rejectedReason` for each. This is
what lets the agent's Decide node produce a meaningful rationale rather than just
a number.

---

## Build Strategy: Greedy Fallback First, LP Second

Same philosophy as the forecaster. Build `greedy_fallback.py` first — it runs
deterministically, is easy to test, and satisfies the same interface contract. The
`scipy.optimize.linprog` solver slots in as a drop-in upgrade.

---

## Canonical Location

```
services/optimizer/
├── main.py
├── solver.py              # LP solver using scipy.optimize.linprog
├── greedy_fallback.py     # Greedy allocation (fallback, no scipy needed)
├── tests/
│   └── test_optimizer.py
└── requirements.txt
```

**`requirements.txt`**:
```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
pydantic>=2.7.0
scipy>=1.13.0
numpy>=1.26.0
httpx>=0.27.0
pytest>=8.2.0
pytest-anyio>=0.0.0
```

---

## How to Run

```bash
cd services/optimizer
pip install -r requirements.txt
uvicorn main.py --reload --port 8004
```

---

## Input Contract

```
POST /optimize
Authorization: Bearer <internal-service-token>
Content-Type: application/json
```

```json
{
  "availableSurplus": "8000000.00",
  "minimumBufferRequired": "20000000.00",
  "currentTotalBalance": "28000000.00",
  "asOfDate": "2026-07-13",
  "nextFixedObligationDate": "2026-07-28",
  "nextFixedObligationAmount": "4200000.00",
  "costOfDebt": 0.1350,
  "instruments": [
    { "bank": "SAMPATH", "type": "CALL_DEPOSIT", "termDays": 1,   "rate": 0.085 },
    { "bank": "SAMPATH", "type": "FIXED_DEPOSIT", "termDays": 14, "rate": 0.100 },
    { "bank": "HNB",     "type": "FIXED_DEPOSIT", "termDays": 14, "rate": 0.102 },
    { "bank": "COMBANK", "type": "FIXED_DEPOSIT", "termDays": 14, "rate": 0.100 },
    { "bank": "SAMPATH", "type": "FIXED_DEPOSIT", "termDays": 30, "rate": 0.110 },
    { "bank": "HNB",     "type": "FIXED_DEPOSIT", "termDays": 30, "rate": 0.1125 },
    { "bank": "SAMPATH", "type": "FIXED_DEPOSIT", "termDays": 90, "rate": 0.120 },
    { "bank": "HNB",     "type": "FIXED_DEPOSIT", "termDays": 90, "rate": 0.121 }
  ]
}
```

### Field Definitions

| Field | Description |
|---|---|
| `availableSurplus` | Cash above the buffer minimum — the pool to deploy |
| `minimumBufferRequired` | The treasury goal's hard floor; never breach this |
| `currentTotalBalance` | Total available balance across all accounts |
| `nextFixedObligationDate` | Earliest date a FIXED-priority payment is due |
| `nextFixedObligationAmount` | Amount required for that obligation |
| `costOfDebt` | Effective rate on the company's floating OD facility (`AWPLR + spread`), sourced from Component 9 + mock bank loan endpoint. This is the **hurdle rate** — any instrument yielding less than this should be flagged as sub-optimal vs. paying down the OD. `null` if no floating rate facility exists. |
| `instruments` | Cross-bank instrument list assembled by the agent's Reason node from Component 9 market data (`bestAvailableRates`) merged with mock bank `/rates/deposits`. Each entry must include `bank`. |

---

## Output Contract (What the Agent Consumes)

```json
{
  "recommendedAllocation": [
    {
      "bank": "HNB",
      "instrument": "FIXED_DEPOSIT",
      "termDays": 14,
      "amount": "8000000.00",
      "maturityDate": "2026-07-27",
      "expectedYield": "31232.88",
      "yieldRate": 0.102
    }
  ],
  "alternativesConsidered": [
    {
      "bank": "SAMPATH",
      "instrument": "FIXED_DEPOSIT",
      "termDays": 14,
      "amount": "8000000.00",
      "maturityDate": "2026-07-27",
      "expectedYield": "30684.93",
      "yieldRate": 0.10,
      "rejectedReason": "HNB offers 10.2% vs Sampath's 10.0% for the same 14-day term. HNB recommended."
    },
    {
      "bank": "HNB",
      "instrument": "FIXED_DEPOSIT",
      "termDays": 30,
      "amount": "8000000.00",
      "maturityDate": "2026-08-12",
      "expectedYield": "73972.60",
      "yieldRate": 0.1125,
      "rejectedReason": "Maturity (2026-08-12) falls after next fixed obligation date (2026-07-28). Deploying full surplus into this instrument would leave insufficient liquid funds to cover the LKR 4,200,000 payroll obligation on 2026-07-28."
    },
    {
      "bank": "SAMPATH",
      "instrument": "FIXED_DEPOSIT",
      "termDays": 90,
      "amount": "8000000.00",
      "maturityDate": "2026-10-11",
      "expectedYield": "236712.33",
      "yieldRate": 0.12,
      "rejectedReason": "Same maturity-date constraint as 30-day FD. Additionally, 90-day lock-up creates unacceptable illiquidity across 3 future payroll and tax cycles."
    },
    {
      "bank": "SAMPATH",
      "instrument": "CALL_DEPOSIT",
      "termDays": 1,
      "amount": "8000000.00",
      "maturityDate": "2026-07-14",
      "expectedYield": "1863.01",
      "yieldRate": 0.085,
      "rejectedReason": "Feasible but sub-optimal. Call deposit at 8.5% yields significantly less than 14-day FD at 10.2% while providing no meaningfully greater liquidity."
    }
  ],
  "constraintsSatisfied": true,
  "infeasibilityReason": null,
  "costOfDebtHurdleBreached": true,
  "hurdleNote": "All available instruments yield below the effective OD rate of 13.5%. Recommend considering partial OD repayment instead of FD placement. Awaiting human approval.",
  "solverUsed": "SCIPY_LINPROG",
  "bufferAfterDeployment": "20000000.00"
}
```

> **`costOfDebtHurdleBreached`**: Set to `true` when the recommended instrument's
> `yieldRate` is below `costOfDebt`. This signals to the agent's Decide node to
> include an OD-repayment alternative in `alternativesConsidered` alongside the
> FD recommendation, with the rationale that repaying the OD at 13.5% is equivalent
> to earning 13.5% risk-free. The human approver sees both options.

---

## Optimization Problem Specification

### Feasibility Pre-check

Before running the LP, check:
1. `availableSurplus <= 0`: return `constraintsSatisfied=false`,
   `infeasibilityReason="Current balance already at or below minimum buffer. No surplus available to deploy."`,
   `recommendedAllocation=[]`.
2. `availableSurplus < 100000` (below a minimum deployment threshold):
   return `constraintsSatisfied=false`,
   `infeasibilityReason="Surplus too small for any instrument (minimum LKR 100,000)."`.

### Instrument Pre-filtering (Before Sending to LP)

This step resolves the constraint "no allocation may lock up funds needed before
`nextFixedObligationDate`":

```python
from datetime import date, timedelta

def maturity_date(as_of_date: date, term_days: int) -> date:
    return as_of_date + timedelta(days=term_days)

def is_safe(instrument, as_of_date, obligation_date, obligation_amount, surplus):
    """
    An instrument is SAFE if:
      1. It matures before or on the obligation date, OR
      2. Its allocation leaves enough undeployed liquid cash to cover the obligation.
    """
    mat = maturity_date(as_of_date, instrument.termDays)
    if mat <= obligation_date:
        return True, None
    # Instrument matures after obligation — would it still leave enough liquid cash?
    liquid_remaining = surplus - instrument_allocation  # needs to be >= obligation_amount
    # For simplicity in the LP pre-filter: if it matures after the date, reject it.
    return False, f"Maturity ({mat}) falls after next fixed obligation date ({obligation_date})."
```

Instruments failing the safety check are moved to `alternativesConsidered` with
their `rejectedReason`. The LP only runs over safe instruments.

### LP Formulation (Safe Instruments Only)

```
Variables: x_i = amount allocated to instrument i   (i in safe_instruments)

Maximize:  Σ (x_i * rate_i * term_i / 365)          [expected yield]

Subject to:
  Σ x_i  ≤  availableSurplus                         [can't deploy more than surplus]
  x_i    ≥  0  for all i                             [no short positions]
```

This is a **linear programme** — use `scipy.optimize.linprog`. Since `linprog`
minimises, negate the objective: minimise `- Σ (x_i * rate_i * term_i / 365)`.

### Greedy Fallback (when scipy unavailable)

Sort safe instruments by descending yield rate. Allocate full surplus to the
highest-yielding safe instrument. This is always feasible (single instrument)
and is easy to explain. Mark `solverUsed="GREEDY_FALLBACK"`.

### Generating `alternativesConsidered`

**All instruments** (not just rejected ones) should appear in
`alternativesConsidered` to show the full decision space:
- **Unsafe instruments** (maturity after obligation): include with maturity-date
  `rejectedReason`.
- **Safe but sub-optimal instruments**: include with yield-comparison
  `rejectedReason` (e.g. "Feasible but yields X% less than recommended option").
- **The recommended instrument**: appears in `recommendedAllocation` only;
  omit from `alternativesConsidered`.

Build at minimum **top 3 candidates with reasons** — this is the data the agent
uses to explain its decision.

---

## Edge Cases to Handle

| Case | Expected Response |
|---|---|
| All instruments unsafe (all mature after obligation) | `constraintsSatisfied=false`, `infeasibilityReason="No instrument matures before next fixed obligation date. Consider using call deposit only."` |
| Only call deposit is safe | Allocate to call deposit, note in rationale |
| `nextFixedObligationDate` is null / no obligations | No maturity constraint — choose highest-yield instrument |
| `instruments` list is empty | `400 {"error": "NO_INSTRUMENTS_PROVIDED"}` |
| LP infeasible (scipy returns failure status) | Fall back to greedy; log LP failure in response |
| Best yield < `costOfDebt` | Set `costOfDebtHurdleBreached=true`, add OD-repayment to `alternativesConsidered` with explanation |
| `costOfDebt` is null | Skip hurdle check; set `costOfDebtHurdleBreached=false` |

---

## Testing Requirements

### Test file: `services/optimizer/tests/test_optimizer.py`

#### 1. Output contract compliance
```python
async def test_optimize_returns_correct_schema():
    # POST /optimize with valid body
    # Assert response has: recommendedAllocation, alternativesConsidered,
    #   constraintsSatisfied, solverUsed, bufferAfterDeployment,
    #   costOfDebtHurdleBreached

async def test_recommended_allocation_has_bank_field():
    # Assert each item in recommendedAllocation has "bank" field

async def test_recommended_allocation_has_maturity_date():
    # Assert each item in recommendedAllocation has maturityDate

async def test_alternatives_have_rejected_reason():
    # Assert all items in alternativesConsidered have non-empty rejectedReason
```

#### 2. Constraint satisfaction — the core logic
```python
async def test_recommended_instrument_matures_before_obligation():
    # Use input where obligation date is 2026-07-28
    # Assert recommended allocation maturityDate <= 2026-07-28

async def test_long_term_instrument_is_in_alternatives_not_recommendation():
    # Use input where 90-day FD matures after obligation
    # Assert 90-day FD is in alternativesConsidered, NOT in recommendedAllocation

async def test_buffer_is_preserved():
    # Assert bufferAfterDeployment >= minimumBufferRequired

async def test_allocation_does_not_exceed_surplus():
    # Assert sum(recommendedAllocation[i]["amount"]) <= availableSurplus
```

#### 3. Infeasibility cases
```python
async def test_no_surplus_returns_infeasible():
    # POST with availableSurplus == 0
    # Assert constraintsSatisfied == false
    # Assert infeasibilityReason is non-empty string
    # Assert recommendedAllocation == []

async def test_all_instruments_unsafe_returns_infeasible():
    # POST with nextFixedObligationDate = tomorrow
    # with only 30-day and 90-day instruments
    # Assert constraintsSatisfied == false

async def test_no_instruments_returns_400():
    # POST with instruments == []
    # Assert 400 with NO_INSTRUMENTS_PROVIDED
```

#### 4. Yield optimality
```python
async def test_higher_yield_safe_instrument_is_chosen_over_lower():
    # Provide two safe instruments: A at 10%, B at 8%
    # Assert recommendedAllocation contains A

async def test_unsafe_high_yield_not_chosen_despite_better_yield():
    # Provide: safe instrument at 8%, unsafe at 12%
    # Assert recommendedAllocation contains 8% instrument
    # Assert 12% instrument in alternativesConsidered with maturity rejection reason
```

#### 5. Greedy fallback
```python
async def test_greedy_fallback_produces_valid_output():
    # Inject optimizer without scipy (or patch solver to raise ImportError)
    # Assert response still has correct schema
    # Assert solverUsed == "GREEDY_FALLBACK"

async def test_greedy_fallback_respects_maturity_constraint():
    # Even with greedy fallback, unsafe instruments must be rejected

async def test_greedy_fallback_respects_bank_field():
    # Assert recommended instrument has bank field even in greedy mode
```

#### 6. No obligation date (unconstrained)
```python
async def test_no_obligation_date_picks_highest_yield():
    # POST with nextFixedObligationDate == null
    # Assert recommended instrument has highest rate in instruments list
```

#### 7. Cross-bank selection
```python
async def test_cross_bank_higher_yield_selected():
    # Provide: SAMPATH 14d at 10.0%, HNB 14d at 10.2% (both safe)
    # Assert recommendedAllocation[0].bank == "HNB"
    # Assert recommendedAllocation[0].yieldRate == 0.102

async def test_same_rate_different_banks_selects_first():
    # Provide: SAMPATH 14d at 10.0%, COMBANK 14d at 10.0%
    # Assert either bank is selected (deterministic tie-break: first in list)
```

#### 8. Cost of debt hurdle
```python
async def test_hurdle_breached_when_best_yield_below_cost_of_debt():
    # POST with costOfDebt=0.135, instruments all yielding < 0.135
    # Assert costOfDebtHurdleBreached == true
    # Assert hurdleNote is non-empty string

async def test_hurdle_not_breached_when_instrument_beats_cost_of_debt():
    # POST with costOfDebt=0.09, best instrument at 0.12
    # Assert costOfDebtHurdleBreached == false

async def test_null_cost_of_debt_skips_hurdle_check():
    # POST with costOfDebt == null
    # Assert costOfDebtHurdleBreached == false
    # Assert hurdleNote == null
```

### Running Tests

```bash
cd services/optimizer
pytest tests/ -v
```
