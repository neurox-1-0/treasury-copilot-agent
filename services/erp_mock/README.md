# Mock SAP ERP — Treasury Copilot Agent

A SAP-shaped OData v2 mock service covering the treasury-relevant entities:
vendor payables, payroll postings, statutory tax liabilities, loan
repayments, GL structure, and cash positions.

## Run it

```bash
pip install fastapi uvicorn pydantic --break-system-packages
uvicorn main:app --reload --port 8001
```

## Explore it

- Health check + record counts: `GET /health`
- Metadata document: `GET /sap/opu/odata/sap/$metadata`
- Cash positions: `GET /sap/opu/odata/sap/ZAPI_CASH_POSITION_SRV/A_CashPosition`
- Vendor payables: `GET /sap/opu/odata/sap/API_ACCOUNTINGDOCUMENTITEM_SRV/A_APAccountingDocumentItem`
- Payroll: `GET /sap/opu/odata/sap/ZAPI_PAYROLL_POSTING_SRV/A_PayrollPosting`
- Tax liabilities: `GET /sap/opu/odata/sap/ZAPI_TAX_LIABILITY_SRV/A_TaxLiabilityItem`
- Loan schedule: `GET /sap/opu/odata/sap/ZAPI_LOAN_SCHEDULE_SRV/A_LoanContractItem`
- Vendors: `GET /sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner`

## OData features supported

- `$filter` — e.g. `?$filter=PaymentPriority eq 'FIXED'`
- `$select` — e.g. `?$select=CompanyCode,AmountInCompanyCodeCurrency`
- `$top` / `$skip` — pagination with a real `__next` cursor link in the response
- CSRF handshake — `GET` any endpoint with header `X-CSRF-Token: Fetch` to receive a token in the response headers, required for future POST/PATCH support

## Why this matters for the agent

Every payable-like entity (`APAccountingDocumentItem`, `PayrollPosting`,
`TaxLiabilityItem`, `LoanContractItem`) carries a `PaymentPriority` field:
`FIXED` (payroll, tax, loan covenants — cannot be delayed) vs. `FLEXIBLE`
(vendor terms, negotiable). This is what gives the agent's Decide & Act
stage a genuine judgment call to make when optimizing cash allocation
under a buffer constraint, rather than treating every outflow identically.

## Next steps

- Add `POST`/`PATCH` endpoints (payment execution, using the CSRF flow already in place)
- Swap the in-memory dataset for PostgreSQL if persistence across restarts is needed
- Extend `$filter` parsing to support `or` and nested `(...)` grouping if a demo scenario needs it
