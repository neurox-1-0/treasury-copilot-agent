from pydantic import BaseModel
from typing import List, Optional
from datetime import date
from decimal import Decimal

class Instrument(BaseModel):
    bank: str
    type: str
    termDays: int
    rate: float

class OptimizationRequest(BaseModel):
    availableSurplus: Decimal
    minimumBufferRequired: Decimal
    currentTotalBalance: Decimal
    asOfDate: date
    nextFixedObligationDate: Optional[date] = None
    nextFixedObligationAmount: Optional[Decimal] = None
    costOfDebt: Optional[float] = None
    instruments: List[Instrument]

class Allocation(BaseModel):
    bank: str
    instrument: str
    termDays: int
    amount: Decimal
    maturityDate: date
    expectedYield: Decimal
    yieldRate: float

class RejectedAlternative(BaseModel):
    bank: str
    instrument: str
    termDays: int
    amount: Decimal
    maturityDate: date
    expectedYield: Decimal
    yieldRate: float
    rejectedReason: str

class OptimizationResult(BaseModel):
    recommendedAllocation: List[Allocation]
    alternativesConsidered: List[RejectedAlternative]
    constraintsSatisfied: bool
    infeasibilityReason: Optional[str] = None
    costOfDebtHurdleBreached: bool = False
    hurdleNote: Optional[str] = None
    solverUsed: str
    bufferAfterDeployment: Decimal
