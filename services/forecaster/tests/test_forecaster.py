"""
services/forecaster/tests/test_forecaster.py
=============================================

Unit tests for Component 3: Forecaster Service.
"""

import pytest
from datetime import date, timedelta
from fastapi.testclient import TestClient

from services.forecaster.main import app
from services.forecaster.model.stub import StubForecaster


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_stub_forecaster_schema_and_horizon():
    stub = StubForecaster()
    today = date.today()
    res = stub.predict(
        company_code="1000",
        horizon_days=14,
        historical_cash_flows=[1000000.0] * 30,
        start_date=today,
    )

    assert res["companyCode"] == "1000"
    assert res["forecastHorizonDays"] == 14
    assert res["modelType"] == "STUB_TRAILING_AVERAGE"
    assert len(res["forecast"]) == 14

    # Monotonic confidence check & sequential dates
    confidence_scores = [d["dayConfidenceScore"] for d in res["forecast"]]
    assert confidence_scores == sorted(confidence_scores, reverse=True)

    dates = [d["date"] for d in res["forecast"]]
    assert len(dates) == len(set(dates))  # No duplicates

    # Low confidence flag for 14 days
    assert "LOW_CONFIDENCE_BEYOND_DAY_10" in res["flags"]
    assert 0.0 <= res["overallConfidenceScore"] <= 1.0

    # Bounds check
    for day in res["forecast"]:
        low = float(day["confidenceLow"])
        pred = float(day["predictedNetCashFlow"])
        high = float(day["confidenceHigh"])
        assert low <= pred <= high


def test_forecaster_endpoint_happy_path(client):
    res = client.post("/forecast", json={"companyCode": "1000", "horizonDays": 14})
    assert res.status_code == 200
    data = res.json()
    assert data["companyCode"] == "1000"
    assert len(data["forecast"]) == 14
    assert "modelType" in data


def test_forecaster_endpoint_invalid_company(client):
    res = client.post("/forecast", json={"companyCode": "INVALID", "horizonDays": 14})
    assert res.status_code == 404


def test_forecaster_endpoint_invalid_horizon_zero(client):
    res = client.post("/forecast", json={"companyCode": "1000", "horizonDays": 0})
    assert res.status_code == 400


def test_forecaster_endpoint_invalid_horizon_exceeds_max(client):
    res = client.post("/forecast", json={"companyCode": "1000", "horizonDays": 400})
    assert res.status_code == 400
