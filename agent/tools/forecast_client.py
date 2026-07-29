"""
agent/tools/forecast_client.py
===============================

HTTP client wrapping the LSTM Forecasting Service (Component 3).

Purpose
-------
Provides the Reason node with a 14-day cash-flow forecast including per-day
confidence intervals and an ``overallConfidenceScore`` used by the Confidence
Check node as the primary routing signal.

Service
-------
Base URL: ``http://localhost:8003`` (configurable via ``FORECASTER_BASE_URL``).
Protocol: Simple JSON POST — no auth required.

Fallback
--------
If the forecaster is unavailable, raises ``ForecastClientError``.  The Reason
node is responsible for deciding whether to proceed with a worst-case
confidence score or block execution.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_FORECASTER_BASE_URL = os.getenv("FORECASTER_BASE_URL", "http://localhost:8003")
_TIMEOUT = 30.0  # LSTM inference can be slow on first call


class ForecastClientError(Exception):
    """Raised when the forecasting service is unreachable or returns an error."""


def get_forecast(company_code: str, horizon_days: int = 14) -> dict:
    """
    Request a cash-flow forecast from the LSTM Forecasting Service.

    Parameters
    ----------
    company_code:
        SAP company code (``"1000"``).
    horizon_days:
        Number of calendar days to forecast (default 14).
        The service supports 1–365 days.

    Returns
    -------
    dict
        ``ForecastResponse``-shaped dict with keys:
        - ``companyCode`` (str)
        - ``forecastHorizonDays`` (int)
        - ``generatedAt`` (ISO timestamp)
        - ``modelType`` (str: ``"STUB"`` or ``"LSTM"``)
        - ``forecast`` (list of daily predictions)
        - ``overallConfidenceScore`` (float 0–1) ← primary signal
        - ``flags`` (list[str])
        - ``fallbackUsed`` (bool)

    Raises
    ------
    ForecastClientError
        If the service is unreachable or returns a non-200 status.

    Examples
    --------
    ::

        result = get_forecast("1000", horizon_days=14)
        confidence = result["overallConfidenceScore"]  # e.g. 0.82
    """
    payload = {"companyCode": company_code, "horizonDays": horizon_days}
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(f"{_FORECASTER_BASE_URL}/forecast", json=payload)
            resp.raise_for_status()
            return resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        raise ForecastClientError(f"Forecast request failed: {exc}") from exc
