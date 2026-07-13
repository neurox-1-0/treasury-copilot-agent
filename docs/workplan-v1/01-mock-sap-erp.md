# Component 1: Mock SAP ERP

**Status: Built — needs migration and tests before use.**

> ⚠️ **Action required**: The code currently lives in `docs/sap/`. It must be moved
> to `services/erp-mock/` and restructured per the layout below before any other
> component can depend on it. Do not build the bank mock or agent against the
> `docs/sap/` path — that location is temporary.

---

## Purpose

Simulate a real SAP S/4HANA OData v2 gateway service exposing treasury-relevant
entities, so the agent's Perceive stage queries something structurally identical
to a real enterprise ERP. The goal is realistic SAP URL conventions and response
shapes, not a full ERP simulation.

---

## Migration: From `docs/sap/` to `services/erp-mock/`

```
BEFORE (current, wrong):          AFTER (target):
docs/
  sap/                            services/
    main.py              →          erp-mock/
    entities.py          →            main.py
    core.py              →            schemas/
    metadata.py          →              entities.py
    seed.py              →            odata/
    README.md            →              core.py
                                       metadata.py
                                     data/
                                       seed.py
                                     tests/
                                       test_erp_mock.py   ← NEW
                                     requirements.txt     ← NEW
```

Migration steps:
1. Create `services/erp-mock/` with the subdirectory structure above.
2. Move files and update all `import` paths accordingly (e.g. `from data.seed import generate_all`).
3. Create `requirements.txt` (see below).
4. Verify `uvicorn main:app --reload --port 8001` from `services/erp-mock/` still works.
5. Delete `docs/sap/` only after tests pass.

---

## Canonical Location After Migration

```
services/erp-mock/
├── main.py
├── schemas/
│   └── entities.py
├── odata/
│   ├── core.py
│   └── metadata.py
├── data/
│   └── seed.py
├── tests/
│   └── test_erp_mock.py
└── requirements.txt
```

**`requirements.txt`**:
```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
pydantic>=2.7.0
httpx>=0.27.0    # for test client
pytest>=8.2.0
pytest-anyio>=0.0.0
```

---

## How to Run

```bash
cd services/erp-mock
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

Health check: `GET http://localhost:8001/health` — returns entity counts for
each loaded dataset. Example response:
```json
{
  "status": "ok",
  "entities_loaded": {
    "business_partners": 12,
    "ap_documents": 48,
    "payroll_postings": 4,
    "tax_items": 8,
    "loan_items": 6,
    "cash_positions": 3
  }
}
```

---

## Entities Exposed

All routes are namespaced under `/sap/opu/odata/sap/` matching real SAP gateway
URL conventions. Entity types follow actual SAP service naming.

| Service path | Entity | Real/Custom | Key fields |
|---|---|---|---|
| `/API_BUSINESS_PARTNER/A_BusinessPartner` | Vendors | Real SAP service | `BusinessPartner`, `BusinessPartnerName`, `PaymentTerms` |
| `/API_ACCOUNTINGDOCUMENTITEM_SRV/A_APAccountingDocumentItem` | Vendor payables | Real SAP service | `CompanyCode`, `FiscalYear`, `AccountingDocument`, `NetDueDate`, `PaymentPriority`, `ClearingStatus` |
| `/ZAPI_PAYROLL_POSTING_SRV/A_PayrollPosting` | Payroll runs | Custom (Z-prefix) | `PayrollArea`, `TotalNetPayable`, `PaymentDueDate`, `PaymentPriority` (always `FIXED`) |
| `/ZAPI_TAX_LIABILITY_SRV/A_TaxLiabilityItem` | VAT/WHT/EPF/ETF/corp tax | Custom | `TaxType`, `StatutoryDueDate`, `PaymentPriority` (always `FIXED`) |
| `/ZAPI_LOAN_SCHEDULE_SRV/A_LoanContractItem` | Loan installments | Custom | `LoanContract`, `InstallmentDueDate`, `CovenantFlag`, `PaymentPriority` (always `FIXED`) |
| `/ZAPI_CASH_POSITION_SRV/A_CashPosition` | Bank balances | Custom | `BankAccount`, `AvailableBalance`, `AccountType` |

