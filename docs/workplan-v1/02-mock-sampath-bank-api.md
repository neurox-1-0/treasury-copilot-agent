# Component 2: Mock Sampath-Style Bank API

**Status: To build.**

---

## Purpose

Simulate a corporate API banking platform modelled on Sampath Bank's real
*"API Banking Platform"* — Sri Lanka's first standardised corporate API banking
offering, which supports faster supplier payments, real-time balance inquiries,
and trade-related transactions while maintaining corporate approval chains and
audit controls.

> **Note on live credentials**: Sandbox credentials for the real platform require
> relationship-manager onboarding. This is a **modelled mock**, not a real
> integration. Document this honestly in any submission — the value is the
> realistic architecture and contract, not a claimed live connection.

---

## Canonical Location

```
services/bank-mock/
├── main.py
├── schemas/
│   └── entities.py
├── data/
│   └── seed.py
├── state/
│   └── account_state.py       ← mutable balance state
├── tests/
│   └── test_bank_mock.py
└── requirements.txt
```

**`requirements.txt`**:
```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
pydantic>=2.7.0
python-jose[cryptography]>=3.3.0   # JWT token issuance
httpx>=0.27.0
pytest>=8.2.0
pytest-anyio>=0.0.0
```

---

## How to Run

```bash
cd services/bank-mock
pip install -r requirements.txt
uvicorn main:app --reload --port 8002
```

Health check: `GET http://localhost:8002/health`

---

## Authentication Model

OAuth2 client-credentials flow. All endpoints except `/auth/token` and `/health`
require a valid `Bearer` token.

### Token issuance

```
POST /auth/token
Content-Type: application/json

{
  "client_id": "treasury-agent",
  "client_secret": "demo-secret-1234",
  "grant_type": "client_credentials"
}
```

Response:
```json
{
  "access_token": "<JWT>",
  "expires_in": 3600,
  "token_type": "Bearer"
}
```

For the mock, the JWT is generated with `python-jose` using a shared secret stored
in an environment variable (`BANK_MOCK_JWT_SECRET`, default `dev-secret`). The
payload carries only `{ "client_id": "...", "exp": ... }`.

### Payment endpoint signature (HMAC-SHA256)

`POST /payments/initiate` additionally requires an `X-Signature` header:

```
X-Signature: HMAC-SHA256(key=PAYMENT_SIGNING_SECRET, msg=canonical_body)
```

Where `canonical_body` is defined as:
```python
canonical_body = json.dumps(payment_payload, sort_keys=True, separators=(",", ":"))
```

The mock verifies this header before processing the payment. Mismatch returns
`401 {"error": "INVALID_SIGNATURE"}`. `PAYMENT_SIGNING_SECRET` is also an
environment variable (default `dev-signing-secret`).

---

## State Management

**The key design decision**: balances are mutable state, not seed data.

Define `services/bank-mock/state/account_state.py`:

```python
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict

@dataclass
class AccountState:
    account_id: str
    currency: str
    available_balance: Decimal
    book_balance: Decimal
    last_updated: datetime = field(default_factory=datetime.utcnow)

# Singleton — initialised from seed.py at startup
AccountStore: Dict[str, AccountState] = {}

def debit_account(account_id: str, amount: Decimal) -> AccountState:
    """Called by payment initiation. Raises ValueError if insufficient funds."""
    state = AccountStore[account_id]
    if state.available_balance < amount:
        raise ValueError("INSUFFICIENT_FUNDS")
    state.available_balance -= amount
    state.book_balance -= amount
    state.last_updated = datetime.utcnow()
    return state
```

The `AccountStore` is populated in `main.py` at startup from `data/seed.py`.
Payment initiation calls `debit_account` synchronously. This is intentionally
simple — no double-entry ledger needed for this demo.

---

## Endpoints

### `GET /accounts` (List accounts)

Returns all account IDs known to the mock — so the agent can discover which IDs
to query. Authorised with `Bearer` token.

```json
{
  "accounts": [
    { "accountId": "SAMP-0012345678", "currency": "LKR", "accountType": "CURRENT" },
    { "accountId": "SAMP-0012345679", "currency": "LKR", "accountType": "CALL_DEPOSIT" }
  ]
}
```

### `GET /accounts/{accountId}/balance`

Returns current balances. These values reflect real-time mutations from payment
initiation (not stale seed data).

