from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date, datetime
from decimal import Decimal

class TokenRequest(BaseModel):
    client_id: str
    client_secret: str
    grant_type: str

class TokenResponse(BaseModel):
    access_token: str
    expires_in: int
    token_type: str = "Bearer"

class AccountInfo(BaseModel):
    accountId: str
    currency: str
    accountType: str

class AccountListResponse(BaseModel):
    accounts: List[AccountInfo]

class AccountBalance(BaseModel):
    accountId: str
    currency: str
    availableBalance: str
    bookBalance: str
    averageBalance: str
    averagePeriodDays: int
    asOfTimestamp: datetime

class Transaction(BaseModel):
    transactionId: str
    referenceId: Optional[str]
    date: date
    amount: str
    direction: str
    description: str

class StatementResponse(BaseModel):
    accountId: str
    fromDate: date
    toDate: date
    transactions: List[Transaction]

class PaymentInitiateRequest(BaseModel):
    sourceAccountId: str
    beneficiaryAccount: str
    amount: str
    currency: str
    purpose: str
    requestedExecutionDate: date
    referenceNote: Optional[str] = None

class PaymentInitiateResponse(BaseModel):
    paymentId: str
    status: str
    amount: str
    currency: str
    beneficiaryAccount: str
    purpose: str
    requestedExecutionDate: date
    submittedAt: datetime

class PaymentStatusResponse(BaseModel):
    paymentId: str
    status: str
    executedAt: Optional[datetime] = None

class InstrumentRate(BaseModel):
    type: str
    termDays: int
    rate: float

class DepositRatesResponse(BaseModel):
    bank: str
    instruments: List[InstrumentRate]
    asOfDate: date

class ForexRate(BaseModel):
    currency: str
    buyingRate: str
    sellingRate: str
    midRate: str

class ForexRatesResponse(BaseModel):
    asOfTimestamp: datetime
    rates: List[ForexRate]

class LoanFacility(BaseModel):
    facilityId: str
    facilityType: str
    sanctionedAmount: str
    outstandingPrincipal: str
    interestRate: float
    rateType: str
    benchmarkRate: Optional[str] = None
    spread: Optional[float] = None
    nextInstallmentDate: date
    nextInstallmentAmount: str
    currency: str

class TransactionLookupResult(BaseModel):
    transactionId: str
    referenceId: str
    date: date
    amount: str
    direction: str
    accountId: str
    description: str

class TransactionLookupResponse(BaseModel):
    found: bool
    transaction: Optional[TransactionLookupResult] = None
