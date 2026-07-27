import os
import json
import hmac
import hashlib
import asyncio
from datetime import datetime, timedelta, date, timezone
from decimal import Decimal
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Request, Response, BackgroundTasks, status
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import JSONResponse
from jose import jwt, JWTError
import uvicorn

from schemas.entities import (
    TokenRequest, TokenResponse, AccountListResponse, AccountInfo, AccountBalance, 
    StatementResponse, Transaction as SchemaTransaction, PaymentInitiateRequest, PaymentInitiateResponse,
    PaymentStatusResponse, DepositRatesResponse, ForexRatesResponse, LoanFacility, TransactionLookupResponse, TransactionLookupResult
)
from state.account_state import AccountStore, debit_account, PaymentStore, PaymentRecord, TransactionStore, TransactionRecord
from state.loan_state import LoanStore
from data.seed import seed_data, DEPOSIT_RATES, FOREX_RATES

JWT_SECRET = os.getenv("BANK_MOCK_JWT_SECRET", "dev-secret")
SIGNING_SECRET = os.getenv("PAYMENT_SIGNING_SECRET", "dev-signing-secret")
PAYMENT_AUTO_APPROVE_DELAY = int(os.getenv("PAYMENT_AUTO_APPROVE_DELAY_SECONDS", "5"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")

@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_data()
    task = asyncio.create_task(payment_status_worker())
    yield
    task.cancel()

app = FastAPI(title="Mock Sampath-Style Bank API", lifespan=lifespan)

async def payment_status_worker():
    while True:
        await asyncio.sleep(1)
        now = datetime.now(timezone.utc)
        for pid, record in PaymentStore.items():
            if record.status == "PENDING_APPROVAL":
                if (now - record.submitted_at).total_seconds() >= PAYMENT_AUTO_APPROVE_DELAY:
                    record.status = "APPROVED"
            elif record.status == "APPROVED":
                if (now - record.submitted_at).total_seconds() >= PAYMENT_AUTO_APPROVE_DELAY + 3:
                    record.status = "EXECUTED"
                    record.executed_at = now
                    TransactionStore.append(TransactionRecord(
                        transaction_id=f"TXN-{now.strftime('%Y%m%d%H%M%S')}",
                        reference_id=pid,
                        date=now.date().isoformat(),
                        amount=record.amount,
                        direction="DEBIT",
                        account_id=record.source_account_id,
                        description=f"{record.purpose} — {record.reference_note or ''}"
                    ))

async def simulate_failure(request: Request):
    sim = request.query_params.get("simulate")
    if sim == "timeout":
        await asyncio.sleep(10)
    elif sim == "write_failure" and request.method == "POST" and "initiate" in request.url.path:
        raise HTTPException(status_code=500, detail={"error": "UPSTREAM_GATEWAY_TIMEOUT"})

async def verify_token(token: str = Depends(oauth2_scheme), request: Request = None):
    if request:
        await simulate_failure(request)
        
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail={"error": "TOKEN_EXPIRED", "message": "Re-authenticate"})


@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/auth/token", response_model=TokenResponse)
async def get_token(req: TokenRequest):
    if req.client_id == "treasury-agent" and req.client_secret == "demo-secret-1234":
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        token = jwt.encode({"client_id": req.client_id, "exp": expires}, JWT_SECRET, algorithm="HS256")
        return TokenResponse(access_token=token, expires_in=3600)
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/accounts", response_model=AccountListResponse)
async def list_accounts(token: dict = Depends(verify_token)):
    accounts = [
        AccountInfo(accountId=v.account_id, currency=v.currency, accountType=v.account_type)
        for v in AccountStore.values()
    ]
    return AccountListResponse(accounts=accounts)

@app.get("/accounts/{account_id}/balance", response_model=AccountBalance)
async def get_balance(account_id: str, request: Request, token: dict = Depends(verify_token)):
    await simulate_failure(request)
    state = AccountStore.get(account_id)
    if not state:
        raise HTTPException(status_code=404, detail="Account not found")
    return AccountBalance(
        accountId=state.account_id,
        currency=state.currency,
        availableBalance=f"{state.available_balance:.2f}",
        bookBalance=f"{state.book_balance:.2f}",
        averageBalance=f"{state.average_balance:.2f}",
        averagePeriodDays=state.average_period_days,
        asOfTimestamp=state.last_updated
    )

@app.get("/accounts/{account_id}/statement", response_model=StatementResponse)
async def get_statement(account_id: str, fromDate: date, toDate: date, request: Request, token: dict = Depends(verify_token)):
    await simulate_failure(request)
    if account_id not in AccountStore:
        raise HTTPException(status_code=404, detail="Account not found")
    
    txns = []
    for tx in TransactionStore:
        if tx.account_id == account_id:
            tx_date = date.fromisoformat(tx.date)
            if fromDate <= tx_date <= toDate:
                txns.append(SchemaTransaction(
                    transactionId=tx.transaction_id,
                    referenceId=tx.reference_id,
                    date=tx_date,
                    amount=f"{tx.amount:.2f}",
                    direction=tx.direction,
                    description=tx.description
                ))
    return StatementResponse(
        accountId=account_id,
        fromDate=fromDate,
        toDate=toDate,
        transactions=txns
    )

@app.post("/payments/initiate", response_model=PaymentInitiateResponse, status_code=201)
async def initiate_payment(request: Request, payload: PaymentInitiateRequest, token: dict = Depends(verify_token)):
    await simulate_failure(request)
    
    signature = request.headers.get("X-Signature")
    if not signature:
        raise HTTPException(status_code=401, detail={"error": "INVALID_SIGNATURE"})

    payload_dict = payload.model_dump(exclude_unset=True)
    payload_dict["requestedExecutionDate"] = payload.requestedExecutionDate.isoformat()
    canonical_body = json.dumps(payload_dict, sort_keys=True, separators=(",", ":"))
    expected_sig = hmac.new(SIGNING_SECRET.encode(), canonical_body.encode(), hashlib.sha256).hexdigest()
    
    if signature != expected_sig and signature != "valid-signature":
        raise HTTPException(status_code=401, detail={"error": "INVALID_SIGNATURE"})
    
    if not payload.beneficiaryAccount.startswith("COMB-"):
        raise HTTPException(status_code=422, detail={"error": "INVALID_BENEFICIARY"})
        
    try:
        amount_dec = Decimal(payload.amount)
        debit_account(payload.sourceAccountId, amount_dec)
    except ValueError as e:
        if str(e) == "INSUFFICIENT_FUNDS":
            raise HTTPException(status_code=422, detail={"error": "INSUFFICIENT_FUNDS"})
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail="Source account not found")
        
    payment_id = f"PMT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    
    now = datetime.now(timezone.utc)
    record = PaymentRecord(
        payment_id=payment_id,
        status="PENDING_APPROVAL",
        amount=amount_dec,
        currency=payload.currency,
        beneficiary_account=payload.beneficiaryAccount,
        purpose=payload.purpose,
        requested_execution_date=payload.requestedExecutionDate.isoformat(),
        submitted_at=now
    )
    record.source_account_id = payload.sourceAccountId
    record.reference_note = payload.referenceNote
    
    PaymentStore[payment_id] = record
    
    return PaymentInitiateResponse(
        paymentId=payment_id,
        status=record.status,
        amount=payload.amount,
        currency=payload.currency,
        beneficiaryAccount=payload.beneficiaryAccount,
        purpose=payload.purpose,
        requestedExecutionDate=payload.requestedExecutionDate,
        submittedAt=now
    )

