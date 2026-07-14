# Component 9: Market Data Tool

**Status: To build — scraper first, CBSL Selenium second.**

---

## Purpose

A **standalone service and agent tool** that assembles current market rate data
from three sources:

1. **Bank website scraping** — publicly available FD rates, Call Deposit rates,
   and Repo rates from Sampath Bank, HNB, and Commercial Bank of Ceylon.
2. **CBSL web scraping (Selenium)** — Central Bank of Sri Lanka policy rates:
   Repo Rate, Reverse Repo Rate, AWPR, AWPLR, and Standing Facility Rates.
3. **Mock bank API** — the bank mock's `/rates/forex` and `/rates/deposits`
   endpoints (Component 2) are not scraped — they are called via HTTP client.

This service runs on a **scheduled background cycle** (not on every agent
reasoning call). It writes a cached rates file and exposes a simple HTTP endpoint
the agent's Perceive node calls to retrieve current rates.

---

## Why This Component Exists

### The bank website vs. CBSL distinction

| Source | Gives You | Used By |
|---|---|---|
| Bank websites (Sampath, HNB, ComBank) | Actual product rates — FD rates by tenor, Call Deposit, Repo | SciPy Optimizer: yield comparison across 3 banks to pick best placement |
| CBSL | Policy rates: Repo Rate, AWPR, AWPLR | LSTM feature vector (regime signal); Optimizer hurdle rate; Floating loan rate recomputation |
| Mock bank `/rates/deposits` | Sampath-specific instrument rates (same source seeded from real Sampath rates) | Optimizer input — consistent with mock bank state |
| Mock bank `/rates/forex` | Live dealing rates for FX valuation | Perceive agent: value FX obligations in LKR |

### Why 3 banks for the optimizer

The optimizer in Component 4 currently only sees rates from the mock bank
(`/rates/deposits`). In practice, a treasury copilot should recommend the
**best available rate across all banks the company has relationships with**.
This service adds HNB and Commercial Bank rate data so the optimizer can
evaluate cross-bank placements.

### Why CBSL data matters for the LSTM

The LSTM (Component 3) forecasts net daily cash flow. Cash flow patterns are
not independent of the monetary policy environment:
- High repo-rate periods correlate with higher bank rates → management tends
  to lock in longer-term FDs → liquidity dips.
- AWPLR direction signals whether variable-rate loan costs are increasing.

Adding CBSL policy rates as **exogenous features** gives the LSTM regime-awareness
it cannot derive from cash flow history alone.

---

## Canonical Location

```
services/market-data/
├── main.py                        # FastAPI — GET /rates endpoint
├── scrapers/
│   ├── sampath_scraper.py         # Sampath Bank website FD/CD/Repo rates
│   ├── hnb_scraper.py             # HNB website FD/CD rates
│   ├── combank_scraper.py         # Commercial Bank website FD/CD rates
│   └── cbsl_scraper.py            # CBSL Selenium scraper (policy rates)
├── scheduler.py                   # APScheduler — runs scrapers on cron
├── cache/
│   └── rates_cache.json           # Persisted on each successful scrape
├── tests/
│   └── test_market_data.py
└── requirements.txt
```

**`requirements.txt`**:
```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
pydantic>=2.7.0
httpx>=0.27.0
beautifulsoup4>=4.12.0
lxml>=5.2.0
selenium>=4.21.0
webdriver-manager>=4.0.0           # Auto-manage chromedriver
APScheduler>=3.10.0
tenacity>=8.3.0
pytest>=8.2.0
pytest-anyio>=0.0.0
```

---

## How to Run

```bash
cd services/market-data
pip install -r requirements.txt
uvicorn main:app --reload --port 8005
```

The service starts the APScheduler background thread on startup. Scraping runs
immediately at startup, then on the configured cron schedule.

Health check: `GET http://localhost:8005/health`

Force re-scrape (for testing): `POST http://localhost:8005/refresh`

---

## Scraper Architecture

### General Principle: HTTP-first, Selenium only for CBSL

Bank websites (Sampath, HNB, ComBank) publish their deposit rates as static HTML
pages or lightly dynamic pages. Use `httpx` + `BeautifulSoup` — no JavaScript
execution required. This is fast (<2s per bank) and does not require a browser.

CBSL's statistical tables are rendered via JavaScript. Use Selenium with a
headless Chromium browser for CBSL only. This is slower (~15–30s) and requires
`chromedriver` — but since it runs on a 24-hour schedule, latency does not matter.

### Scraper Schedule

