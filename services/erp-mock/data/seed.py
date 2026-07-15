"""
Synthetic seed data generator.

Generates realistic-volume data across a Sri Lankan fiscal year (April-March)
for a fictional mid-market group, covering all 7 entities with plausible
seasonal patterns (e.g. payroll on fixed monthly dates, tax on statutory
deadlines, vendor invoices distributed through the month).
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from schemas.entities import (
    to_sap_date, SAPDocumentType, PaymentPriority,
)

random.seed(42)  # reproducible demo data

COMPANY_CODE = "1000"
CURRENCY = "LKR"

VENDOR_NAMES = [
    "Ceylon Beverage Holdings PLC", "Hayleys Fabric PLC", "Richard Pieris Distributors",
    "Brandix Apparel Solutions", "MAS Holdings Logistics", "Lanka Tiles PLC",
    "Softlogic Retail (Pvt) Ltd", "Singer Sri Lanka PLC", "Dilmah Ceylon Tea Company",
    "Expolanka Freight (Pvt) Ltd",
]

BANK_NAMES = ["Sampath Bank PLC", "Commercial Bank of Ceylon", "Hatton National Bank"]


def _fiscal_dates(fy_start: date, count: int) -> list[date]:
    """Spread `count` dates across a fiscal year (Apr-Mar), business-day biased."""
    days_span = 365
    return sorted(fy_start + timedelta(days=random.randint(0, days_span)) for _ in range(count))


def generate_business_partners(n: int = 10) -> list[dict]:
    partners = []
    for i, name in enumerate(VENDOR_NAMES[:n], start=1):
        partners.append({
            "BusinessPartner": f"10000234{i:02d}",
            "BusinessPartnerCategory": "2",
            "BusinessPartnerName": name,
            "BusinessPartnerIsVendor": True,
            "BusinessPartnerIsCustomer": False,
            "CreationDate": to_sap_date(date(2022, 1, 1)),
            "Country": "LK",
            "PaymentTerms": random.choice(["NT30", "NT45", "NT60"]),
        })
    return partners


def generate_ap_documents(business_partners: list[dict], fy_start: date, n: int = 120) -> list[dict]:
    docs = []
    dates = _fiscal_dates(fy_start, n)
    for i, posting_date in enumerate(dates, start=1):
        vendor = random.choice(business_partners)
        amount = Decimal(random.randrange(50_000, 8_000_000)) / 100
        due_date = posting_date + timedelta(days=random.choice([30, 45, 60]))
        docs.append({
            "CompanyCode": COMPANY_CODE,
            "FiscalYear": str(fy_start.year if posting_date.month >= 4 else fy_start.year + 1),
            "AccountingDocument": f"51{i:08d}",
            "AccountingDocumentItem": "001",
            "DocumentType": SAPDocumentType.VENDOR_INVOICE.value,
            "Vendor": vendor["BusinessPartner"],
            "AmountInDocumentCurrency": str(amount),
            "DocumentCurrency": random.choice(["USD", "LKR"]),
            "AmountInCompanyCodeCurrency": str(amount * Decimal("1") if random.random() > 0.3 else amount * Decimal("310")),
            "CompanyCodeCurrency": CURRENCY,
            "NetDueDate": to_sap_date(due_date),
            "PostingDate": to_sap_date(posting_date),
            "PaymentPriority": PaymentPriority.FLEXIBLE.value,
            "ClearingStatus": "OPEN" if due_date > date.today() else random.choice(["OPEN", "CLEARED"]),
        })
    return docs


def generate_payroll_postings(fy_start: date, months: int = 12) -> list[dict]:
    postings = []
    for m in range(months):
        period_date = fy_start.replace(day=1) + timedelta(days=31 * m)
        period_date = period_date.replace(day=1)
        # last working day approx = 28th for simplicity
        due = period_date.replace(day=28)
        gross = Decimal(random.randrange(18_000_000, 22_000_000))
        net = gross * Decimal("0.82")
        postings.append({
            "CompanyCode": COMPANY_CODE,
            "FiscalYear": str(due.year),
            "AccountingDocument": f"52{m:08d}",
            "DocumentType": SAPDocumentType.PAYROLL_POSTING.value,
            "PayrollArea": "X1",
            "PostingPeriod": due.strftime("%m/%Y"),
            "TotalGrossAmount": str(gross),
            "TotalNetPayable": str(net),
            "CompanyCodeCurrency": CURRENCY,
            "PaymentDueDate": to_sap_date(due),
            "PaymentPriority": PaymentPriority.FIXED.value,
            "EmployeeCount": random.randint(180, 220),
        })
    return postings


def generate_tax_items(fy_start: date, months: int = 12) -> list[dict]:
    items = []
    tax_types = ["VAT", "WHT", "EPF", "ETF", "CORP_TAX_INSTALLMENT"]
    doc_counter = 0
    for m in range(months):
        period_date = (fy_start.replace(day=1) + timedelta(days=31 * m)).replace(day=1)
        for tax_type in tax_types:
            doc_counter += 1
            due = period_date.replace(day=20) + timedelta(days=30)  # statutory ~20th of following month
            amount = Decimal(random.randrange(500_000, 5_000_000))
            items.append({
                "CompanyCode": COMPANY_CODE,
                "FiscalYear": str(due.year),
                "AccountingDocument": f"53{doc_counter:08d}",
                "DocumentType": SAPDocumentType.TAX_POSTING.value,
                "TaxType": tax_type,
                "TaxPeriod": period_date.strftime("%m/%Y"),
                "AmountInCompanyCodeCurrency": str(amount),
                "CompanyCodeCurrency": CURRENCY,
                "StatutoryDueDate": to_sap_date(due),
                "PaymentPriority": PaymentPriority.FIXED.value,
                "RegulatorAuthority": "IRD" if "TAX" in tax_type or tax_type in ("VAT", "WHT") else "EPF Department",
            })
    return items


def generate_loan_items(fy_start: date, n_loans: int = 3, installments_each: int = 12) -> list[dict]:
    items = []
    for loan_idx in range(1, n_loans + 1):
        principal_per = Decimal(random.randrange(2_000_000, 6_000_000))
        interest_per = principal_per * Decimal("0.02")
        for inst in range(1, installments_each + 1):
            due = fy_start + timedelta(days=30 * inst)
            items.append({
                "CompanyCode": COMPANY_CODE,
                "LoanContract": f"LN-{loan_idx:04d}",
                "LoanType": random.choice(["TERM_LOAN", "REVOLVING_CREDIT", "OVERDRAFT"]),
                "Lender": random.choice(BANK_NAMES),
                "InstallmentNumber": inst,
                "PrincipalAmount": str(principal_per),
                "InterestAmount": str(interest_per),
                "TotalInstallmentAmount": str(principal_per + interest_per),
                "CompanyCodeCurrency": CURRENCY,
                "InstallmentDueDate": to_sap_date(due),
                "PaymentPriority": PaymentPriority.FIXED.value,
                "CovenantFlag": inst % 6 == 0,
            })
    return items


def generate_cash_positions() -> list[dict]:
    accounts = [
        ("SAMP-0012345678", "Sampath Bank PLC", "CURRENT", Decimal("42_500_000".replace("_", ""))),
        ("SAMP-0012345679", "Sampath Bank PLC", "CALL_DEPOSIT", Decimal("15_000_000".replace("_", ""))),
        ("COMB-0098765432", "Commercial Bank of Ceylon", "CURRENT", Decimal("18_200_000".replace("_", ""))),
        ("HNB-0055667788", "Hatton National Bank", "FIXED_DEPOSIT", Decimal("25_000_000".replace("_", ""))),
    ]
    today = date.today()
    return [
        {
            "CompanyCode": COMPANY_CODE,
            "BankAccount": acc,
            "BankKey": bank,
            "ValueDate": to_sap_date(today),
            "AvailableBalance": str(bal),
            "BookBalance": str(bal - Decimal(random.randrange(0, 200_000))),
            "CompanyCodeCurrency": CURRENCY,
            "AccountType": acc_type,
        }
        for acc, bank, acc_type, bal in accounts
    ]


def generate_all(fy_start: date | None = None) -> dict[str, list[dict]]:
    fy_start = fy_start or date(date.today().year, 4, 1)
    business_partners = generate_business_partners()
    return {
        "business_partners": business_partners,
        "ap_documents": generate_ap_documents(business_partners, fy_start),
        "payroll_postings": generate_payroll_postings(fy_start),
        "tax_items": generate_tax_items(fy_start),
        "loan_items": generate_loan_items(fy_start),
        "cash_positions": generate_cash_positions(),
    }
