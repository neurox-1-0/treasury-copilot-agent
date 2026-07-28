"""
FastAPI application for Market Data Tool (Component 9).
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from rate_source import RateQuote, InstrumentType, RateEntrySource
from rate_store import RateStore
from orchestrator import refresh_all_sources, start_scheduler
from models import MarketRatesResponse, compute_best_available_rates
from sources.html_sources import BOCSource, SeylanSource, NDBSource
from sources.cbsl_source import CBSLSource
from sources.sampath_api_source import SampathAPISource
from dummy_sources import DummyBOCSource, DummySeylanSource, DummyNDBSource

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("market-data-service")

store = RateStore()

# Active sources: real scrapers / API sources
sources = [
    SampathAPISource(),
    BOCSource(),
    SeylanSource(),
    NDBSource(),
    CBSLSource(),
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Market Data Service...")
    # Start scheduler
    scheduler = start_scheduler(sources, store)
    # Perform initial background refresh task asynchronously
    try:
        await refresh_all_sources(sources, store)
    except Exception as e:
        logger.warning("Startup scrape encountered errors (seed cache will be used): %s", e)
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    logger.info("Shutting down Market Data Service...")



app = FastAPI(
    title="Market Data Tool Service",
    version="1.0.0",
    description="Assembles bank rates & CBSL policy indicator data for Treasury Copilot Agent.",
    lifespan=lifespan,
)


class ManualQuoteRequest(BaseModel):
    source: str
    instrumentType: str
    termDays: int
    rate: float
    currency: str = "LKR"
    effectiveDate: str | None = None
    enteredBy: str = Field(..., description="Analyst identity performing manual rate entry")


@app.get("/health")
async def health_check():
    latest = store.get_latest_quotes()
    return {
        "status": "healthy",
        "service": "market-data",
        "last_scraped_at": latest.get("lastRefreshAttempt"),
        "active_sources": [s.source_name() for s in sources],
    }


@app.get("/rates", response_model=MarketRatesResponse)
async def get_rates():
    latest = store.get_latest_quotes()
    quotes = latest.get("quotes", [])

    if not quotes:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "MARKET_DATA_UNAVAILABLE", "reason": "No cached rates available"},
        )

    # Group by bank/source for bankRates structure
    bank_rates: dict[str, dict] = {}
    cbsl_data: dict = {}

    for q in quotes:
        src = q.get("source", "").lower()
        if src == "cbsl":
            itype = q.get("instrumentType")
            if itype == "POLICY_RATE":
                cbsl_data["overnightPolicyRate"] = q.get("rate")
            elif itype == "INFLATION":
                cbsl_data["inflationCCPI"] = q.get("rate")
            elif itype == "FOREX":
                cbsl_data["forexUSD"] = {
                    "buy": q.get("metadata", {}).get("buy", str(q.get("rate"))),
                    "sell": q.get("metadata", {}).get("sell"),
                }
            cbsl_data["stale"] = latest.get("stalenessBySource", {}).get("CBSL", {}).get("isStale", False)
            cbsl_data["scraped_at"] = q.get("fetchedAt")
        else:
            if src not in bank_rates:
                stale_info = latest.get("stalenessBySource", {}).get(q.get("source", ""), {})
                bank_rates[src] = {
                    "fixedDeposit": [],
                    "callDeposit": None,
                    "stale": stale_info.get("isStale", False),
                    "scraped_at": q.get("fetchedAt"),
                }

            itype = q.get("instrumentType")
            if itype == "FIXED_DEPOSIT":
                bank_rates[src]["fixedDeposit"].append({
                    "termDays": q.get("termDays"),
                    "rate": q.get("rate"),
                })
            elif itype == "CALL_DEPOSIT":
                bank_rates[src]["callDeposit"] = {"rate": q.get("rate")}

    best_rates = compute_best_available_rates(quotes)

    return MarketRatesResponse(
        asOfTimestamp=datetime.now(timezone.utc).isoformat(),
        bankRates=bank_rates,
        cbsl=cbsl_data,
        bestAvailableRates=best_rates,
        stalenessBySource=latest.get("stalenessBySource", {}),
    )


@app.post("/refresh")
async def trigger_refresh():
    results = await refresh_all_sources(sources, store)
    return {
        "status": "refresh_completed",
        "results": results,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/rates/manual")
async def add_manual_rate(req: ManualQuoteRequest):
    try:
        inst_enum = InstrumentType(req.instrumentType)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid instrumentType: {req.instrumentType}")

    quote = RateQuote(
        source=req.source.upper(),
        instrumentType=inst_enum,
        termDays=req.termDays,
        rate=req.rate,
        currency=req.currency,
        effectiveDate=req.effectiveDate,
        entryMethod=RateEntrySource.MANUAL,
        enteredBy=req.enteredBy,
    )
    store.add_manual_quote(quote, entered_by=req.enteredBy)
    return {"status": "success", "message": f"Manual rate saved for {quote.source}"}
