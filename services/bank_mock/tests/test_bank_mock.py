import pytest
from httpx import AsyncClient, ASGITransport
import json
import hmac
import hashlib
from datetime import datetime, date

from main import app
from data.seed import seed_data

@pytest.fixture(autouse=True)
def setup_seed():
    seed_data()

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

@pytest.fixture
async def valid_token(client):
    response = await client.post("/auth/token", json={
        "client_id": "treasury-agent",
        "client_secret": "demo-secret-1234",
        "grant_type": "client_credentials"
    })
    return response.json()["access_token"]

@pytest.fixture
def auth_headers(valid_token):
    return {"Authorization": f"Bearer {valid_token}"}

@pytest.mark.anyio
async def test_token_issuance_with_valid_credentials(client):
    response = await client.post("/auth/token", json={
        "client_id": "treasury-agent",
        "client_secret": "demo-secret-1234",
        "grant_type": "client_credentials"
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "Bearer"

@pytest.mark.anyio
async def test_token_rejected_with_invalid_credentials(client):
    response = await client.post("/auth/token", json={
        "client_id": "treasury-agent",
        "client_secret": "wrong-secret",
        "grant_type": "client_credentials"
    })
    assert response.status_code == 401

@pytest.mark.anyio
async def test_protected_endpoint_requires_bearer_token(client):
    response = await client.get("/accounts/SAMP-0012345678/balance")
    assert response.status_code == 401

@pytest.mark.anyio
async def test_balance_returns_correct_shape(client, auth_headers):
    response = await client.get("/accounts/SAMP-0012345678/balance", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "accountId" in data
    assert "currency" in data
    assert "availableBalance" in data
    assert "bookBalance" in data
    assert "asOfTimestamp" in data
    assert "averageBalance" in data
    assert "averagePeriodDays" in data
    assert float(data["averageBalance"]) > 0

@pytest.mark.anyio
async def test_balance_unknown_account_returns_404(client, auth_headers):
    response = await client.get("/accounts/NONEXISTENT/balance", headers=auth_headers)
    assert response.status_code == 404

def get_signature(payload_dict):
    canonical_body = json.dumps(payload_dict, sort_keys=True, separators=(",", ":"))
    return hmac.new(b"dev-signing-secret", canonical_body.encode(), hashlib.sha256).hexdigest()

@pytest.mark.anyio
async def test_payment_initiation_returns_pending_approval(client, auth_headers):
    payload = {
        "sourceAccountId": "SAMP-0012345678",
        "beneficiaryAccount": "COMB-0098765432",
        "amount": "8000000.00",
        "currency": "LKR",
        "purpose": "SURPLUS_SWEEP",
        "requestedExecutionDate": date.today().isoformat(),
        "referenceNote": "test"
    }
    sig = get_signature(payload)
    headers = {**auth_headers, "X-Signature": sig}
    response = await client.post("/payments/initiate", json=payload, headers=headers)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "PENDING_APPROVAL"

@pytest.mark.anyio
async def test_payment_deducts_balance(client, auth_headers):
    bal_res1 = await client.get("/accounts/SAMP-0012345678/balance", headers=auth_headers)
    initial_balance = float(bal_res1.json()["availableBalance"])
    
    payload = {
        "sourceAccountId": "SAMP-0012345678",
        "beneficiaryAccount": "COMB-0098765432",
        "amount": "100.00",
        "currency": "LKR",
        "purpose": "TEST",
        "requestedExecutionDate": date.today().isoformat()
    }
    sig = get_signature(payload)
    headers = {**auth_headers, "X-Signature": sig}
    await client.post("/payments/initiate", json=payload, headers=headers)
    
    bal_res2 = await client.get("/accounts/SAMP-0012345678/balance", headers=auth_headers)
    new_balance = float(bal_res2.json()["availableBalance"])
    assert initial_balance - new_balance == 100.00

@pytest.mark.anyio
async def test_payment_rejected_with_invalid_signature(client, auth_headers):
    payload = {
        "sourceAccountId": "SAMP-0012345678",
        "beneficiaryAccount": "COMB-0098765432",
        "amount": "8000000.00",
        "currency": "LKR",
        "purpose": "SURPLUS_SWEEP",
        "requestedExecutionDate": date.today().isoformat()
    }
    headers = {**auth_headers, "X-Signature": "invalid-sig"}
    response = await client.post("/payments/initiate", json=payload, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "INVALID_SIGNATURE"

@pytest.mark.anyio
async def test_payment_rejected_insufficient_funds(client, auth_headers):
    payload = {
        "sourceAccountId": "SAMP-0012345678",
        "beneficiaryAccount": "COMB-0098765432",
        "amount": "999999999.00",
        "currency": "LKR",
        "purpose": "SURPLUS_SWEEP",
        "requestedExecutionDate": date.today().isoformat()
    }
    sig = get_signature(payload)
    headers = {**auth_headers, "X-Signature": sig}
    response = await client.post("/payments/initiate", json=payload, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"] == "INSUFFICIENT_FUNDS"

@pytest.mark.anyio
async def test_payment_rejected_invalid_beneficiary(client, auth_headers):
    payload = {
        "sourceAccountId": "SAMP-0012345678",
        "beneficiaryAccount": "HNB-0098765432",
        "amount": "100.00",
        "currency": "LKR",
        "purpose": "SURPLUS_SWEEP",
        "requestedExecutionDate": date.today().isoformat()
    }
    sig = get_signature(payload)
    headers = {**auth_headers, "X-Signature": sig}
    response = await client.post("/payments/initiate", json=payload, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_BENEFICIARY"

@pytest.mark.anyio
async def test_deposit_rates_returns_all_instrument_types(client, auth_headers):
    response = await client.get("/rates/deposits", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["bank"] == "SAMPATH"
    types = [instr["type"] for instr in data["instruments"]]
    assert "CALL_DEPOSIT" in types
    assert types.count("FIXED_DEPOSIT") >= 4

@pytest.mark.anyio
async def test_forex_rates_returns_major_currencies(client, auth_headers):
    response = await client.get("/rates/forex", headers=auth_headers)
    assert response.status_code == 200
    currencies = [rate["currency"] for rate in response.json()["rates"]]
    assert "USD" in currencies
    assert "EUR" in currencies
    assert "GBP" in currencies

@pytest.mark.anyio
async def test_loan_facility_returns_correct_schema(client, auth_headers):
    response = await client.get("/loans/LN-2024-0087", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "facilityId" in data
    assert "facilityType" in data
    assert "outstandingPrincipal" in data
    assert "interestRate" in data

@pytest.mark.anyio
async def test_loan_facility_unknown_id_returns_404(client, auth_headers):
    response = await client.get("/loans/NONEXISTENT", headers=auth_headers)
    assert response.status_code == 404

@pytest.mark.anyio
async def test_transaction_lookup_not_found_for_unknown_ref(client, auth_headers):
    response = await client.get("/transactions?refId=NONEXISTENT", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["found"] is False

@pytest.mark.anyio
async def test_write_failure_simulation_returns_500(client, auth_headers):
    payload = {
        "sourceAccountId": "SAMP-0012345678",
        "beneficiaryAccount": "COMB-0098765432",
        "amount": "100.00",
        "currency": "LKR",
        "purpose": "TEST",
        "requestedExecutionDate": date.today().isoformat()
    }
    sig = get_signature(payload)
    headers = {**auth_headers, "X-Signature": sig}
    response = await client.post("/payments/initiate?simulate=write_failure", json=payload, headers=headers)
    assert response.status_code == 500
    assert response.json()["error"] == "UPSTREAM_GATEWAY_TIMEOUT"
