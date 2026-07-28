"""
Parser for Bank of Ceylon's rates-tariff page (static HTML, confirmed via
manual fetch — no JS rendering needed).

Strategy: find the heading/text 'Rupee Fixed Deposits' (or similar), then
walk forward through the tables that follow it until the next major
section heading.
"""

from bs4 import BeautifulSoup
from rate_source import RateQuote, InstrumentType, RateSourceError
from parsers.term_utils import parse_term_label, parse_percentage, extract_effective_date

SOURCE_NAME = "BOC"


def parse_boc_page(html: str) -> list[RateQuote]:
    soup = BeautifulSoup(html, "html.parser")
    quotes: list[RateQuote] = []

    # 1. Rupee Fixed Deposits section
    fd_heading = soup.find(string=lambda s: s and "Rupee Fixed Deposits" in s)
    if not fd_heading:
        raise RateSourceError("BOC: could not locate 'Rupee Fixed Deposits' section heading")

    fd_table = fd_heading.find_next("table")
    if not fd_table:
        raise RateSourceError("BOC: no table found after Fixed Deposits heading")

    effective_date = None
    date_marker = fd_heading.find_next(string=lambda s: s and "W.E.F" in s.upper())
    if date_marker:
        effective_date = extract_effective_date(str(date_marker))

    for row in fd_table.select("tr")[1:]:  # skip header row
        cells = [c.get_text(strip=True) for c in row.select("td")]
        if len(cells) < 2:
            continue
        term_label, rate_str = cells[0], cells[1]
        term_days = parse_term_label(term_label)
        rate = parse_percentage(rate_str)
        if term_days is None or rate is None:
            continue  # skip rows that aren't plain term/rate pairs
        quotes.append(RateQuote(
            source=SOURCE_NAME,
            instrumentType=InstrumentType.FIXED_DEPOSIT,
            termDays=term_days,
            rate=rate,
            effectiveDate=effective_date,
        ))

    # 2. Treasury Bill Rates section
    tbill_heading = soup.find(string=lambda s: s and "Treasury Bill Rates" in s)
    if tbill_heading:
        tbill_table = tbill_heading.find_next("table")
        if tbill_table:
            for row in tbill_table.select("tr")[1:]:
                cells = [c.get_text(strip=True) for c in row.select("td")]
                if len(cells) < 2:
                    continue
                term_days = parse_term_label(cells[0])
                rate = parse_percentage(cells[1])
                if term_days and rate:
                    quotes.append(RateQuote(
                        source=SOURCE_NAME,
                        instrumentType=InstrumentType.TREASURY_BILL,
                        termDays=term_days,
                        rate=rate,
                    ))

    if not quotes:
        raise RateSourceError("BOC: parsed page but extracted zero quotes — page structure may have changed")

    return quotes
