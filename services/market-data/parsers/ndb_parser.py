"""
Parser for NDB Bank's interest-rates page.
"""

from bs4 import BeautifulSoup
from rate_source import RateQuote, InstrumentType, RateSourceError
from parsers.term_utils import parse_term_label, parse_percentage, extract_effective_date

SOURCE_NAME = "NDB"


def parse_ndb_page(html: str) -> list[RateQuote]:
    soup = BeautifulSoup(html, "html.parser")
    quotes: list[RateQuote] = []

    fd_heading = soup.find(string=lambda s: s and "Fixed Deposits" in s)
    if not fd_heading:
        raise RateSourceError("NDB: could not locate 'Fixed Deposits' section heading")

    fd_table = fd_heading.find_next("table")
    if not fd_table:
        raise RateSourceError("NDB: no table found after Fixed Deposits heading")

    effective_date = None
    date_marker = fd_heading.find_previous(string=lambda s: s and "Last Updated On" in s) \
        or fd_heading.find_next(string=lambda s: s and "Last Updated On" in s)
    if date_marker:
        effective_date = extract_effective_date(str(date_marker))

    last_seen_term_days: int | None = None
    for row in fd_table.select("tr")[1:]:
        cells = [c.get_text(strip=True) for c in row.select("td")]
        if len(cells) < 3:
            continue

        term_label = cells[0]
        term_days = parse_term_label(term_label) if term_label else None
        if term_days is not None:
            last_seen_term_days = term_days
        elif last_seen_term_days is not None:
            term_days = last_seen_term_days
        else:
            continue

        rate_candidates = [parse_percentage(c) for c in cells[1:]]
        rate = next((r for r in rate_candidates if r is not None), None)
        if rate is None:
            continue

        quotes.append(RateQuote(
            source=SOURCE_NAME,
            instrumentType=InstrumentType.FIXED_DEPOSIT,
            termDays=term_days,
            rate=rate,
            effectiveDate=effective_date,
        ))

    if not quotes:
        raise RateSourceError("NDB: parsed page but extracted zero quotes — page structure may have changed")

    return quotes
