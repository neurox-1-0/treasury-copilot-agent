# Component 3: LSTM Forecasting Tool

**Status: To build — start with stub, upgrade to LSTM.**

---

## Purpose

An **internal tool the agent calls**. Given historical daily cash flow data,
produce a short-term forecast (7–30 days) of net cash position with a
confidence measure the agent's Confidence & Conflict Check node can act on.

The purpose is not forecasting accuracy in isolation — it is giving the agent
a calibrated signal about *when to trust* the forecast before deciding whether
to deploy capital.

---

## Build Strategy: Stub First, LSTM Second

The LSTM carries training cost and model management overhead. Building a fully
functional agent loop should not be blocked by it. Follow this two-phase approach:

### Phase 1 — Stub (unblock the agent loop immediately)

Build `services/forecaster/model/stub.py`: a rule-based forecaster satisfying the
exact same output contract as the LSTM. It computes:
- **Point estimate**: trailing N-day average net cash flow per day.
- **Confidence interval**: ± 15% of the trailing standard deviation.
- **Overall confidence score**: degrades with forecast horizon (longer horizon →
  lower confidence). Formula: `max(0.4, 1.0 - (horizon_day / max_horizon) * 0.5)`.
- **Flags**: `LOW_CONFIDENCE_BEYOND_DAY_10` if any day > 10 has confidence < 0.6.

This stub is immediately testable, immediately usable by the agent, and the LSTM
slots in as a drop-in replacement without changing any consuming code.

### Phase 2 — LSTM (if time permits)

Replace the stub with a trained LSTM model. The interface contract is identical.

---

## Canonical Location

```
services/forecaster/
├── main.py                        # FastAPI wrapper
├── model/
│   ├── stub.py                    # Phase 1: rule-based stub
│   ├── lstm.py                    # Phase 2: LSTM implementation
│   └── weights/                   # Saved Keras model weights (.h5 or SavedModel)
│       └── .gitkeep
├── data/
│   └── series_generator.py        # Generates / loads historical time series
├── tests/
│   └── test_forecaster.py
└── requirements.txt
```

**`requirements.txt`** (stub only):
```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
pydantic>=2.7.0
numpy>=1.26.0
httpx>=0.27.0
pandas>=2.2.0                  # for feature assembly
pytest>=8.2.0
pytest-anyio>=0.0.0
```

Add for LSTM phase:
```
tensorflow>=2.16.0
# OR: torch>=2.3.0 torchvision torchaudio
scikit-learn>=1.4.0   # for MinMaxScaler
```

---

## How to Run

```bash
cd services/forecaster
pip install -r requirements.txt
uvicorn main:app --reload --port 8003
```

The service auto-selects stub vs LSTM based on whether saved weights exist:
```python
# main.py startup logic
if Path("model/weights/lstm_model.h5").exists():
    forecaster = LSTMForecaster(weights_path="model/weights/lstm_model.h5")
else:
    forecaster = StubForecaster()   # no warning needed — this is a valid mode
```

---

## Input Contract

```
POST /forecast
Authorization: Bearer <internal-service-token>
Content-Type: application/json

{
  "companyCode": "1000",
  "horizonDays": 14
}
```

The service internally calls:
1. The ERP mock's Perceive endpoints to assemble the historical cash flow series.
2. The Market Data Service (Component 9) `GET /rates` to get the current CBSL and
   bank rate snapshot, which is appended as exogenous features to the input vector.

(Or uses pre-loaded data if either service is unavailable — see Failure Modes.)
The caller (agent's Reason node) does not need to supply raw time series data —
it just supplies the company code and horizon.

---

## Output Contract (What the Agent Consumes)

```json
{
  "companyCode": "1000",
  "forecastHorizonDays": 14,
  "generatedAt": "2026-07-13T09:30:00+05:30",
  "modelType": "LSTM_MC_DROPOUT",
  "forecast": [
    {
      "date": "2026-07-14",
      "predictedNetCashFlow": "1250000.00",
      "confidenceLow": "900000.00",
      "confidenceHigh": "1600000.00",
      "dayConfidenceScore": 0.91
    },
    {
      "date": "2026-07-15",
      "predictedNetCashFlow": "1100000.00",
      "confidenceLow": "750000.00",
      "confidenceHigh": "1450000.00",
      "dayConfidenceScore": 0.88
    }
  ],
  "overallConfidenceScore": 0.82,
  "flags": ["LOW_CONFIDENCE_BEYOND_DAY_10"],
  "fallbackUsed": false,
  "fallbackReason": null
}
```

### Field Definitions

| Field | Type | Description |
|---|---|---|
| `modelType` | string | `"LSTM_MC_DROPOUT"`, `"STUB_TRAILING_AVERAGE"`, or `"HEURISTIC_FALLBACK"` |
| `dayConfidenceScore` | float 0–1 | Per-day confidence; decreases with horizon |
| `overallConfidenceScore` | float 0–1 | Scalar the agent checks against threshold (default 0.6) |
| `flags` | string[] | Named conditions: `LOW_CONFIDENCE_BEYOND_DAY_10`, `STALE_INPUT_DATA`, `FALLBACK_ACTIVE` |
| `fallbackUsed` | bool | True if stub/heuristic used instead of LSTM |
| `fallbackReason` | string\|null | E.g. `"LSTM weights not found"` or `"ERP data unavailable, using cached series"` |

### Confidence Score Semantics

`overallConfidenceScore` is the mean of `dayConfidenceScore` across all forecast
days. It is interpreted by the agent as:

| Score | Agent behaviour |
|---|---|
| ≥ 0.80 | High confidence — proceed to Decide node |
| 0.60 – 0.79 | Medium confidence — proceed but flag in proposal |
| < 0.60 | Low confidence — route to Disambiguate node |

For the LSTM: `dayConfidenceScore = 1.0 - clip(std_of_mc_samples / abs(mean_of_mc_samples), 0, 1)`.
For the stub: use the formula described in Phase 1 above.

---

## LSTM Model Specification (Phase 2)

### LSTM Architecture

```
Input: sequence of 60–90 days of feature vectors (see Feature Vector below)
  │
  ▼
LSTM layer 1: 64 units, return_sequences=True, Dropout(0.2)
  │
  ▼
LSTM layer 2: 32 units, Dropout(0.2)
  │
  ▼
Dense(horizonDays)   ← outputs N values, one per forecast day
```

### Feature Vector (per day)

Each time step in the LSTM input is a **5-dimensional feature vector**, not
just the scalar cash flow. This gives the model regime-awareness it cannot
derive from cash flow history alone.

| # | Feature | Source | Rationale |
|---|---|---|---|
| 1 | `net_cash_flow` | ERP mock historical series | The primary signal to forecast |
| 2 | `awplr` | Component 9 CBSL cache | Floating loan cost benchmark; high AWPLR periods correlate with tighter liquidity |
| 3 | `repo_rate` | Component 9 CBSL cache | Risk-free rate proxy; signals monetary policy regime |
| 4 | `best_fd_rate_90d` | Component 9 `bestAvailableRates` | Best available yield; management tends to lock longer when rates are high |
| 5 | `usd_lkr_mid` | Mock bank `/rates/forex` | FX exposure; sharp LKR depreciation increases import-side outflows |

For the **stub forecaster**, features 2–5 are ignored (the stub uses the trailing
average of `net_cash_flow` only). For the LSTM, all 5 features are normalized
together using `MinMaxScaler` fitted on the training window.

```python
# data/series_generator.py
def generate_feature_matrix(
    cash_flow_series: list[float],
    awplr_series: list[float],
    repo_rate_series: list[float],
    best_fd_90d_series: list[float],
    usd_lkr_series: list[float],
) -> np.ndarray:
    """Shape: (n_days, 5). All series must be same length."""
    return np.column_stack([
        cash_flow_series,
        awplr_series,
        repo_rate_series,
        best_fd_90d_series,
        usd_lkr_series,
    ])
```

For historical data used in training, CBSL rates and FX rates are read from the
cached `cache/rates_cache.json` in Component 9. For periods where only a single
value is available (CBSL data updates weekly), the most recent value is **forward-filled**
across days in that week.

### Monte Carlo Dropout for Confidence

Run the same input through the model `N=50` times with dropout active at
inference time (set `training=True` in Keras, or use a custom inference function).
The 50 outputs form a distribution per day:

```python
predictions = np.array([model(X, training=True) for _ in range(50)])
mean_pred = predictions.mean(axis=0)
std_pred = predictions.std(axis=0)
confidence_low = mean_pred - 1.96 * std_pred
confidence_high = mean_pred + 1.96 * std_pred
```

### Training Data

The historical time series is generated by extending `services/erp-mock/data/seed.py`
with a `generate_historical_series(lookback_days=90)` function (see Component 1 doc).
The series must be:
- **Deterministic**: `random.seed(42)` ensures reproducibility.
- **Seasonal**: slight weekly pattern (lower inflows Friday, zero outflows on
  weekends) to give the LSTM a learnable signal.
- **At least 90 days**: 60-day input window + 30-day headroom.

For exogenous features (AWPLR, Repo Rate, best FD rate, USD/LKR), the training
data generator synthesises plausible historical rate series:
- AWPLR: starts at 12.5%, trends down to 12.0% over 90 days (mirrors a
  gradual easing scenario).
- Repo Rate: starts at 9.0%, steps down to 8.5% at day 45 (policy change event).
- Best FD 90d: follows Repo Rate with a lag (correlation = 0.8, lag = 7 days).
- USD/LKR mid: starts at 305, random walk with σ = 1.5/day.

These are seeded synthetic values — they are sufficient to teach the LSTM the
correlation structure. The model will refine against real data when connected
to Component 9 in production.

Pre-trained weights should be saved to `model/weights/lstm_model.h5` and committed
to the repo so the service starts without re-training. Training script:
`python -m model.train` (add a `model/train.py` script for this).

---

## Failure Modes

| Failure | Behaviour |
|---|---|
| ERP data unavailable for series assembly | Use a cached / pre-seeded local CSV; set `fallbackUsed=true`, add flag `STALE_INPUT_DATA` |
| Market Data Service unavailable (Component 9) | Use last-known exogenous feature values from `data/feature_cache.json`; add flag `STALE_EXOGENOUS_FEATURES`. If no cache exists, use hardcoded fallback values (AWPLR=12.0, Repo=8.5, FD90d=0.12, USD/LKR=305.0) and add flag `DEFAULT_EXOGENOUS_FEATURES`. |
| LSTM weights missing | Auto-downgrade to stub; set `modelType="STUB_TRAILING_AVERAGE"`, `fallbackUsed=true`. Stub ignores exogenous features. |
| Too few data points (< 30 days) | Return `400 {"error": "INSUFFICIENT_HISTORY", "daysAvailable": N}` |
| MC Dropout produces all-zero std (degenerate model) | Return `overallConfidenceScore=0.5`, flag `DEGENERATE_MODEL_OUTPUT` |

---

## Testing Requirements

### Test file: `services/forecaster/tests/test_forecaster.py`

#### 1. Stub — output contract compliance
```python
async def test_stub_returns_correct_schema():
    # POST /forecast {"companyCode": "1000", "horizonDays": 14}
    # Assert response has: companyCode, forecastHorizonDays, forecast (list),
    #   overallConfidenceScore, flags, fallbackUsed, modelType

async def test_stub_forecast_length_matches_horizon():
    # POST /forecast with horizonDays=7
    # Assert len(response["forecast"]) == 7
    # POST /forecast with horizonDays=14
    # Assert len(response["forecast"]) == 14

async def test_stub_dates_are_sequential():
    # Assert forecast[i+1]["date"] == forecast[i]["date"] + 1 day for all i

async def test_stub_confidence_decreases_with_horizon():
    # Assert forecast[0]["dayConfidenceScore"] >= forecast[13]["dayConfidenceScore"]

async def test_stub_confidence_low_flag_triggered():
    # POST /forecast with horizonDays=14
    # Assert "LOW_CONFIDENCE_BEYOND_DAY_10" in flags if any day > 10 has low score

async def test_stub_overall_confidence_in_range():
    # Assert 0.0 <= overallConfidenceScore <= 1.0

async def test_stub_confidence_interval_is_ordered():
    # Assert all: confidenceLow <= predictedNetCashFlow <= confidenceHigh
```

#### 2. Stub — fallback is declared
```python
async def test_stub_declares_model_type():
    # When running without LSTM weights, assert modelType == "STUB_TRAILING_AVERAGE"
    # Assert fallbackUsed == true if LSTM weights absent
```

#### 3. Input validation
```python
async def test_invalid_company_code_returns_404():
    # POST /forecast {"companyCode": "9999", "horizonDays": 14}
    # Assert 404

async def test_horizon_too_long_returns_400():
    # POST /forecast {"companyCode": "1000", "horizonDays": 365}
    # Assert 400 with meaningful error

async def test_horizon_zero_returns_400():
    # POST /forecast {"companyCode": "1000", "horizonDays": 0}
    # Assert 400
```

#### 4. LSTM — smoke test (if weights present)
```python
@pytest.mark.skipif(not Path("model/weights/lstm_model.h5").exists(), reason="No LSTM weights")
async def test_lstm_output_passes_schema_contract():
    # POST /forecast with LSTM active
    # Assert same schema as stub test
    # Assert modelType == "LSTM_MC_DROPOUT"
    # Assert fallbackUsed == false

@pytest.mark.skipif(not Path("model/weights/lstm_model.h5").exists(), reason="No LSTM weights")
async def test_lstm_confidence_intervals_are_non_trivial():
    # Assert std > 0 for all days (MC Dropout is active and producing variance)
```

#### 5. Agent threshold semantics
```python
async def test_low_confidence_threshold_behaviour():
    # Inject a mock that always returns overallConfidenceScore=0.4
    # Assert "LOW_CONFIDENCE_BEYOND_DAY_10" or similar flag is present
    # (Tests that the agent can identify when to route to Disambiguate)
```

### Running Tests

```bash
cd services/forecaster
pytest tests/ -v
# Run only stub tests (no LSTM required):
pytest tests/ -v -k "not lstm"
```
