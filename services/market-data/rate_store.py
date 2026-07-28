"""
Storage for scraped/fetched rate snapshots.

Simple JSON-file-backed store for demo purposes.
Key design point: this is a *write-then-read-latest* store. The optimizer
tool never triggers a fetch — it only ever reads whatever is currently
stored here, however fresh or stale that happens to be. Staleness is
surfaced explicitly, never hidden.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rate_source import RateQuote, RateEntrySource

STORE_PATH = Path(__file__).parent / "cache" / "rates_cache.json"

STALENESS_THRESHOLD = timedelta(hours=8)
MANUAL_STALENESS_THRESHOLD = timedelta(hours=48)


class RateStore:
    def __init__(self, path: Path = STORE_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_raw({"quotes": [], "lastRefreshAttempt": None, "lastRefreshSuccessBySource": {}})

    def _read_raw(self) -> dict:
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_raw(self, data: dict) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def save_quotes(self, source: str, quotes: list[RateQuote], fetched_at: datetime) -> None:
        """Replace this source's SCRAPED/API quotes with a fresh batch.
        Manual entries for the same source are untouched."""
        data = self._read_raw()
        fetched_at_iso = fetched_at.isoformat()
        for q in quotes:
            q.fetchedAt = fetched_at_iso

        non_manual_others = [
            q for q in data["quotes"]
            if not (q["source"] == source and q.get("entryMethod", "SCRAPED") != "MANUAL")
        ]
        data["quotes"] = non_manual_others + [q.model_dump() for q in quotes]
        if "lastRefreshSuccessBySource" not in data:
            data["lastRefreshSuccessBySource"] = {}
        data["lastRefreshSuccessBySource"][source] = fetched_at_iso
        data["lastRefreshAttempt"] = fetched_at_iso
        self._write_raw(data)

    def add_manual_quote(self, quote: RateQuote, entered_by: str) -> None:
        """Add or replace a manually-entered rate. Overwrites existing matching entry."""
        quote.entryMethod = RateEntrySource.MANUAL
        quote.enteredBy = entered_by
        quote.fetchedAt = datetime.now(timezone.utc).isoformat()

        data = self._read_raw()
        key = (quote.source, quote.instrumentType.value, quote.termDays)
        data["quotes"] = [
            q for q in data["quotes"]
            if (q["source"], q["instrumentType"], q["termDays"]) != key
        ] + [quote.model_dump()]
        self._write_raw(data)

    def remove_manual_quote(self, source: str, instrument_type: str, term_days: int) -> bool:
        """Remove a manual entry."""
        data = self._read_raw()
        key = (source, instrument_type, term_days)
        before = len(data["quotes"])
        data["quotes"] = [
            q for q in data["quotes"]
            if not (q.get("entryMethod") == "MANUAL"
                    and (q["source"], q["instrumentType"], q["termDays"]) == key)
        ]
        removed = len(data["quotes"]) != before
        if removed:
            self._write_raw(data)
        return removed

    def record_failed_attempt(self, source: str, attempted_at: datetime) -> None:
        """Log that a refresh was attempted but failed."""
        data = self._read_raw()
        data["lastRefreshAttempt"] = attempted_at.isoformat()
        self._write_raw(data)

    def get_latest_quotes(self, instrument_type: str | None = None) -> dict:
        """Returns latest cached quotes + per-source & per-manual staleness info."""
        data = self._read_raw()
        quotes = data.get("quotes", [])
        if instrument_type:
            quotes = [q for q in quotes if q["instrumentType"] == instrument_type]

        now = datetime.now(timezone.utc)

        annotated_quotes = []
        for q in quotes:
            q = dict(q)
            if q.get("entryMethod") == "MANUAL" and q.get("fetchedAt"):
                try:
                    dt = datetime.fromisoformat(q["fetchedAt"])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    age = now - dt
                    q["isStale"] = age > MANUAL_STALENESS_THRESHOLD
                    q["ageHours"] = round(age.total_seconds() / 3600, 1)
                except ValueError:
                    q["isStale"] = True
            annotated_quotes.append(q)

        staleness_report = {}
        for source, last_success_iso in data.get("lastRefreshSuccessBySource", {}).items():
            try:
                last_success = datetime.fromisoformat(last_success_iso)
                if last_success.tzinfo is None:
                    last_success = last_success.replace(tzinfo=timezone.utc)
                age = now - last_success
                staleness_report[source] = {
                    "lastSuccess": last_success_iso,
                    "isStale": age > STALENESS_THRESHOLD,
                    "ageHours": round(age.total_seconds() / 3600, 1),
                }
            except ValueError:
                staleness_report[source] = {
                    "lastSuccess": last_success_iso,
                    "isStale": True,
                    "ageHours": None,
                }

        return {
            "quotes": annotated_quotes,
            "stalenessBySource": staleness_report,
            "lastRefreshAttempt": data.get("lastRefreshAttempt"),
        }