| Scraper | Frequency | Rationale |
|---|---|---|
| `sampath_scraper.py` | Every 6 hours | FD rates can update intraday |
| `hnb_scraper.py` | Every 6 hours | Same |
| `combank_scraper.py` | Every 6 hours | Same |
| `cbsl_scraper.py` | Every 24 hours at 08:00 LKT | CBSL publishes weekly/monthly; daily poll is sufficient |

```python
# scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler

def start_scheduler(app_state):
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_bank_scrapers, "interval", hours=6, args=[app_state])
    scheduler.add_job(run_cbsl_scraper,  "cron", hour=8, minute=0, args=[app_state])
    scheduler.start()
```

---

## Scraper Specifications

### `sampath_scraper.py`

**Target**: Sampath Bank public website deposit rate table.
**Method**: `httpx.get` → `BeautifulSoup` HTML parse.

Data to extract:
- Fixed Deposit rates by tenor: 7-day, 14-day, 30-day, 90-day, 180-day, 365-day.
- Call/Savings Deposit rate.
- Repo rate (if published on the rates page).

```python
async def scrape_sampath_rates() -> dict:
    """Returns dict with keys: fd_rates (list), call_deposit_rate, repo_rate, scraped_at."""
    ...
```

### `hnb_scraper.py`

**Target**: HNB public rates page.
**Method**: `httpx.get` → `BeautifulSoup`.

Data to extract:
- Fixed Deposit rates by tenor.
- Savings Account rates.

```python
async def scrape_hnb_rates() -> dict:
    """Returns dict with keys: fd_rates (list), savings_rate, scraped_at."""
    ...
```

### `combank_scraper.py`

**Target**: Commercial Bank of Ceylon public rates page.
**Method**: `httpx.get` → `BeautifulSoup`.

Data to extract:
- Fixed Deposit rates by tenor.
- Savings rates.

```python
async def scrape_combank_rates() -> dict:
    """Returns dict with keys: fd_rates (list), savings_rate, scraped_at."""
    ...
```

### `cbsl_scraper.py`

**Target**: CBSL website statistical tables (policy rates, AWPLR, AWPR).
**Method**: Selenium + headless Chromium.

Data to extract:

| Field | CBSL Table/Publication | Description |
|---|---|---|
| `repo_rate` | Monetary Policy rates | CBSL's Standing Deposit Facility (SDF) rate — the rate at which banks deposit with CBSL |
| `reverse_repo_rate` | Monetary Policy rates | Standing Lending Facility (SLF) rate — the rate at which banks borrow from CBSL |
| `awpr` | Market Interest Rates | Average Weighted Prime Rate — weighted average of rates on prime short-term loans |
| `awplr` | Market Interest Rates | Average Weighted Prime Lending Rate — the key floating loan benchmark for corporate loans |
| `published_date` | As shown on table | Date of the most recent CBSL update |

```python
def scrape_cbsl_rates() -> dict:
    """
    Runs synchronously (Selenium is blocking). Returns:
    {
      "repo_rate": 8.50,
      "reverse_repo_rate": 9.50,
      "awpr": 9.25,
      "awplr": 12.00,
      "published_date": "2026-07-11",
      "scraped_at": "2026-07-14T08:02:11+05:30"
    }
    """
    options = ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(
        service=ChromeService(ChromeDriverManager().install()),
        options=options
    )
    try:
        driver.get("https://www.cbsl.gov.lk/en/statistics/...")
        # wait for table to render, parse with BeautifulSoup
        ...
    finally:
        driver.quit()
```

> **Resilience note**: If the CBSL website is unreachable or the page structure
> has changed (scraper breakage), the service falls back to the last successfully
> cached values from `cache/rates_cache.json` and sets `cbsl_stale: true` in
> the response. The agent must surface this as a `DataFreshness.STALE` signal
> on the AWPLR field — it should not silently use stale data for floating-rate
> loan recomputation without flagging it.

---

## Output Contract (What the Agent Consumes)

```
GET /rates
Authorization: Bearer <internal-service-token>
```