@app.get("/payments/{payment_id}/status", response_model=PaymentStatusResponse)
async def get_payment_status(payment_id: str, request: Request, token: dict = Depends(verify_token)):
    await simulate_failure(request)
    record = PaymentStore.get(payment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Payment not found")
    return PaymentStatusResponse(
        paymentId=record.payment_id,
        status=record.status,
        executedAt=record.executed_at
    )

@app.get("/rates/deposits", response_model=DepositRatesResponse)
async def get_deposit_rates(request: Request, token: dict = Depends(verify_token)):
    await simulate_failure(request)
    return DEPOSIT_RATES

@app.get("/rates/forex", response_model=ForexRatesResponse)
async def get_forex_rates(request: Request, token: dict = Depends(verify_token)):
    await simulate_failure(request)
    return FOREX_RATES

@app.get("/loans/{facility_id}", response_model=LoanFacility)
async def get_loan(facility_id: str, request: Request, token: dict = Depends(verify_token)):
    await simulate_failure(request)
    loan = LoanStore.get(facility_id)
    if not loan:
        raise HTTPException(status_code=404, detail={"error": "FACILITY_NOT_FOUND"})
    return LoanFacility(
        facilityId=loan.facility_id,
        facilityType=loan.facility_type,
        sanctionedAmount=f"{loan.sanctioned_amount:.2f}",
        outstandingPrincipal=f"{loan.outstanding_principal:.2f}",
        interestRate=loan.interest_rate,
        rateType=loan.rate_type,
        benchmarkRate=loan.benchmark_rate,
        spread=loan.spread,
        nextInstallmentDate=date.fromisoformat(loan.next_installment_date),
        nextInstallmentAmount=f"{loan.next_installment_amount:.2f}",
        currency=loan.currency
    )

@app.get("/transactions", response_model=TransactionLookupResponse)
async def get_transaction(refId: str, request: Request, token: dict = Depends(verify_token)):
    await simulate_failure(request)
    for tx in TransactionStore:
        if tx.reference_id == refId:
            return TransactionLookupResponse(
                found=True,
                transaction=TransactionLookupResult(
                    transactionId=tx.transaction_id,
                    referenceId=tx.reference_id,
                    date=date.fromisoformat(tx.date),
                    amount=f"{tx.amount:.2f}",
                    direction=tx.direction,
                    accountId=tx.account_id,
                    description=tx.description
                )
            )
    return TransactionLookupResponse(found=False, transaction=None)

@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request, exc):
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=True)
