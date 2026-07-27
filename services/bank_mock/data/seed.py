from decimal import Decimal
from state.account_state import AccountState, AccountStore, TransactionRecord, TransactionStore
from state.loan_state import LoanState, LoanStore

DEPOSIT_RATES = {
    "bank": "SAMPATH",
    "instruments": [
        { "type": "CALL_DEPOSIT", "termDays": 1,   "rate": 0.085 },
        { "type": "FIXED_DEPOSIT", "termDays": 7,  "rate": 0.095 },
        { "type": "FIXED_DEPOSIT", "termDays": 14, "rate": 0.100 },
        { "type": "FIXED_DEPOSIT", "termDays": 30, "rate": 0.110 },
        { "type": "FIXED_DEPOSIT", "termDays": 90, "rate": 0.120 },
        { "type": "FIXED_DEPOSIT", "termDays": 180, "rate": 0.125 },
        { "type": "FIXED_DEPOSIT", "termDays": 365, "rate": 0.130 }
    ],
    "asOfDate": "2026-07-13"
}

FOREX_RATES = {
    "asOfTimestamp": "2026-07-13T09:30:00+05:30",
    "rates": [
        { "currency": "USD", "buyingRate": "300.50", "sellingRate": "310.25", "midRate": "305.38" },
        { "currency": "EUR", "buyingRate": "330.00", "sellingRate": "340.50", "midRate": "335.25" },
        { "currency": "GBP", "buyingRate": "382.00", "sellingRate": "393.75", "midRate": "387.88" },
        { "currency": "SGD", "buyingRate": "222.00", "sellingRate": "229.50", "midRate": "225.75" }
    ]
}

def seed_data():
    AccountStore.clear()
    TransactionStore.clear()
    LoanStore.clear()

    # Accounts
    AccountStore["SAMP-0012345678"] = AccountState(
        account_id="SAMP-0012345678",
        currency="LKR",
        account_type="CURRENT",
        available_balance=Decimal("42500000.00"),
        book_balance=Decimal("42441068.00"),
        average_balance=Decimal("39850000.00"),
        average_period_days=30
    )
    
    AccountStore["SAMP-0012345679"] = AccountState(
        account_id="SAMP-0012345679",
        currency="LKR",
        account_type="CALL_DEPOSIT",
        available_balance=Decimal("15000000.00"),
        book_balance=Decimal("15000000.00"),
        average_balance=Decimal("14500000.00"),
        average_period_days=30
    )

    # Initial Transactions
    TransactionStore.append(TransactionRecord(
        transaction_id="TXN-2026070001",
        reference_id=None,
        date="2026-07-01",
        amount=Decimal("1500000.00"),
        direction="CREDIT",
        account_id="SAMP-0012345678",
        description="Customer receipt — Invoice INV-2026-0045"
    ))

    # Loans
    LoanStore["LN-2024-0087"] = LoanState(
        facility_id="LN-2024-0087",
        facility_type="WORKING_CAPITAL_OD",
        sanctioned_amount=Decimal("50000000.00"),
        outstanding_principal=Decimal("32500000.00"),
        interest_rate=0.1350,
        rate_type="FLOATING",
        benchmark_rate="AWPLR",
        spread=0.0150,
        next_installment_date="2026-08-01",
        next_installment_amount=Decimal("1500000.00"),
        currency="LKR"
    )

    LoanStore["LN-2025-0012"] = LoanState(
        facility_id="LN-2025-0012",
        facility_type="TERM_LOAN",
        sanctioned_amount=Decimal("20000000.00"),
        outstanding_principal=Decimal("18000000.00"),
        interest_rate=0.1375,
        rate_type="FIXED",
        benchmark_rate=None,
        spread=None,
        next_installment_date="2026-09-01",
        next_installment_amount=Decimal("2500000.00"),
        currency="LKR"
    )