```json
{
  "accountId": "SAMP-0012345678",
  "currency": "LKR",
  "availableBalance": "42500000.00",
  "bookBalance": "42441068.00",
  "asOfTimestamp": "2026-07-13T09:30:00+05:30"
}
```

### `GET /accounts/{accountId}/statement`

Transaction history, date-ranged. Query params: `fromDate`, `toDate` (ISO 8601).

```json
{
  "accountId": "SAMP-0012345678",
  "fromDate": "2026-07-01",
  "toDate": "2026-07-13",
  "transactions": [
    {
      "transactionId": "TXN-2026070001",
      "date": "2026-07-01",
      "amount": "1500000.00",
      "direction": "CREDIT",
      "description": "Customer receipt — Invoice INV-2026-0045"
    }
  ]
}
```

### `POST /payments/initiate`

Submit a payment. Requires `Bearer` token **and** `X-Signature` header.

Request body:
```json
{
  "sourceAccountId": "SAMP-0012345678",
  "beneficiaryAccount": "COMB-0098765432",
  "amount": "8000000.00",
  "currency": "LKR",
  "purpose": "SURPLUS_SWEEP",
  "requestedExecutionDate": "2026-07-14",
  "referenceNote": "14-day FD placement per treasury proposal TRP-0042"
}
```

Response:
```json
{
  "paymentId": "PMT-2026070001",
  "status": "PENDING_APPROVAL",
  "amount": "8000000.00",
  "currency": "LKR",
  "beneficiaryAccount": "COMB-0098765432",
  "purpose": "SURPLUS_SWEEP",
  "requestedExecutionDate": "2026-07-14",
  "submittedAt": "2026-07-13T09:31:00+05:30"
}
```

On success, `debit_account` is called and the payment is stored in an in-process
`PaymentStore: Dict[str, PaymentRecord]`.

### `GET /payments/{paymentId}/status`

Returns current status of a submitted payment.

```json
{
  "paymentId": "PMT-2026070001",
  "status": "EXECUTED",
  "executedAt": "2026-07-13T09:35:00+05:30"
}
```

### `GET /rates/deposits`

Short-term instrument rates. Feeds the SciPy optimizer. Static seed data, updated
manually to reflect realistic LKR rates.

```json
{
  "instruments": [
    { "type": "CALL_DEPOSIT", "termDays": 1, "rate": 0.085 },
    { "type": "FIXED_DEPOSIT", "termDays": 14, "rate": 0.10 },
    { "type": "FIXED_DEPOSIT", "termDays": 30, "rate": 0.11 },
    { "type": "FIXED_DEPOSIT", "termDays": 90, "rate": 0.12 }
  ],
  "asOfDate": "2026-07-13"
}
```

---

## Payment Status State Machine

```
PENDING_APPROVAL → APPROVED → EXECUTED
               ↘            ↗
                → REJECTED
                           → FAILED
```

**Auto-transition for demo purposes**: The mock runs a background task that
advances payment status automatically after configurable delays:
- `PENDING_APPROVAL` → `APPROVED` after 5 seconds (configurable via env var
  `PAYMENT_AUTO_APPROVE_DELAY_SECONDS`, default `5`).
- `APPROVED` → `EXECUTED` after a further 3 seconds.

This allows the agent's Report node to exercise the polling loop without manual
intervention during a demo. Set `PAYMENT_AUTO_APPROVE_DELAY_SECONDS=0` to disable
(useful for testing rejection flows manually).

---

## Failure Modes (Deliberate, for Component 7 Testing)

Each failure mode is activated by a query parameter `?simulate=<mode>` on any
endpoint, or by a global toggle via `POST /chaos` (see `ChaosPanel` in dashboard):

