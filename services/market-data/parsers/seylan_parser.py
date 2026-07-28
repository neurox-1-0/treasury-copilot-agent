"""
Parser for Seylan Bank's interest-rates page.
"""

from bs4 import BeautifulSoup
from rate_source import RateQuote, InstrumentType, RateSourceError
from parsers.term_utils import parse_term_label, parse_percentage, extract_effective_date

SOURCE_NAME = "SEYLAN"

FD_SECTION_HEADINGS = [
    "Fixed Deposits - Interest Paid At Maturity",
    "Rupee Fixed Deposit Interest Paid Monthly",
]

CALL_DEPOSIT_HEADING = "Call Deposit"


def _parse_section_table(soup: BeautifulSoup, heading_text: str, instrument_type: InstrumentType) -> list[RateQuote]:
    heading = soup.find(string=lambda s: s and heading_text in s)
    if not heading:
        return []

    table = heading.find_next("table")
    if not table:
        return []

    effective_date = None
    date_marker = heading.find_next(string=lambda s: s and ("w.e.f" in s.lower() or "Last Updated" in s))
    if date_marker:
        effective_date = extract_effective_date(str(date_marker))

    quotes = []
    for row in table.select("tr")[1:]:
        cells = [c.get_text(strip=True) for c in row.select("td")]
        if len(cells) < 2:
            continue
        term_days = parse_term_label(cells[0])
        rate = parse_percentage(cells[1])
        if term_days and rate:
            quotes.append(RateQuote(
                source=SOURCE_NAME,
                instrumentType=instrument_type,
                termDays=term_days,
                rate=rate,
                effectiveDate=effective_date,
            ))
    return quotes


def parse_seylan_page(html: str) -> list[RateQuote]:
    soup = BeautifulSoup(html, "html.parser")
    quotes: list[RateQuote] = []

    for heading_text in FD_SECTION_HEADINGS:
        quotes.extend(_parse_section_table(soup, heading_text, InstrumentType.FIXED_DEPOSIT))

    call_heading = soup.find(string=lambda s: s and CALL_DEPOSIT_HEADING in s)
    if call_heading:
        call_table = call_heading.find_next("table")
        if call_table:
            for row in call_table.select("tr")[1:]:
                cells = [c.get_text(strip=True) for c in row.select("td")]
                if len(cells) < 2:
                    continue
                rate = parse_percentage(cells[1])
                if rate and "above" in cells[0].lower():
                    quotes.append(RateQuote(
                        source=SOURCE_NAME,
                        instrumentType=InstrumentType.CALL_DEPOSIT,
                        termDays=1,
                        rate=rate,
                    ))

    if not quotes:
        raise RateSourceError("SEYLAN: parsed page but extracted zero quotes — page structure may have changed")

    return quotes
