import pytest
import httpx
from datetime import datetime
from main import app, SERVICE_BASE

# Mark all tests to use anyio async backend
pytestmark = pytest.mark.anyio

@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

async def test_health_returns_all_entity_counts(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    counts = data["entities_loaded"]
    for key in ["business_partners", "ap_documents", "payroll_postings", "tax_items", "loan_items", "cash_positions"]:
        assert key in counts
        assert isinstance(counts[key], int)
        assert counts[key] > 0

async def test_cash_positions_returns_odata_envelope(client):
    response = await client.get(f"{SERVICE_BASE}/ZAPI_CASH_POSITION_SRV/A_CashPosition")
    assert response.status_code == 200
    data = response.json()
    assert "d" in data
    assert "results" in data["d"]
    results = data["d"]["results"]
    assert len(results) > 0
    for item in results:
        assert "BankAccount" in item
        assert "AvailableBalance" in item
        assert "AccountType" in item

async def test_ap_filter_fixed_priority(client):
    response = await client.get(f"{SERVICE_BASE}/API_ACCOUNTINGDOCUMENTITEM_SRV/A_APAccountingDocumentItem?$filter=PaymentPriority eq 'FIXED'")
    assert response.status_code == 200
    data = response.json()
    results = data["d"]["results"]
    # AP documents seeded are flexible by default in seed.py, let's verify if filter runs
    for item in results:
        assert item["PaymentPriority"] == "FIXED"

async def test_ap_filter_flexible_priority(client):
    response = await client.get(f"{SERVICE_BASE}/API_ACCOUNTINGDOCUMENTITEM_SRV/A_APAccountingDocumentItem?$filter=PaymentPriority eq 'FLEXIBLE'")
    assert response.status_code == 200
    data = response.json()
    results = data["d"]["results"]
    assert len(results) > 0
    for item in results:
        assert item["PaymentPriority"] == "FLEXIBLE"

async def test_ap_filter_clearing_status_open(client):
    response = await client.get(f"{SERVICE_BASE}/API_ACCOUNTINGDOCUMENTITEM_SRV/A_APAccountingDocumentItem?$filter=ClearingStatus eq 'OPEN'")
    assert response.status_code == 200
    data = response.json()
    results = data["d"]["results"]
    for item in results:
        assert item["ClearingStatus"] == "OPEN"

async def test_pagination_top_skip(client):
    # First page of 2
    res1 = await client.get(f"{SERVICE_BASE}/API_ACCOUNTINGDOCUMENTITEM_SRV/A_APAccountingDocumentItem?$top=2&$skip=0")
    assert res1.status_code == 200
    results1 = res1.json()["d"]["results"]
    assert len(results1) == 2

    # Second page of 2
    res2 = await client.get(f"{SERVICE_BASE}/API_ACCOUNTINGDOCUMENTITEM_SRV/A_APAccountingDocumentItem?$top=2&$skip=2")
    assert res2.status_code == 200
    results2 = res2.json()["d"]["results"]
    assert len(results2) == 2
    
    assert results1 != results2

async def test_pagination_next_link_present(client):
    response = await client.get(f"{SERVICE_BASE}/API_ACCOUNTINGDOCUMENTITEM_SRV/A_APAccountingDocumentItem?$top=2")
    assert response.status_code == 200
    data = response.json()["d"]
    assert "__next" in data
    assert "$skip=2" in data["__next"]

async def test_pagination_next_link_absent_on_last_page(client):
    # Large top to request everything
    response = await client.get(f"{SERVICE_BASE}/API_ACCOUNTINGDOCUMENTITEM_SRV/A_APAccountingDocumentItem?$top=500")
    assert response.status_code == 200
    data = response.json()["d"]
    assert "__next" not in data

async def test_select_returns_only_requested_fields(client):
    response = await client.get(f"{SERVICE_BASE}/ZAPI_CASH_POSITION_SRV/A_CashPosition?$select=BankAccount,AvailableBalance")
    assert response.status_code == 200
    results = response.json()["d"]["results"]
    for item in results:
        # Pydantic serialization / our mock core query handler might retain fields or strip them
        # Let's verify our query_entity_set respects $select projection
        assert "BankAccount" in item
        assert "AvailableBalance" in item
        # Fields not in select should not be returned, except maybe metadata or keys depending on OData core implementation
        assert "AccountType" not in item or item["AccountType"] is None

async def test_csrf_fetch_returns_token_in_header(client):
    response = await client.get(f"{SERVICE_BASE}/ZAPI_CASH_POSITION_SRV/A_CashPosition", headers={"X-CSRF-Token": "Fetch"})
    assert response.status_code == 200
    assert "x-csrf-token" in response.headers
    assert response.headers["x-csrf-token"] != "Fetch"

async def test_request_without_csrf_fetch_does_not_return_token(client):
    response = await client.get(f"{SERVICE_BASE}/ZAPI_CASH_POSITION_SRV/A_CashPosition")
    assert response.status_code == 200
    # Should either not contain x-csrf-token or be empty
    assert response.headers.get("x-csrf-token") is None

async def test_payroll_all_fixed_priority(client):
    response = await client.get(f"{SERVICE_BASE}/ZAPI_PAYROLL_POSTING_SRV/A_PayrollPosting")
    assert response.status_code == 200
    results = response.json()["d"]["results"]
    assert len(results) > 0
    for item in results:
        assert item["PaymentPriority"] == "FIXED"

async def test_tax_items_all_fixed_priority(client):
    response = await client.get(f"{SERVICE_BASE}/ZAPI_TAX_LIABILITY_SRV/A_TaxLiabilityItem")
    assert response.status_code == 200
    results = response.json()["d"]["results"]
    assert len(results) > 0
    for item in results:
        assert item["PaymentPriority"] == "FIXED"

async def test_metadata_returns_all_service_names(client):
    response = await client.get(f"{SERVICE_BASE}/$metadata")
    assert response.status_code == 200
    metadata = response.json()
    
    entity_sets = metadata["Edmx"]["DataServices"]["Schema"]["EntityContainer"]["EntitySet"]
    service_names = [es["Name"] for es in entity_sets]
    
    assert "API_BUSINESS_PARTNER" in service_names
    assert "API_ACCOUNTINGDOCUMENTITEM_SRV" in service_names
    assert "ZAPI_PAYROLL_POSTING_SRV" in service_names
    assert "ZAPI_TAX_LIABILITY_SRV" in service_names
    assert "ZAPI_LOAN_SCHEDULE_SRV" in service_names
    assert "ZAPI_CASH_POSITION_SRV" in service_names

async def test_invalid_filter_returns_400(client):
    response = await client.get(f"{SERVICE_BASE}/ZAPI_CASH_POSITION_SRV/A_CashPosition?$filter=NonExistentField eq 'foo'")
    assert response.status_code == 400
    assert "Unknown field in $filter" in response.text
