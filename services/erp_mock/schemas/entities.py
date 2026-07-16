"""
Mock SAP OData entity schemas for Treasury Copilot Agent.

Field names and structural conventions mirror real SAP S/4HANA OData v2
services (API_BUSINESS_PARTNER, API_ACCOUNTINGDOCUMENTITEM_SRV,
API_JOURNALENTRY_SRV) plus custom Z-prefixed services for functionality
SAP doesn't standardly expose (payroll postings, tax liabilities, loan
schedules, cash position) — consistent with how real enterprises extend
SAP with custom objects.

Conventions followed:
- PascalCase field names (SAP standard)
- Dual-currency amount fields (DocumentCurrency vs CompanyCodeCurrency)
- SAP date format: /Date(epoch_ms)/ via `to_sap_date()` helper
- Company code + fiscal year + document number as composite keys (real SAP FI keying)
- Two-character SAP document type codes (KR, KZ, etc.)
"""

from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ── Shared helpers ──────────────────────────────────────────────

def to_sap_date(d: date | datetime) -> str:
    """Convert a Python date to SAP's OData v2 /Date(epoch_ms)/ format."""
    if isinstance(d, date) and not isinstance(d, datetime):
        d = datetime.combine(d, datetime.min.time())
    epoch_ms = int(d.timestamp() * 1000)
    return f"/Date({epoch_ms})/"


class ODataEnvelope(BaseModel):
    """Wraps any entity set response in the real OData v2 { d: { results: [...] } } shape."""
    d: dict


# ── Document type codes (real SAP FI convention) ───────────────

class SAPDocumentType(str, Enum):
    VENDOR_INVOICE = "KR"       # Rechnung (invoice) - vendor
    VENDOR_PAYMENT = "KZ"       # Zahlung (payment) - vendor
    CUSTOMER_INVOICE = "DR"     # Debit memo / customer invoice
    CUSTOMER_PAYMENT = "DZ"     # Customer payment
    GL_JOURNAL = "SA"           # Sammelbuchung (general journal)
    PAYROLL_POSTING = "PR"      # Payroll run posting (common custom convention)
    TAX_POSTING = "TX"          # Statutory tax posting
    LOAN_POSTING = "LN"         # Loan drawdown/repayment


class PaymentPriority(str, Enum):
    FIXED = "FIXED"         # Legally/contractually locked date - payroll, tax, loan covenants
    FLEXIBLE = "FLEXIBLE"   # Negotiable within vendor terms


# ── 1. Business Partner (API_BUSINESS_PARTNER) ─────────────────

class BusinessPartner(BaseModel):
    BusinessPartner: str = Field(..., description="10-digit SAP BP number, e.g. '1000023456'")
    BusinessPartnerCategory: str = Field(..., description="'1' = person, '2' = organization")
    BusinessPartnerName: str
    BusinessPartnerIsVendor: bool = False
    BusinessPartnerIsCustomer: bool = False
    CreationDate: str  # /Date(...)/  format
    Country: str = "LK"
    PaymentTerms: Optional[str] = Field(None, description="SAP terms key, e.g. 'NT30', 'NT60'")


# ── 2. AP Accounting Document Item (API_ACCOUNTINGDOCUMENTITEM_SRV) ──

class APAccountingDocumentItem(BaseModel):
    CompanyCode: str = Field(..., description="e.g. '1000'")
    FiscalYear: str = Field(..., description="e.g. '2026'")
    AccountingDocument: str = Field(..., description="10-digit FI document number")
    AccountingDocumentItem: str = Field(..., description="Line item number, zero-padded")
    DocumentType: SAPDocumentType = SAPDocumentType.VENDOR_INVOICE
    Vendor: str = Field(..., description="BusinessPartner number of the vendor")
    AmountInDocumentCurrency: Decimal
    DocumentCurrency: str = "USD"
    AmountInCompanyCodeCurrency: Decimal
    CompanyCodeCurrency: str = "LKR"
    NetDueDate: str  # /Date(...)/
    PostingDate: str  # /Date(...)/
    PaymentPriority: PaymentPriority = PaymentPriority.FLEXIBLE
    ClearingStatus: str = Field("OPEN", description="OPEN | CLEARED")


