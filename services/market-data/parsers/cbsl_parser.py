"""
Parser for Central Bank of Sri Lanka (CBSL) homepage key economic indicators.
Extracts Overnight Policy Rate, CCPI Inflation, and USD/LKR Exchange Rates.
"""

import re
from bs4 import BeautifulSoup
from rate_source import RateQuote, InstrumentType, RateSourceError
from parsers.term_utils import parse_percentage

SOURCE_NAME = "CBSL"


def parse_cbsl_homepage(html: str) -> list[RateQuote]:
    """
    Parses CBSL homepage HTML for key indicator cards/tables:
    - Overnight Policy Rate -> InstrumentType.POLICY_RATE
    - Inflation (CCPI) -> InstrumentType.INFLATION
    - USD/LKR Exchange Rates -> InstrumentType.FOREX
    """
    soup = BeautifulSoup(html, "html.parser")
    quotes: list[RateQuote] = []

    text = soup.get_text()

    # 1. Overnight Policy Rate (e.g., "Overnight Policy Rate - 8.75 %" or "8.75%")
    opr_match = re.search(r"Overnight Policy Rate\s*[-:]?\s*(\d+(?:\.\d+)?)\s*%", text, re.IGNORECASE)
    if opr_match:
        rate_val = float(opr_match.group(1)) / 100.0
        quotes.append(RateQuote(
            source=SOURCE_NAME,
            instrumentType=InstrumentType.POLICY_RATE,
            termDays=1,
            rate=rate_val,
        ))

    # 2. Inflation (CCPI) (e.g., "Inflation - 6.80 % (CCPI)" or "6.80%")
    inf_match = re.search(r"Inflation\s*[-:]?\s*(\d+(?:\.\d+)?)\s*%\s*(?:\([^)]*\))?", text, re.IGNORECASE)
    if inf_match:
        inf_val = float(inf_match.group(1)) / 100.0
        quotes.append(RateQuote(
            source=SOURCE_NAME,
            instrumentType=InstrumentType.INFLATION,
            termDays=0,
            rate=inf_val,
        ))

    # 3. USD/LKR Exchange Rates (e.g. "USD/LKR - TT Buy 331.6265, TT Sell 340.7938")
    usd_buy = re.search(r"TT Buy\s*[-:]?\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    usd_sell = re.search(r"TT Sell\s*[-:]?\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if usd_buy and usd_sell:
        buy_rate = float(usd_buy.group(1))
        sell_rate = float(usd_sell.group(1))
        quotes.append(RateQuote(
            source=SOURCE_NAME,
            instrumentType=InstrumentType.FOREX,
            termDays=0,
            rate=buy_rate,  # Use Buy rate as main value
            currency="USD",
            metadata={"buy": str(buy_rate), "sell": str(sell_rate)},
        ))

    if not quotes:
        raise RateSourceError("CBSL: parsed page but extracted zero indicator quotes")

    return quotes
