"""
agent/memory/cache.py
=====================

In-process data cache for stale-data tracking.

Purpose
-------
When an ERP or bank API call fails, the Perceive node falls back to the last
successful data from this cache instead of halting the entire reasoning cycle.
The cache tracks *when* data was last fresh so the Confidence Check node can
assess whether stale data is material to the proposed action.

Design
------
- **Singleton in-process dict** — ``DataCache`` lives for the lifetime of the
  Python process.  On service restart the cache is empty, which is intentional:
  a freshly started agent should not inherit stale state from a previous run.
- **No TTL enforcement** — The Perceive node marks entries stale explicitly
  when a live fetch fails.  Age-based expiry is handled by the Confidence Check
  node (which checks ``last_fresh_at`` against ``datetime.utcnow()``).
- **Thread-safety** — For a single-threaded async event loop this is safe.
  If multi-threaded execution is required, wrap mutations in a ``threading.Lock``.

Usage
-----
Perceive node::

    data, freshness, last_fresh_at = cache_get("ERP_CASH_POSITION")
    if freshness == DataFreshness.MISSING:
        # No fallback available — set execution_blocked
        ...

    # On successful live fetch:
    cache_set("ERP_CASH_POSITION", live_data)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agent.state import DataFreshness


@dataclass
class CacheEntry:
    """
    A single cached data item.

    Attributes
    ----------
    data:
        The cached payload (raw dict, list, etc.).
    last_fresh_at:
        UTC timestamp of the last successful live fetch.
    is_fresh:
        ``True`` immediately after a ``cache_set``; set to ``False`` by
        ``mark_stale`` when a subsequent live fetch fails.
    """

    data: Any
    last_fresh_at: datetime
    is_fresh: bool = True


# Singleton in-process cache — reset on service restart
DataCache: dict[str, CacheEntry] = {}


def cache_set(key: str, data: Any) -> None:
    """
    Store fresh data in the cache.

    Called by the Perceive node after every successful live fetch.

    Parameters
    ----------
    key:
        Data source identifier, e.g. ``"ERP_CASH_POSITION"``,
        ``"BANK_BALANCE"``, ``"ERP_OPEN_PAYABLES"``.
    data:
        The raw response data to cache.
    """
    DataCache[key] = CacheEntry(
        data=data,
        last_fresh_at=datetime.utcnow(),
        is_fresh=True,
    )


def cache_get(key: str) -> tuple[Any, DataFreshness, datetime | None]:
    """
    Retrieve data from the cache.

    Returns a 3-tuple of ``(data, freshness, last_fresh_at)``.

    - If the key is present, ``freshness`` is ``DataFreshness.STALE``
      (regardless of ``is_fresh`` — the caller only reaches here because
      the live fetch already failed).
    - If the key is absent, returns ``(None, DataFreshness.MISSING, None)``.

    Parameters
    ----------
    key:
        Data source identifier.

    Returns
    -------
    tuple[Any, DataFreshness, datetime | None]
        ``(cached_data, freshness_enum, last_fresh_at_or_None)``
    """
    entry = DataCache.get(key)
    if entry is None:
        return None, DataFreshness.MISSING, None
    return entry.data, DataFreshness.STALE, entry.last_fresh_at


def mark_stale(key: str) -> None:
    """
    Mark a cache entry as stale without removing the data.

    Called when a live fetch fails after a previous successful fetch.
    The data remains accessible via ``cache_get`` for fallback use.

    Parameters
    ----------
    key:
        Data source identifier.
    """
    if key in DataCache:
        DataCache[key].is_fresh = False


def cache_has(key: str) -> bool:
    """
    Check if a key is present in the cache.
    
    Parameters
    ----------
    key:
        Data source identifier.
    """
    return key in DataCache


def clear_cache() -> None:
    """
    Remove all entries from the cache.

    Intended for use in test teardown to reset state between test cases.
    **Do not call in production code.**
    """
    DataCache.clear()