```json
{
  "asOfTimestamp": "2026-07-14T08:05:00+05:30",
  "bankRates": {
    "sampath": {
      "fixedDeposit": [
        { "termDays": 7,   "rate": 0.0950 },
        { "termDays": 14,  "rate": 0.1000 },
        { "termDays": 30,  "rate": 0.1100 },
        { "termDays": 90,  "rate": 0.1200 },
        { "termDays": 180, "rate": 0.1250 },
        { "termDays": 365, "rate": 0.1300 }
      ],
      "callDeposit": { "rate": 0.0850 },
      "repoRate": 0.0875,
      "scraped_at": "2026-07-14T06:01:00+05:30",
      "stale": false
    },
    "hnb": {
      "fixedDeposit": [
        { "termDays": 30,  "rate": 0.1125 },
        { "termDays": 90,  "rate": 0.1210 },
        { "termDays": 180, "rate": 0.1260 }
      ],
      "callDeposit": { "rate": 0.0870 },
      "scraped_at": "2026-07-14T06:02:00+05:30",
      "stale": false
    },
    "combank": {
      "fixedDeposit": [
        { "termDays": 30,  "rate": 0.1100 },
        { "termDays": 90,  "rate": 0.1200 },
        { "termDays": 180, "rate": 0.1240 }
      ],
      "callDeposit": { "rate": 0.0860 },
      "scraped_at": "2026-07-14T06:03:00+05:30",
      "stale": false
    }
  },
  "cbsl": {
    "repoRate": 8.50,
    "reverseRepoRate": 9.50,
    "awpr": 9.25,
    "awplr": 12.00,
    "publishedDate": "2026-07-11",
    "scraped_at": "2026-07-14T08:02:00+05:30",
    "stale": false
  },
  "bestAvailableRates": {
    "fd_30d":  { "rate": 0.1125, "bank": "HNB" },
    "fd_90d":  { "rate": 0.1210, "bank": "HNB" },
    "fd_180d": { "rate": 0.1260, "bank": "HNB" },
    "callDeposit": { "rate": 0.0870, "bank": "HNB" }
  }
}
```

### `bestAvailableRates` section

This is a pre-computed summary — the best rate per tenor across all three
scraped banks. The optimizer uses this to recommend cross-bank placements.
The agent's rationale generation can cite it directly: *"HNB is currently
offering 12.1% on 90-day FD, 10bps above Sampath — recommend HNB placement."*

---

## Integration with Other Components

### Perceive Node (`agent/nodes/perceive.py`)

Add `market_data_client.get_rates()` call alongside existing ERP and bank calls.
Results are merged into `TreasuryState`:

```python
# In TreasuryState (agent/state.py) — new fields
class TreasuryState(BaseModel):
    ...
    # Existing fields unchanged
    market_rates: MarketRates | None = None          # NEW
    awplr: float | None = None                        # NEW — surfaced for floating loan recomputation
    cbsl_rates_stale: bool = False                    # NEW
```

```python
class MarketRates(BaseModel):
    best_fd_rates: dict[str, dict]   # keyed by tenor "fd_30d", "fd_90d", etc.
    call_deposit_best: dict          # {"rate": 0.087, "bank": "HNB"}
    awplr: float
    repo_rate: float
    as_of: datetime
    cbsl_stale: bool
```

### Floating Rate Loan Recomputation

When the Perceive node retrieves loan data from `GET /loans/{facilityId}` and
finds `rateType == "FLOATING"` with `benchmarkRate == "AWPLR"`, it must:

```python
current_awplr = treasury_state.awplr  # from market data
if treasury_state.cbsl_rates_stale:
    # Flag this — the recomputed rate may be based on old AWPLR
    add_flag("STALE_AWPLR_LOAN_RATE_ESTIMATE")
effective_rate = current_awplr / 100 + loan.spread
```

This recomputed `effective_rate` replaces the seeded value from the bank mock
and is what the optimizer uses as the cost-of-debt reference.

### SciPy Optimizer (`services/optimizer/`)

The optimizer receives an updated input that includes cross-bank instruments:

```json
{
  "availableSurplus": "8000000.00",
  "minimumBufferRequired": "20000000.00",
  "currentTotalBalance": "28000000.00",
  "asOfDate": "2026-07-14",
  "nextFixedObligationDate": "2026-07-28",
  "nextFixedObligationAmount": "4200000.00",
  "costOfDebt": 0.1350,
  "instruments": [
    { "bank": "SAMPATH", "type": "CALL_DEPOSIT", "termDays": 1,   "rate": 0.085 },
    { "bank": "SAMPATH", "type": "FIXED_DEPOSIT", "termDays": 14, "rate": 0.100 },
    { "bank": "HNB",     "type": "FIXED_DEPOSIT", "termDays": 14, "rate": 0.102 },
    { "bank": "COMBANK", "type": "FIXED_DEPOSIT", "termDays": 14, "rate": 0.100 },
    { "bank": "HNB",     "type": "FIXED_DEPOSIT", "termDays": 30, "rate": 0.1125 },
    { "bank": "SAMPATH", "type": "FIXED_DEPOSIT", "termDays": 90, "rate": 0.120 },
    { "bank": "HNB",     "type": "FIXED_DEPOSIT", "termDays": 90, "rate": 0.121 }
  ]
}
```

**New field: `costOfDebt`** — the recomputed effective rate on the company's
floating rate OD facility. This is the optimizer's **hurdle rate**: any instrument
returning less than `costOfDebt` should be flagged as sub-optimal (paying down the
OD would yield more than placing at that rate).

See `04-optimizer-tool-scipy.md` for the updated optimizer spec.

### LSTM Forecaster (`services/forecaster/`)

CBSL rates are added as **exogenous features** to the LSTM input vector. See
`03-forecasting-tool-lstm.md` for the updated feature spec.

---

## Failure Modes

| Failure | Behaviour |
|---|---|
| Bank website unreachable (HTTP error / structure changed) | Use last cached rates for that bank; set `stale: true` on that bank's entry. Mark other banks' data fresh as normal. |
| All 3 bank scrapers fail | `GET /rates` returns cached data for all with `stale: true`; Perceive node adds `DataFreshness.STALE` for `MARKET_RATES`. |
| CBSL scraper fails (Selenium crash, page change) | Use cached CBSL rates; set `cbsl.stale: true`; agent flags `STALE_AWPLR` on floating rate loan. |
| No cache file exists (first run + all scrapers fail) | `GET /rates` returns `503 {"error": "MARKET_DATA_UNAVAILABLE", "reason": "First-run scrape failed and no cache exists"}`. Perceive node marks `execution_blocked: true` only if `awplr` is needed for a floating rate loan recomputation. |
| Scraped rate value is implausible (e.g. 0% or 500%) | Apply sanity check: FD rate must be `0.01 <= rate <= 0.30`. If outside range, discard and use cached value; log `IMPLAUSIBLE_RATE_DISCARDED`. |

---

## Testing Requirements

### Test file: `services/market-data/tests/test_market_data.py`

#### 1. Output contract
```python
async def test_get_rates_returns_correct_schema():
    # GET /rates
    # Assert response has: bankRates, cbsl, bestAvailableRates, asOfTimestamp

async def test_best_available_rates_keys_present():
    # Assert bestAvailableRates has: fd_30d, fd_90d, callDeposit

async def test_best_available_rate_matches_max_across_banks():
    # Assert bestAvailableRates.fd_30d.rate == max(sampath 30d, hnb 30d, combank 30d)
```

#### 2. Staleness propagation
```python
async def test_stale_flag_set_when_cache_is_used():
    # Patch scraper to raise exception, pre-seed cache file
    # GET /rates
    # Assert affected bank's stale == true

async def test_cbsl_stale_flag_propagates():
    # Patch CBSL scraper to fail
    # Assert cbsl.stale == true in response
```

#### 3. CBSL scraper unit test (mocked Selenium)
```python
def test_cbsl_scraper_parses_rates_correctly():
    # Provide fixture HTML matching CBSL page structure
    # Assert parsed: repo_rate, reverse_repo_rate, awpr, awplr

def test_cbsl_scraper_rejects_implausible_rates():
    # Provide fixture HTML with 0.0% repo rate
    # Assert IMPLAUSIBLE_RATE_DISCARDED logged, cached value used
```

#### 4. Bank scraper unit tests (mocked HTTP)
```python
async def test_sampath_scraper_parses_fd_rates():
    # Provide fixture HTML for Sampath rates page
    # Assert fd_rates list has at least 4 tenors
    # Assert all rates in range [0.01, 0.30]

async def test_hnb_scraper_parses_fd_rates():
    # Same as above for HNB

async def test_combank_scraper_parses_fd_rates():
    # Same as above for ComBank
```

#### 5. Scheduler (integration)
```python
async def test_refresh_endpoint_triggers_scrape():
    # POST /refresh
    # Assert 200
    # Assert cache file updated (check mtime)

async def test_health_reports_last_scrape_time():
    # GET /health
    # Assert response has last_scraped_at field
```

### Running Tests

```bash
cd services/market-data
pytest tests/ -v
# Run without Selenium (skip CBSL integration test):
pytest tests/ -v -k "not cbsl_integration"
```

---

## Seed / Fallback Cache

A committed `cache/rates_cache.json` with realistic LKR rates is included in the
repo. This ensures the service works on first run even if all scrapers fail
(e.g. in a CI environment with no network access). The committed values are
clearly dated and should be updated monthly.

```json
{
  "_note": "Fallback seed cache — update monthly. Last updated: 2026-07-01.",
  "bankRates": { ... },
  "cbsl": {
    "repoRate": 8.50,
    "reverseRepoRate": 9.50,
    "awpr": 9.25,
    "awplr": 12.00,
    "publishedDate": "2026-07-01",
    "scraped_at": "2026-07-01T08:00:00+05:30",
    "stale": true
  }
}
```
