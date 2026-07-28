"""
Shared helpers for turning bank websites' free-text term labels into
termDays integers, and percentage strings into decimal rates.

Every bank phrases terms slightly differently ("1 Month", "12 MONTHS",
"2 years", "100 Days Investment") — this centralizes that parsing so each
bank-specific parser doesn't reinvent it.
"""

import re

_MONTH_DAYS = 30
_YEAR_DAYS = 365

_TERM_PATTERN = re.compile(
    r"(\d+)\s*(day|month|year)s?", re.IGNORECASE
)


def parse_term_label(label: str) -> int | None:
    """
    '1 Month' -> 30, '100 Days' -> 100, '2 Years' -> 730, '100 Days Investment' -> 100.
    Returns None if no recognizable term is found (e.g. a non-term row like
    a heading or a "Call Deposit" label with no fixed number attached).
    """
    match = _TERM_PATTERN.search(label)
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2).lower()
    if unit == "day":
        return value
    if unit == "month":
        return value * _MONTH_DAYS
    if unit == "year":
        return value * _YEAR_DAYS
    return None


def parse_percentage(rate_str: str) -> float | None:
    """'9.00%' -> 0.09, '9.00' -> 0.09, 'N/A' -> None."""
    cleaned = rate_str.strip().replace("%", "").replace(",", "")
    if not cleaned or cleaned.upper() in ("N/A", "-", ""):
        return None
    try:
        return float(cleaned) / 100
    except ValueError:
        return None


_DATE_PATTERNS = [
    re.compile(r"w\.e\.f\.?\s*(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE),
    re.compile(r"Last Updated (?:on|On:?)\s*(\d{1,2}[\s./]\w+[\s./]\d{4}|\d{4}-\d{2}-\d{2})", re.IGNORECASE),
]


def extract_effective_date(text: str) -> str | None:
    """Pull a bank's own stated effective/last-updated date out of nearby text,
    e.g. 'w.e.f. 22.07.2026' or 'Last Updated on 22 Jul 2026 - 03:30PM'."""
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None
