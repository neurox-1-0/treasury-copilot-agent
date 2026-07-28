"""
Orchestrator and scheduler for Market Data refresh cycle.
Runs scrapers/sources concurrently or sequentially, handles errors per source, and updates store.
"""

import asyncio
import logging
from datetime import datetime, timezone

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
except ImportError:
    AsyncIOScheduler = None

from rate_source import RateSource, RateSourceError
from rate_store import RateStore


logger = logging.getLogger(__name__)


async def refresh_all_sources(sources: list[RateSource], store: RateStore) -> dict[str, bool]:
    """
    Fetch rates from all configured sources.
    Isolates failures: if one source fails, it records a failed attempt in store, while others succeed.
    Returns a dict mapping source_name -> success_bool.
    """
    now = datetime.now(timezone.utc)
    results = {}

    for source in sources:
        s_name = source.source_name()
        try:
            logger.info("Refreshing rates for source: %s", s_name)
            quotes = await source.fetch_rates()

            # Sanity check: rates must be in reasonable range if percentage/fractional
            valid_quotes = []
            for q in quotes:
                if q.instrumentType == "FOREX":
                    valid_quotes.append(q)
                elif 0.001 <= q.rate <= 0.50:  # 0.1% to 50%
                    valid_quotes.append(q)
                else:
                    logger.warning("Implausible rate %f discarded for %s", q.rate, s_name)

            store.save_quotes(s_name, valid_quotes, now)
            results[s_name] = True
            logger.info("Successfully saved %d quotes for %s", len(valid_quotes), s_name)
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to refresh rates for source %s: %s", s_name, e)
            store.record_failed_attempt(s_name, now)
            results[s_name] = False

    return results


def start_scheduler(sources: list[RateSource], store: RateStore):
    """
    Starts an AsyncIOScheduler to run background refresh cycles if available.
    """
    if AsyncIOScheduler is None:
        logger.warning("APScheduler is not installed. Background interval scheduler disabled.")
        return None

    scheduler = AsyncIOScheduler()
    # Bank rates refresh every 6 hours
    scheduler.add_job(
        refresh_all_sources,
        "interval",
        hours=6,
        args=[sources, store],
        id="bank_rates_refresh",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler

