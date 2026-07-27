# Mock Sampath Bank API

This service simulates a corporate API banking platform modelled on Sampath Bank's real "API Banking Platform." It provides endpoints for accounts, balances, statements, payment initiation, payment polling, deposit/forex rates, and loan facilities.

## Directory Structure and Scripts

Here is an overview of what each script in this service does:

### Core Application
- **`main.py`**: The main FastAPI application entry point. It registers all the API routes, handles OAuth2 token authentication, validates HMAC-SHA256 signatures for payments, and runs a background task (`payment_status_worker`) that simulates the time it takes for a payment to transition from `PENDING_APPROVAL` to `EXECUTED`. It also contains a `simulate_failure` mechanism to mimic network issues.
- **`requirements.txt`**: Lists all the necessary Python dependencies (FastAPI, Uvicorn, Pydantic, python-jose, httpx, pytest, etc.) required to run the service and its tests.

### Schemas
- **`schemas/entities.py`**: Contains all the Pydantic models used to validate incoming requests and serialize outgoing responses. This ensures strict adherence to the API contract expected by the treasury agent.

### State Management
*Since this is a mock API, state is maintained in memory during runtime.*
- **`state/account_state.py`**: Defines the data structures and in-memory stores (`AccountStore`, `PaymentStore`, `TransactionStore`) for accounts and payments. It includes the `debit_account` function, which handles real-time deduction of funds when a payment is initiated.
- **`state/loan_state.py`**: Defines the data structures and in-memory store (`LoanStore`) for tracking loan facilities (e.g., sanctioned amount, interest rate, next installment).

### Data
- **`data/seed.py`**: A script that runs when the application starts up. It populates the in-memory stores with realistic mock data, including active current and deposit accounts, a history of transactions, specific working capital and term loans, and static arrays for deposit and forex rates.

### Tests
- **`tests/test_bank_mock.py`**: A comprehensive test suite using `pytest` and `httpx`. It validates the behavior of all endpoints, checking authentication rules, signature validation, happy paths (e.g., balance deduction after payment), and edge cases (e.g., insufficient funds, invalid beneficiaries, simulated timeouts).

## How to Run

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
2. **Start the server:**
   ```bash
   uvicorn main:app --reload --port 8002
   ```
3. **Run the tests:**
   ```bash
   $env:PYTHONPATH="."
   pytest tests/ -v
   ```
