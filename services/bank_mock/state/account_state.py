from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional, List

@dataclass
class AccountState:
    account_id: str
    currency: str
    account_type: str
    available_balance: Decimal
    book_balance: Decimal
    average_balance: Decimal
    average_period_days: int
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

# Singleton — initialised from seed.py at startup
AccountStore: Dict[str, AccountState] = {}

def debit_account(account_id: str, amount: Decimal) -> AccountState:
    """Called by payment initiation. Raises ValueError if insufficient funds."""
    state = AccountStore[account_id]
    if state.available_balance < amount:
        raise ValueError("INSUFFICIENT_FUNDS")
    state.available_balance -= amount
    state.book_balance -= amount
    state.last_updated = datetime.now(timezone.utc)
    return state

@dataclass
class PaymentRecord:
    payment_id: str
    status: str
    amount: Decimal
    currency: str
    beneficiary_account: str
    purpose: str
    requested_execution_date: str
    submitted_at: datetime
    executed_at: Optional[datetime] = None

PaymentStore: Dict[str, PaymentRecord] = {}

@dataclass
class TransactionRecord:
    transaction_id: str
    reference_id: Optional[str]
    date: str
    amount: Decimal
    direction: str
    account_id: str
    description: str

TransactionStore: List[TransactionRecord] = []
