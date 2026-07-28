import pytest
from httpx import AsyncClient, ASGITransport
from datetime import date, timedelta
from decimal import Decimal
import sys
import unittest.mock as mock

from main import app

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.fixture
def test_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

def valid_request_body():
    return {
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

@pytest.mark.anyio
async def test_optimize_returns_correct_schema(test_client):
    response = await test_client.post("/optimize", json=valid_request_body())
    assert response.status_code == 200
    data = response.json()
    assert "recommendedAllocation" in data
    assert "alternativesConsidered" in data
    assert "constraintsSatisfied" in data
    assert "solverUsed" in data
    assert "bufferAfterDeployment" in data
    assert "costOfDebtHurdleBreached" in data

@pytest.mark.anyio
async def test_recommended_allocation_has_bank_field(test_client):
    response = await test_client.post("/optimize", json=valid_request_body())
    data = response.json()
    for alloc in data["recommendedAllocation"]:
        assert "bank" in alloc

@pytest.mark.anyio
async def test_recommended_allocation_has_maturity_date(test_client):
    response = await test_client.post("/optimize", json=valid_request_body())
    data = response.json()
    for alloc in data["recommendedAllocation"]:
        assert "maturityDate" in alloc

@pytest.mark.anyio
async def test_alternatives_have_rejected_reason(test_client):
    response = await test_client.post("/optimize", json=valid_request_body())
    data = response.json()
    for alt in data["alternativesConsidered"]:
        assert "rejectedReason" in alt
        assert alt["rejectedReason"]

@pytest.mark.anyio
async def test_recommended_instrument_matures_before_obligation(test_client):
    body = valid_request_body()
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    ob_date = body["nextFixedObligationDate"]
    for alloc in data["recommendedAllocation"]:
        assert alloc["maturityDate"] <= ob_date

@pytest.mark.anyio
async def test_long_term_instrument_is_in_alternatives_not_recommendation(test_client):
    body = valid_request_body()
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    
    in_rec = any(alloc["termDays"] == 90 for alloc in data["recommendedAllocation"])
    assert not in_rec
    
    in_alt = any(alt["termDays"] == 90 for alt in data["alternativesConsidered"])
    assert in_alt

@pytest.mark.anyio
async def test_buffer_is_preserved(test_client):
    body = valid_request_body()
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    assert float(data["bufferAfterDeployment"]) >= float(body["minimumBufferRequired"])

@pytest.mark.anyio
async def test_allocation_does_not_exceed_surplus(test_client):
    body = valid_request_body()
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    allocated = sum(float(alloc["amount"]) for alloc in data["recommendedAllocation"])
    assert allocated <= float(body["availableSurplus"])

@pytest.mark.anyio
async def test_no_surplus_returns_infeasible(test_client):
    body = valid_request_body()
    body["availableSurplus"] = "0.00"
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    assert data["constraintsSatisfied"] is False
    assert data["infeasibilityReason"]
    assert data["recommendedAllocation"] == []

@pytest.mark.anyio
async def test_all_instruments_unsafe_returns_infeasible(test_client):
    body = valid_request_body()
    body["nextFixedObligationDate"] = "2026-07-14"
    # Only have long term instruments
    body["instruments"] = [
        { "bank": "SAMPATH", "type": "FIXED_DEPOSIT", "termDays": 30, "rate": 0.110 },
        { "bank": "HNB",     "type": "FIXED_DEPOSIT", "termDays": 90, "rate": 0.121 }
    ]
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    assert data["constraintsSatisfied"] is False

@pytest.mark.anyio
async def test_no_instruments_returns_400(test_client):
    body = valid_request_body()
    body["instruments"] = []
    response = await test_client.post("/optimize", json=body)
    assert response.status_code == 400
    assert response.json()["detail"] == "NO_INSTRUMENTS_PROVIDED"

@pytest.mark.anyio
async def test_higher_yield_safe_instrument_is_chosen_over_lower(test_client):
    body = valid_request_body()
    body["instruments"] = [
        { "bank": "A", "type": "FIXED_DEPOSIT", "termDays": 14, "rate": 0.100 },
        { "bank": "B", "type": "FIXED_DEPOSIT", "termDays": 14, "rate": 0.080 }
    ]
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    assert data["recommendedAllocation"][0]["bank"] == "A"

@pytest.mark.anyio
async def test_unsafe_high_yield_not_chosen_despite_better_yield(test_client):
    body = valid_request_body()
    body["instruments"] = [
        { "bank": "SAFE", "type": "FIXED_DEPOSIT", "termDays": 14, "rate": 0.080 },
        { "bank": "UNSAFE", "type": "FIXED_DEPOSIT", "termDays": 90, "rate": 0.120 }
    ]
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    assert data["recommendedAllocation"][0]["bank"] == "SAFE"
    unsafe_alt = next((alt for alt in data["alternativesConsidered"] if alt["bank"] == "UNSAFE"), None)
    assert unsafe_alt is not None

@pytest.mark.anyio
async def test_greedy_fallback_produces_valid_output(test_client):
    # We mock scipy.optimize.linprog inside solver module
    body = valid_request_body()
    with mock.patch("solver.SCIPY_AVAILABLE", False):
        import solver
        from schemas import OptimizationRequest
        res = solver.optimize_allocation(OptimizationRequest(**body))
        assert res.solverUsed == "GREEDY_FALLBACK"
        assert res.recommendedAllocation[0].bank == "HNB" # the best safe in valid_request_body is 14 days, 10.2%
        
@pytest.mark.anyio
async def test_greedy_fallback_respects_maturity_constraint(test_client):
    body = valid_request_body()
    with mock.patch("solver.SCIPY_AVAILABLE", False):
        import solver
        from schemas import OptimizationRequest
        res = solver.optimize_allocation(OptimizationRequest(**body))
        
        ob_date = date.fromisoformat(body["nextFixedObligationDate"])
        for alloc in res.recommendedAllocation:
            assert alloc.maturityDate <= ob_date

@pytest.mark.anyio
async def test_greedy_fallback_respects_bank_field(test_client):
    body = valid_request_body()
    with mock.patch("solver.SCIPY_AVAILABLE", False):
        import solver
        from schemas import OptimizationRequest
        res = solver.optimize_allocation(OptimizationRequest(**body))
        for alloc in res.recommendedAllocation:
            assert alloc.bank

@pytest.mark.anyio
async def test_no_obligation_date_picks_highest_yield(test_client):
    body = valid_request_body()
    body["nextFixedObligationDate"] = None
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    assert data["recommendedAllocation"][0]["termDays"] == 90 # 12.1% is highest

@pytest.mark.anyio
async def test_cross_bank_higher_yield_selected(test_client):
    body = valid_request_body()
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    assert data["recommendedAllocation"][0]["bank"] == "HNB"
    assert data["recommendedAllocation"][0]["yieldRate"] == 0.102

@pytest.mark.anyio
async def test_same_rate_different_banks_selects_first(test_client):
    body = valid_request_body()
    body["instruments"] = [
        { "bank": "SAMPATH", "type": "FIXED_DEPOSIT", "termDays": 14, "rate": 0.100 },
        { "bank": "COMBANK", "type": "FIXED_DEPOSIT", "termDays": 14, "rate": 0.100 }
    ]
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    assert data["recommendedAllocation"][0]["bank"] == "SAMPATH"

@pytest.mark.anyio
async def test_hurdle_breached_when_best_yield_below_cost_of_debt(test_client):
    body = valid_request_body()
    body["costOfDebt"] = 0.135
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    assert data["costOfDebtHurdleBreached"] is True
    assert data["hurdleNote"]

@pytest.mark.anyio
async def test_hurdle_not_breached_when_instrument_beats_cost_of_debt(test_client):
    body = valid_request_body()
    body["costOfDebt"] = 0.09
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    assert data["costOfDebtHurdleBreached"] is False

@pytest.mark.anyio
async def test_null_cost_of_debt_skips_hurdle_check(test_client):
    body = valid_request_body()
    body["costOfDebt"] = None
    response = await test_client.post("/optimize", json=body)
    data = response.json()
    assert data["costOfDebtHurdleBreached"] is False
    assert data["hurdleNote"] is None
