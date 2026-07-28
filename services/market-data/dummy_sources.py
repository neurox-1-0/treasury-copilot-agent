"""
Dummy / Test stand-in RateSources.
"""

from rate_source import RateSource, RateQuote, InstrumentType, RateSourceError, RateEntrySource


class DummyBOCSource(RateSource):
    def source_name(self) -> str:
        return "BOC"

    async def fetch_rates(self) -> list[RateQuote]:
        return [
            RateQuote(source="BOC", instrumentType=InstrumentType.FIXED_DEPOSIT, termDays=30, rate=0.105),
            RateQuote(source="BOC", instrumentType=InstrumentType.FIXED_DEPOSIT, termDays=90, rate=0.115),
            RateQuote(source="BOC", instrumentType=InstrumentType.FIXED_DEPOSIT, termDays=180, rate=0.120),
            RateQuote(source="BOC", instrumentType=InstrumentType.FIXED_DEPOSIT, termDays=365, rate=0.125),
        ]


class DummySeylanSource(RateSource):
    def source_name(self) -> str:
        return "SEYLAN"

    async def fetch_rates(self) -> list[RateQuote]:
        return [
            RateQuote(source="SEYLAN", instrumentType=InstrumentType.FIXED_DEPOSIT, termDays=30, rate=0.110),
            RateQuote(source="SEYLAN", instrumentType=InstrumentType.FIXED_DEPOSIT, termDays=90, rate=0.120),
            RateQuote(source="SEYLAN", instrumentType=InstrumentType.FIXED_DEPOSIT, termDays=180, rate=0.125),
            RateQuote(source="SEYLAN", instrumentType=InstrumentType.CALL_DEPOSIT, termDays=1, rate=0.085),
        ]


class DummyNDBSource(RateSource):
    def source_name(self) -> str:
        return "NDB"

    async def fetch_rates(self) -> list[RateQuote]:
        return [
            RateQuote(source="NDB", instrumentType=InstrumentType.FIXED_DEPOSIT, termDays=30, rate=0.108),
            RateQuote(source="NDB", instrumentType=InstrumentType.FIXED_DEPOSIT, termDays=90, rate=0.118),
            RateQuote(source="NDB", instrumentType=InstrumentType.FIXED_DEPOSIT, termDays=180, rate=0.122),
        ]


class FlakyCombankSource(RateSource):
    def source_name(self) -> str:
        return "COMBANK"

    async def fetch_rates(self) -> list[RateQuote]:
        raise RateSourceError("COMBANK: request blocked by bot detection (403 Forbidden)")
