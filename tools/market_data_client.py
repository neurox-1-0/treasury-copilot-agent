"""
agent/tools/market_data_client.py
==================================

HTTP client for the Market Data service (Component 9, http://localhost:8005).
Perceive node uses this client to fetch live/cached bank & CBSL market rate data.
"""

from datetime import datetime
import logging
import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MARKET_DATA_SERVICE_URL = "http://localhost:8005"
TIMEOUT_SECONDS = 5


class MarketDataError(Exception):
    """Raised when market data service request fails."""
    pass


def get_market_rates(url: str = MARKET_DATA_SERVICE_URL) -> dict:
    """
    Fetch market rates from Market Data Tool service `GET /rates`.
    Returns raw dict response containing bankRates, cbsl, bestAvailableRates, etc.
    Raises MarketDataError if service is unreachable.
    """
    endpoint = f"{url.rstrip('/')}/rates"
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
            resp = client.get(endpoint)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch market rates from %s: %s", endpoint, exc)
        raise MarketDataError(f"Market data fetch failed: {exc}") from exc
