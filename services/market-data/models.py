"""
Pydantic models for Market Data service HTTP responses and best rate calculations.
"""

from datetime import datetime, timezone
from pydantic import BaseModel, Field


class BestRateInfo(BaseModel):
    rate: float
    bank: str
    termDays: int | None = None


class MarketRatesResponse(BaseModel):
    asOfTimestamp: str
    bankRates: dict[str, dict] = Field(default_factory=dict)
    cbsl: dict = Field(default_factory=dict)
    bestAvailableRates: dict[str, BestRateInfo] = Field(default_factory=dict)
    stalenessBySource: dict[str, dict] = Field(default_factory=dict)


def compute_best_available_rates(quotes: list[dict]) -> dict[str, BestRateInfo]:
    """
    Computes best available rates per tenor across all bank FD & Call deposit quotes.
    Tenors tracked: fd_14d, fd_30d, fd_90d, fd_180d, fd_365d, callDeposit.
    """
    best: dict[str, BestRateInfo] = {}

    tenor_map = {
        14: "fd_14d",
        30: "fd_30d",
        90: "fd_90d",
        180: "fd_180d",
        365: "fd_365d",
    }

    for q in quotes:
        itype = q.get("instrumentType")
        source = q.get("source", "UNKNOWN")
        rate = float(q.get("rate", 0.0))
        term_days = q.get("termDays", 0)

        if itype == "CALL_DEPOSIT" or term_days == 1:
            key = "callDeposit"
            if key not in best or rate > best[key].rate:
                best[key] = BestRateInfo(rate=rate, bank=source, termDays=1)
        elif itype == "FIXED_DEPOSIT":
            # Match nearest tenor key if exact or close
            tenor_key = tenor_map.get(term_days)
            if not tenor_key:
                # Find closest key in tenor_map
                closest_days = min(tenor_map.keys(), key=lambda d: abs(d - term_days))
                if abs(closest_days - term_days) <= 5:  # within 5 days tolerance
                    tenor_key = tenor_map[closest_days]

            if tenor_key:
                if tenor_key not in best or rate > best[tenor_key].rate:
                    best[tenor_key] = BestRateInfo(rate=rate, bank=source, termDays=term_days)

    return best
