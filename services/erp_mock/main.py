"""
Mock SAP ERP — entrypoint.

Exposes SAP-shaped OData v2 endpoints at real SAP URL conventions:
  /sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner
  /sap/opu/odata/sap/API_ACCOUNTINGDOCUMENTITEM_SRV/A_APAccountingDocumentItem
  /sap/opu/odata/sap/ZAPI_PAYROLL_POSTING_SRV/A_PayrollPosting
  /sap/opu/odata/sap/ZAPI_TAX_LIABILITY_SRV/A_TaxLiabilityItem
  /sap/opu/odata/sap/ZAPI_LOAN_SCHEDULE_SRV/A_LoanContractItem
  /sap/opu/odata/sap/ZAPI_CASH_POSITION_SRV/A_CashPosition
  /sap/opu/odata/sap/$metadata

Run: uvicorn main:app --reload --port 8001
"""

from datetime import date
from fastapi import FastAPI, Request, Response, Header, Depends

from services.erp_mock.data.seed import generate_all
from services.erp_mock.odata.core import query_entity_set, envelope_single
from services.erp_mock.odata.metadata import build_metadata_document, maybe_issue_csrf_token, validate_csrf_token

app = FastAPI(
    title="Mock SAP ERP (Treasury Copilot Agent)",
    description="SAP-shaped OData v2 mock service for treasury/cash-relevant entities.",
)

# In-memory dataset, generated once at startup
DB = generate_all(fy_start=date(2026, 4, 1))

SERVICE_BASE = "/sap/opu/odata/sap"

METADATA_SERVICES = {
    "API_BUSINESS_PARTNER": {
        "entity_type": "A_BusinessPartner",
        "key": ["BusinessPartner"],
        "properties": {
            "BusinessPartner": "Edm.String", "BusinessPartnerName": "Edm.String",
            "BusinessPartnerIsVendor": "Edm.Boolean", "CreationDate": "Edm.DateTime",
        },
    },
    "API_ACCOUNTINGDOCUMENTITEM_SRV": {
        "entity_type": "A_APAccountingDocumentItem",
        "key": ["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"],
        "properties": {
            "CompanyCode": "Edm.String", "Vendor": "Edm.String",
            "AmountInCompanyCodeCurrency": "Edm.Decimal", "NetDueDate": "Edm.DateTime",
            "PaymentPriority": "Edm.String",
        },
    },
    "ZAPI_PAYROLL_POSTING_SRV": {
        "entity_type": "A_PayrollPosting",
        "key": ["CompanyCode", "FiscalYear", "AccountingDocument"],
        "properties": {
            "PayrollArea": "Edm.String", "TotalNetPayable": "Edm.Decimal",
            "PaymentDueDate": "Edm.DateTime", "PaymentPriority": "Edm.String",
        },
    },
    "ZAPI_TAX_LIABILITY_SRV": {
        "entity_type": "A_TaxLiabilityItem",
        "key": ["CompanyCode", "FiscalYear", "AccountingDocument"],
        "properties": {
            "TaxType": "Edm.String", "AmountInCompanyCodeCurrency": "Edm.Decimal",
            "StatutoryDueDate": "Edm.DateTime", "PaymentPriority": "Edm.String",
        },
    },
    "ZAPI_LOAN_SCHEDULE_SRV": {
        "entity_type": "A_LoanContractItem",
        "key": ["CompanyCode", "LoanContract", "InstallmentNumber"],
        "properties": {
            "LoanContract": "Edm.String", "TotalInstallmentAmount": "Edm.Decimal",
            "InstallmentDueDate": "Edm.DateTime", "CovenantFlag": "Edm.Boolean",
        },
    },
    "ZAPI_CASH_POSITION_SRV": {
        "entity_type": "A_CashPosition",
        "key": ["CompanyCode", "BankAccount"],
        "properties": {
            "BankAccount": "Edm.String", "AvailableBalance": "Edm.Decimal",
            "AccountType": "Edm.String",
        },
    },
}


@app.get(f"{SERVICE_BASE}/$metadata")
def get_metadata():
    return build_metadata_document(METADATA_SERVICES)


@app.get(f"{SERVICE_BASE}/API_BUSINESS_PARTNER/A_BusinessPartner")
def get_business_partners(request: Request, response: Response, x_csrf_token: str | None = Header(None)):
    maybe_issue_csrf_token(response, x_csrf_token)
    return query_entity_set(DB["business_partners"], request, "A_BusinessPartner")


@app.get(f"{SERVICE_BASE}/API_ACCOUNTINGDOCUMENTITEM_SRV/A_APAccountingDocumentItem")
def get_ap_documents(request: Request, response: Response, x_csrf_token: str | None = Header(None)):
    maybe_issue_csrf_token(response, x_csrf_token)
    return query_entity_set(DB["ap_documents"], request, "A_APAccountingDocumentItem")


@app.get(f"{SERVICE_BASE}/ZAPI_PAYROLL_POSTING_SRV/A_PayrollPosting")
def get_payroll_postings(request: Request, response: Response, x_csrf_token: str | None = Header(None)):
    maybe_issue_csrf_token(response, x_csrf_token)
    return query_entity_set(DB["payroll_postings"], request, "A_PayrollPosting")


@app.get(f"{SERVICE_BASE}/ZAPI_TAX_LIABILITY_SRV/A_TaxLiabilityItem")
def get_tax_items(request: Request, response: Response, x_csrf_token: str | None = Header(None)):
    maybe_issue_csrf_token(response, x_csrf_token)
    return query_entity_set(DB["tax_items"], request, "A_TaxLiabilityItem")


@app.get(f"{SERVICE_BASE}/ZAPI_LOAN_SCHEDULE_SRV/A_LoanContractItem")
def get_loan_items(request: Request, response: Response, x_csrf_token: str | None = Header(None)):
    maybe_issue_csrf_token(response, x_csrf_token)
    return query_entity_set(DB["loan_items"], request, "A_LoanContractItem")


@app.get(f"{SERVICE_BASE}/ZAPI_CASH_POSITION_SRV/A_CashPosition")
def get_cash_positions(request: Request, response: Response, x_csrf_token: str | None = Header(None)):
    maybe_issue_csrf_token(response, x_csrf_token)
    return query_entity_set(DB["cash_positions"], request, "A_CashPosition")


@app.get("/health")
def health():
    return {"status": "ok", "entities_loaded": {k: len(v) for k, v in DB.items()}}
