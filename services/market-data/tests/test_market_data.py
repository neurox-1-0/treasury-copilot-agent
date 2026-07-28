import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add services/market-data to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from rate_source import RateQuote, InstrumentType, RateSourceError, RateEntrySource

from rate_store import RateStore
from models import compute_best_available_rates
from parsers.boc_parser import parse_boc_page
from parsers.seylan_parser import parse_seylan_page
from parsers.ndb_parser import parse_ndb_page
from parsers.cbsl_parser import parse_cbsl_homepage
from main import app, store


@pytest.fixture
def test_store(tmp_path):
    store_file = tmp_path / "test_rates.json"
    return RateStore(path=store_file)


# ---------------------------------------------------------------------------
# 1. Output Contract & FastAPI Endpoints
# ---------------------------------------------------------------------------

def test_health_check_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "market-data"
    assert "active_sources" in data


def test_get_rates_endpoint_contract():
    client = TestClient(app)
    response = client.get("/rates")
    assert response.status_code == 200
    data = response.json()
    assert "asOfTimestamp" in data
    assert "bankRates" in data
    assert "cbsl" in data
    assert "bestAvailableRates" in data
    assert "stalenessBySource" in data


def test_manual_rate_entry_endpoint():
    client = TestClient(app)
    payload = {
        "source": "COMBANK",
        "instrumentType": "FIXED_DEPOSIT",
        "termDays": 30,
        "rate": 0.115,
        "currency": "LKR",
        "enteredBy": "analyst_test",
    }
    response = client.post("/rates/manual", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verify rates endpoint includes Combank manual rate
    r2 = client.get("/rates")
    assert r2.status_code == 200
    rates_data = r2.json()
    assert "combank" in rates_data["bankRates"]


# ---------------------------------------------------------------------------
# 2. Best Available Rates Computation
# ---------------------------------------------------------------------------

def test_compute_best_available_rates():
    quotes = [
        {"source": "SAMPATH", "instrumentType": "FIXED_DEPOSIT", "termDays": 30, "rate": 0.100},
        {"source": "BOC", "instrumentType": "FIXED_DEPOSIT", "termDays": 30, "rate": 0.115},
        {"source": "SEYLAN", "instrumentType": "FIXED_DEPOSIT", "termDays": 30, "rate": 0.110},
        {"source": "SAMPATH", "instrumentType": "CALL_DEPOSIT", "termDays": 1, "rate": 0.080},
        {"source": "SEYLAN", "instrumentType": "CALL_DEPOSIT", "termDays": 1, "rate": 0.087},
    ]

    best = compute_best_available_rates(quotes)
    assert "fd_30d" in best
    assert best["fd_30d"].rate == 0.115
    assert best["fd_30d"].bank == "BOC"

    assert "callDeposit" in best
    assert best["callDeposit"].rate == 0.087
    assert best["callDeposit"].bank == "SEYLAN"


# ---------------------------------------------------------------------------
# 3. RateStore Unit Tests
# ---------------------------------------------------------------------------

def test_rate_store_manual_entry_overwrite(test_store):
    q1 = RateQuote(
        source="TESTBANK",
        instrumentType=InstrumentType.FIXED_DEPOSIT,
        termDays=30,
        rate=0.10,
        entryMethod=RateEntrySource.MANUAL,
        enteredBy="user1",
    )
    test_store.add_manual_quote(q1, "user1")

    latest = test_store.get_latest_quotes()
    assert len(latest["quotes"]) == 1
    assert latest["quotes"][0]["rate"] == 0.10

    # Overwrite with new rate
    q2 = RateQuote(
        source="TESTBANK",
        instrumentType=InstrumentType.FIXED_DEPOSIT,
        termDays=30,
        rate=0.12,
        entryMethod=RateEntrySource.MANUAL,
        enteredBy="user2",
    )
    test_store.add_manual_quote(q2, "user2")

    latest2 = test_store.get_latest_quotes()
    assert len(latest2["quotes"]) == 1
    assert latest2["quotes"][0]["rate"] == 0.12


# ---------------------------------------------------------------------------
# 4. Bank Parsers Unit Tests (Synthetic HTML Fixtures)
# ---------------------------------------------------------------------------

def test_parse_boc_page():
    synthetic_html = """
    <html>
    <body>
        <h2>Rupee Fixed Deposits</h2>
        <p>W.E.F 01.07.2026</p>
        <table>
            <tr><th>Term</th><th>Rate %</th></tr>
            <tr><td>1 Month</td><td>10.50%</td></tr>
            <tr><td>3 Months</td><td>11.50%</td></tr>
        </table>
    </body>
    </html>
    """
    quotes = parse_boc_page(synthetic_html)
    assert len(quotes) == 2
    assert quotes[0].termDays == 30
    assert quotes[0].rate == 0.105
    assert quotes[0].source == "BOC"


def test_parse_seylan_page():
    synthetic_html = """
    <html>
    <body>
        <h3>Fixed Deposits - Interest Paid At Maturity</h3>
        <p>w.e.f. 01.07.2026</p>
        <table>
            <tr><th>Term</th><th>Rate</th></tr>
            <tr><td>1 Month</td><td>11.25%</td></tr>
        </table>
        <h3>Call Deposit</h3>
        <table>
            <tr><th>Tier</th><th>Rate</th></tr>
            <tr><td>Above LKR 1,000,000</td><td>8.50%</td></tr>
        </table>
    </body>
    </html>
    """
    quotes = parse_seylan_page(synthetic_html)
    assert len(quotes) == 2
    fd_q = [q for q in quotes if q.instrumentType == InstrumentType.FIXED_DEPOSIT][0]
    assert fd_q.termDays == 30
    assert fd_q.rate == 0.1125


def test_parse_ndb_page():
    synthetic_html = """
    <html>
    <body>
        <h3>Fixed Deposits</h3>
        <p>Last Updated On: 2026-07-01</p>
        <table>
            <tr><th>Term</th><th>Subtype</th><th>Rate %</th></tr>
            <tr><td>1 Month</td><td>Maturity</td><td>10.80%</td></tr>
        </table>
    </body>
    </html>
    """
    quotes = parse_ndb_page(synthetic_html)
    assert len(quotes) == 1
    assert quotes[0].termDays == 30
    assert quotes[0].rate == pytest.approx(0.108)


def test_parse_cbsl_homepage():
    synthetic_html = """
    <html>
    <body>
        <div>Overnight Policy Rate - 8.75 %</div>
        <div>Inflation - 6.80 % (CCPI)</div>
        <div>USD/LKR - TT Buy 331.6265, TT Sell 340.7938</div>
    </body>
    </html>
    """
    quotes = parse_cbsl_homepage(synthetic_html)
    assert len(quotes) == 3

    policy_q = [q for q in quotes if q.instrumentType == InstrumentType.POLICY_RATE][0]
    assert policy_q.rate == 0.0875

    inf_q = [q for q in quotes if q.instrumentType == InstrumentType.INFLATION][0]
    assert inf_q.rate == 0.068

    forex_q = [q for q in quotes if q.instrumentType == InstrumentType.FOREX][0]
    assert forex_q.currency == "USD"
    assert forex_q.rate == 331.6265
    assert forex_q.metadata["sell"] == "340.7938"