# ── 3. Payroll Posting (custom Z-service, mirrors HCM→FI posting) ──

class PayrollPosting(BaseModel):
    CompanyCode: str
    FiscalYear: str
    AccountingDocument: str
    DocumentType: SAPDocumentType = SAPDocumentType.PAYROLL_POSTING
    PayrollArea: str = Field(..., description="SAP HCM payroll area code, e.g. 'X1' monthly staff")
    PostingPeriod: str = Field(..., description="MM/YYYY")
    TotalGrossAmount: Decimal
    TotalNetPayable: Decimal
    CompanyCodeCurrency: str = "LKR"
    PaymentDueDate: str  # /Date(...)/ - typically fixed, e.g. last working day of month
    PaymentPriority: PaymentPriority = PaymentPriority.FIXED
    EmployeeCount: int


# ── 4. Tax Liability Item (custom Z-service, statutory obligations) ──

class TaxLiabilityItem(BaseModel):
    CompanyCode: str
    FiscalYear: str
    AccountingDocument: str
    DocumentType: SAPDocumentType = SAPDocumentType.TAX_POSTING
    TaxType: str = Field(..., description="VAT | WHT | EPF | ETF | CORP_TAX_INSTALLMENT")
    TaxPeriod: str = Field(..., description="MM/YYYY")
    AmountInCompanyCodeCurrency: Decimal
    CompanyCodeCurrency: str = "LKR"
    StatutoryDueDate: str  # /Date(...)/ - regulator-mandated, cannot be delayed
    PaymentPriority: PaymentPriority = PaymentPriority.FIXED
    RegulatorAuthority: str = Field(..., description="e.g. 'IRD', 'EPF Department'")


# ── 5. Loan Contract Item (custom Z-service, Treasury sub-ledger) ──

class LoanContractItem(BaseModel):
    CompanyCode: str
    LoanContract: str = Field(..., description="Loan contract ID")
    LoanType: str = Field(..., description="TERM_LOAN | REVOLVING_CREDIT | OVERDRAFT")
    Lender: str = Field(..., description="BusinessPartner number of the bank")
    InstallmentNumber: int
    PrincipalAmount: Decimal
    InterestAmount: Decimal
    TotalInstallmentAmount: Decimal
    CompanyCodeCurrency: str = "LKR"
    InstallmentDueDate: str  # /Date(...)/
    PaymentPriority: PaymentPriority = PaymentPriority.FIXED
    CovenantFlag: bool = Field(False, description="True if late payment breaches a loan covenant")


# ── 6. GL Journal Entry Item (API_JOURNALENTRY_SRV) ─────────────

class JournalEntryItem(BaseModel):
    CompanyCode: str
    FiscalYear: str
    AccountingDocument: str
    AccountingDocumentItem: str
    DocumentType: SAPDocumentType = SAPDocumentType.GL_JOURNAL
    GLAccount: str = Field(..., description="10-digit GL account, e.g. '0000113100' = bank")
    AmountInCompanyCodeCurrency: Decimal
    CompanyCodeCurrency: str = "LKR"
    DebitCreditCode: str = Field(..., description="'S' = debit, 'H' = credit (SAP convention)")
    PostingDate: str  # /Date(...)/
    DocumentReferenceID: Optional[str] = None


# ── 7. Cash Position (custom Z-service, cash management view) ──

class CashPosition(BaseModel):
    CompanyCode: str
    BankAccount: str = Field(..., description="Internal bank account ID, e.g. 'SAMP-0012345678'")
    BankKey: str = Field(..., description="Bank routing/SWIFT-like identifier")
    ValueDate: str  # /Date(...)/
    AvailableBalance: Decimal
    BookBalance: Decimal
    CompanyCodeCurrency: str = "LKR"
    AccountType: str = Field(..., description="CURRENT | CALL_DEPOSIT | FIXED_DEPOSIT")
