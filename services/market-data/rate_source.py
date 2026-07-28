"""
Shared contract for all rate sources (Sampath API, BOC scraper, ComBank scraper, ...).

Every source — regardless of fetch mechanism (httpx, Playwright, manual) —
implements this same interface, so the orchestrator and storage layer never
need to know how a given source actually gets its data.
"""

from abc import ABC, abstractmethod
from enum import Enum
from pydantic import BaseModel


class InstrumentType(str, Enum):
    SAVINGS = "SAVINGS"
    CALL_DEPOSIT = "CALL_DEPOSIT"
    FIXED_DEPOSIT = "FIXED_DEPOSIT"
    TREASURY_BILL = "TREASURY_BILL"
    REPO = "REPO"
    POLICY_RATE = "POLICY_RATE"
    FOREX = "FOREX"
    INFLATION = "INFLATION"


class RateEntrySource(str, Enum):
    SCRAPED = "SCRAPED"
    API = "API"
    MANUAL = "MANUAL"


class RateQuote(BaseModel):
    source: str                      # "SAMPATH" | "BOC" | "SEYLAN" | "NDB" | "CBSL" | ...
    instrumentType: InstrumentType
    termDays: int                    # e.g. 30, 90, 365 (0 for spot/overnight/policy)
    rate: float                      # decimal, e.g. 0.085 for 8.5%
    currency: str = "LKR"
    effectiveDate: str | None = None  # bank's own "W.E.F." date if published, else None
    fetchedAt: str = ""              # ISO timestamp, set by the orchestrator/store on save
    entryMethod: RateEntrySource = RateEntrySource.SCRAPED
    enteredBy: str | None = None     # analyst identity, required when entryMethod == MANUAL
    metadata: dict[str, str] = {}    # extra details (e.g. {"buy": "331.62", "sell": "340.79"})


class RateSourceError(Exception):
    """Raised by any RateSource implementation on fetch/parse failure."""
    pass


class RateSource(ABC):
    @abstractmethod
    async def fetch_rates(self) -> list[RateQuote]:
        """Fetch and parse current rates. Raise RateSourceError on any failure —
        never return partial/guessed data silently."""
        ...

    @abstractmethod
    def source_name(self) -> str:
        ...