| Mode | Trigger | HTTP behaviour |
|---|---|---|
| `timeout` | `?simulate=timeout` | Response delayed by 10s (triggers agent's 3s timeout) |
| `auth_failure` | Expired/invalid token | `401 {"error": "TOKEN_EXPIRED", "message": "Re-authenticate"}` |
| `insufficient_funds` | Payment amount > available balance | `422 {"error": "INSUFFICIENT_FUNDS", "availableBalance": "..."}` |
| `beneficiary_invalid` | Unknown beneficiary account | `422 {"error": "INVALID_BENEFICIARY"}` |
| `rate_limit` | >20 requests/minute (optional) | `429 {"error": "RATE_LIMIT_EXCEEDED", "retryAfter": 60}` |
| `write_failure` | `?simulate=write_failure` on payment initiate | `500 {"error": "UPSTREAM_GATEWAY_TIMEOUT"}` — payment status unknown |

The `write_failure` mode is the most important: it simulates the case where the
agent cannot confirm whether a payment went through — the correct response is to
halt and surface "manual verification required", not to retry and risk a duplicate
payment.

---

## Interface Contract the Agent Depends On

| Stage | Call | Purpose |
|---|---|---|
| Perceive | `GET /accounts` then `GET /accounts/{id}/balance` for each account | Reconcile bank balances against ERP cash positions |
| Perceive | `GET /accounts/{id}/statement?fromDate=...&toDate=...` | Detect unreconciled large incoming transactions |
| Reason | `GET /rates/deposits` | Feed optimizer's yield comparison |
| Decide & Act | `POST /payments/initiate` | Execute approved proposal |
| Report | `GET /payments/{id}/status` | Confirm execution before closing audit trail |

---

## Testing Requirements

### Test file: `services/bank-mock/tests/test_bank_mock.py`

#### 1. Authentication
```python
async def test_token_issuance_with_valid_credentials():
    # POST /auth/token with valid client_id + client_secret
    # Assert 200, response has "access_token", "token_type": "Bearer"

async def test_token_rejected_with_invalid_credentials():
    # POST /auth/token with wrong secret
    # Assert 401

async def test_protected_endpoint_requires_bearer_token():
    # GET /accounts/SAMP-0012345678/balance without Authorization header
    # Assert 401

async def test_expired_token_returns_401():
    # Manually construct an expired JWT, use it on a protected endpoint
    # Assert 401 with TOKEN_EXPIRED error body
```

#### 2. Balance endpoint
```python
async def test_balance_returns_correct_shape():
    # Assert response has: accountId, currency, availableBalance, bookBalance, asOfTimestamp

async def test_balance_unknown_account_returns_404():
    # GET /accounts/NONEXISTENT/balance
    # Assert 404
```

#### 3. Payment initiation — happy path
```python
async def test_payment_initiation_returns_pending_approval():
    # POST /payments/initiate with valid body + valid signature
    # Assert 201, status == "PENDING_APPROVAL"

async def test_payment_deducts_balance():
    # Record balance before payment
    # POST /payments/initiate
    # GET /accounts/{id}/balance again
    # Assert new balance == old balance - payment amount
```

#### 4. Payment initiation — failure paths
```python
async def test_payment_rejected_with_invalid_signature():
    # POST /payments/initiate with wrong X-Signature
    # Assert 401 INVALID_SIGNATURE

async def test_payment_rejected_insufficient_funds():
    # POST with amount > available balance
    # Assert 422 INSUFFICIENT_FUNDS

async def test_payment_rejected_invalid_beneficiary():
    # POST with unknown beneficiary account
    # Assert 422 INVALID_BENEFICIARY
```

#### 5. Payment status polling
```python
async def test_payment_status_transitions_to_executed():
    # Initiate payment, poll status until EXECUTED (with timeout)
    # Assert final status == "EXECUTED"

async def test_payment_status_unknown_id_returns_404():
    # GET /payments/NONEXISTENT/status
    # Assert 404
```

#### 6. Deposit rates
```python
async def test_deposit_rates_returns_all_instrument_types():
    # Assert response has CALL_DEPOSIT and at least 3 FIXED_DEPOSIT tenors

async def test_deposit_rates_fields_complete():
    # Assert each instrument has: type, termDays, rate
    # Assert rate values are sensible (0 < rate < 1)
```

#### 7. Failure simulation
```python
async def test_timeout_simulation_delays_response():
    # GET /accounts/{id}/balance?simulate=timeout
    # Assert response time > 8s (mock delay)

async def test_write_failure_simulation_returns_500():
    # POST /payments/initiate?simulate=write_failure
    # Assert 500 with UPSTREAM_GATEWAY_TIMEOUT body
    # Assert balance NOT changed (idempotent on error)
```

#### 8. Accounts list
```python
async def test_accounts_list_returns_seeded_accounts():
    # GET /accounts
    # Assert list is non-empty, each item has accountId + currency
```

### Running Tests

```bash
cd services/bank-mock
pytest tests/ -v
```
