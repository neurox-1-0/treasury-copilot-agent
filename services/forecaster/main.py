from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import List, Optional
import sys
from pathlib import Path
from datetime import date
import logging
import datetime

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

from services.forecaster.model.stub import StubForecaster
from services.forecaster.model.lstm import LSTMForecaster
from services.erp_mock.data.seed import generate_historical_series
from services.forecaster.data.series_generator import generate_feature_matrix, generate_synthetic_exogenous

app = FastAPI(title="LSTM Forecasting Tool")
logger = logging.getLogger("uvicorn.error")

class ForecastRequest(BaseModel):
    companyCode: str
    horizonDays: int

class ForecastDay(BaseModel):
    date: str
    predictedNetCashFlow: str
    confidenceLow: str
    confidenceHigh: str
    dayConfidenceScore: float

class ForecastResponse(BaseModel):
    companyCode: str
    forecastHorizonDays: int
    generatedAt: str
    modelType: str
    forecast: List[ForecastDay]
    overallConfidenceScore: float
    flags: List[str]
    fallbackUsed: bool
    fallbackReason: Optional[str]

# Initialize model
weights_dir = Path(__file__).parent / "model" / "weights"
if (weights_dir / "lstm_model.keras").exists():
    try:
        forecaster = LSTMForecaster(weights_dir=str(weights_dir))
        logger.info("Loaded LSTM model.")
    except Exception as e:
        logger.warning(f"Failed to load LSTM model: {e}. Falling back to Stub.")
        forecaster = StubForecaster()
else:
    logger.info("LSTM model weights not found. Using StubForecaster.")
    forecaster = StubForecaster()

@app.post("/forecast", response_model=ForecastResponse)
async def create_forecast(req: ForecastRequest = Body(...)):
    if req.companyCode != "1000":
        raise HTTPException(status_code=404, detail="Company code not found")
    if req.horizonDays <= 0:
        raise HTTPException(status_code=400, detail="Horizon days must be > 0")
    if req.horizonDays > 365:
        raise HTTPException(status_code=400, detail="Horizon days cannot exceed 365")
        
    today = date.today()
    
    # In a real app we would call the ERP Mock and Market Data API here via HTTP.
    # For now, we simulate fetching recent historical data directly from the generators.
    lookback = 90
    try:
        cash_flow_data = generate_historical_series(lookback_days=lookback)
        cf_series = [float(d["net_cash_flow"]) for d in cash_flow_data]
        
        exo_data = generate_synthetic_exogenous(lookback_days=lookback)
        
        feature_matrix = generate_feature_matrix(
            cash_flow_series=cf_series,
            awplr_series=exo_data["awplr"],
            repo_rate_series=exo_data["repo_rate"],
            best_fd_90d_series=exo_data["best_fd_rate_90d"],
            usd_lkr_series=exo_data["usd_lkr_mid"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load historical data: {str(e)}")
        
    try:
        if isinstance(forecaster, StubForecaster):
            res = forecaster.predict(
                company_code=req.companyCode,
                horizon_days=req.horizonDays,
                historical_cash_flows=cf_series,
                start_date=today
            )
        else:
            res = forecaster.predict(
                company_code=req.companyCode,
                horizon_days=req.horizonDays,
                feature_matrix=feature_matrix,
                start_date=today
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Forecasting failed: {str(e)}")
        
    res["generatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return res