---

## OData Semantics Supported

- **`$filter`** — single or `and`-joined clauses:
  `?$filter=PaymentPriority eq 'FIXED'`
  `?$filter=ClearingStatus eq 'OPEN' and CompanyCode eq '1000'`
  > Note: `or` and nested grouping are not supported. Add this only if a
  > specific demo scenario requires it.
- **`$select`** — field projection: `?$select=CompanyCode,AmountInCompanyCodeCurrency`
- **`$top` / `$skip`** — pagination; returns a real `__next` cursor link in the
  response body when more pages exist.
- **`$metadata`** — simplified EDMX-style JSON describing entity types, keys, and
  property EDM types. Endpoint: `GET /sap/opu/odata/sap/$metadata`
- **CSRF handshake** — `GET` any endpoint with header `X-CSRF-Token: Fetch`
  returns a token in the response `X-CSRF-Token` header. Required convention
  for any future `POST`/`PATCH` endpoints.

---

## Response Envelope (Real OData v2 Shape)

```json
{
  "d": {
    "results": [
      { "...entity fields..." }
    ],
    "__next": "http://localhost:8001/sap/opu/odata/sap/ZAPI_CASH_POSITION_SRV/A_CashPosition?$skip=20&$top=20"
  }
}
```

`__next` is omitted when the current page is the last page.

---

## `PaymentPriority` Convention

`PaymentPriority` is the single most important field in the system. Every
payable-like entity carries it:

| Value | Entities | Agent behaviour |
|---|---|---|
| `FIXED` | Payroll, all tax types (VAT/WHT/EPF/ETF/corp), loan installments with `CovenantFlag=true` | **Cannot be delayed**. Verify sufficient funds before due date. Surfaced as non-negotiable in Decide stage. |
| `FLEXIBLE` | Vendor AP documents, standard vendor invoices | **Can be delayed** when preserving the liquidity buffer. Agent considers delaying these before recommending a deposit. |

---

## Seed Data Notes for LSTM Forecaster

The seed generator in `data/seed.py` currently produces **point-in-time** payable
snapshots (upcoming obligations only). When building the LSTM forecaster
(Component 3), the seed must be extended to produce a **historical time series**:

- At minimum 90 days of daily `(date, opening_balance, total_inflows,
  total_outflows, closing_balance)` rows.
- Inflows: synthetic customer receipts, randomised around a seasonal pattern.
- Outflows: payables as projected cash exits on their due dates.
- The generator must be deterministic given the same seed (use `random.seed(42)`).

Add a function `generate_historical_series(fy_start, lookback_days=90)` to
`data/seed.py` that returns this time series. The LSTM service will call this
as its training data source.

---

## What Is NOT Built Yet (Extensions)

- `JournalEntryItem` (GL): schema placeholder defined in metadata but **no seed
  data generator exists**. Do not reference this entity until seed data is added.
- No `POST`/`PATCH` endpoints (read-only mock). Execution happens via the bank
  API, not the ERP.
- `$filter` with `or` / nested grouping not supported.
- No multi-company code support (only `1000`).

---

## Interface Contract the Agent Depends On

The Perceive Agent must call these endpoints each cycle:

```python
# 1. Cash positions (bank account balances from ERP perspective)
GET .../ZAPI_CASH_POSITION_SRV/A_CashPosition

# 2. Open vendor payables
GET .../API_ACCOUNTINGDOCUMENTITEM_SRV/A_APAccountingDocumentItem
    ?$filter=ClearingStatus eq 'OPEN'

# 3. Upcoming payroll
GET .../ZAPI_PAYROLL_POSTING_SRV/A_PayrollPosting

# 4. Statutory tax liabilities
GET .../ZAPI_TAX_LIABILITY_SRV/A_TaxLiabilityItem

# 5. Loan schedule
GET .../ZAPI_LOAN_SCHEDULE_SRV/A_LoanContractItem
```

