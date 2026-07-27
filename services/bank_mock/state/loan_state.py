from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional

@dataclass
class LoanState:
    facility_id: str
    facility_type: str
    sanctioned_amount: Decimal
    outstanding_principal: Decimal
    interest_rate: float
    rate_type: str
    benchmark_rate: Optional[str]
    spread: Optional[float]
    next_installment_date: str
    next_installment_amount: Decimal
    currency: str

LoanStore: Dict[str, LoanState] = {}