All consuming code treats `PaymentPriority == "FIXED"` as non-negotiable in
timing, and `"FLEXIBLE"` as a candidate for delay when preserving buffer.

---

## Testing Requirements

### Test file: `services/erp-mock/tests/test_erp_mock.py`

Use `pytest` + `httpx.AsyncClient` with `app` as the ASGI transport (no server
needed). All tests should be async using `pytest-anyio`.

#### 1. Health check
```python
async def test_health_returns_all_entity_counts():
    # Assert response has "status": "ok"
    # Assert all 6 entity keys present with integer counts > 0
```

#### 2. Cash position endpoint
```python
async def test_cash_positions_returns_odata_envelope():
    # Assert response shape: {"d": {"results": [...]}}
    # Assert each item has: BankAccount, AvailableBalance, AccountType

async def test_cash_positions_filter_by_company_code():
    # GET ?$filter=CompanyCode eq '1000'
    # Assert all results have CompanyCode == "1000"
```

#### 3. AP documents — filter by PaymentPriority
```python
async def test_ap_filter_fixed_priority():
    # GET ?$filter=PaymentPriority eq 'FIXED'
    # Assert all results have PaymentPriority == "FIXED"

async def test_ap_filter_flexible_priority():
    # GET ?$filter=PaymentPriority eq 'FLEXIBLE'
    # Assert all results have PaymentPriority == "FLEXIBLE"

async def test_ap_filter_clearing_status_open():
    # GET ?$filter=ClearingStatus eq 'OPEN'
    # Assert all results have ClearingStatus == "OPEN"
```

#### 4. Pagination
```python
async def test_pagination_top_skip():
    # GET ?$top=2&$skip=0 → assert results length == 2
    # GET ?$top=2&$skip=2 → assert results are different from first page

async def test_pagination_next_link_present():
    # When total items > $top, assert "__next" key present in "d"
    # When on last page, assert "__next" absent

async def test_pagination_next_link_absent_on_last_page():
    # GET all items (no $top), assert no "__next" in response
```

#### 5. Field projection ($select)
```python
async def test_select_returns_only_requested_fields():
    # GET ?$select=BankAccount,AvailableBalance
    # Assert response items have only those two fields
```

#### 6. CSRF handshake
```python
async def test_csrf_fetch_returns_token_in_header():
    # GET with header X-CSRF-Token: Fetch
    # Assert response header X-CSRF-Token is a non-empty string

async def test_request_without_csrf_fetch_does_not_return_token():
    # GET without X-CSRF-Token header
    # Assert X-CSRF-Token not in response headers
```

#### 7. Payroll — always FIXED
```python
async def test_payroll_all_fixed_priority():
    # GET .../A_PayrollPosting
    # Assert every result has PaymentPriority == "FIXED"
```

#### 8. Tax liabilities — always FIXED
```python
async def test_tax_items_all_fixed_priority():
    # GET .../A_TaxLiabilityItem
    # Assert every result has PaymentPriority == "FIXED"
```

#### 9. Metadata endpoint
```python
async def test_metadata_returns_all_service_names():
    # GET /sap/opu/odata/sap/$metadata
    # Assert all 6 service names present in response

async def test_metadata_has_property_types():
    # Assert each service definition contains "properties" with EDM types
```

#### 10. Invalid filter (graceful)
```python
async def test_invalid_filter_returns_empty_not_500():
    # GET ?$filter=NonExistentField eq 'foo'
    # Assert 200 response with empty results, not a 500
```

### Running Tests

```bash
cd services/erp-mock
pytest tests/ -v
```

Expected: all tests green before declaring Component 1 complete.
