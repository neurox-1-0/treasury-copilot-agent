"""
Shared OData v2 plumbing: response envelopes, $filter parsing, pagination.

This is intentionally a simplified subset of the real OData v2 spec —
enough to be structurally and behaviorally authentic (same envelope shape,
same query semantics, same pagination convention) without implementing a
full OData server framework.
"""

import operator
import re
from typing import Any
from fastapi import Request, HTTPException


PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 200

# Maps OData filter operators to Python operators
_OPS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "ge": operator.ge,
    "lt": operator.lt,
    "le": operator.le,
}

# Matches: FieldName op 'value'  OR  FieldName op value
_FILTER_TOKEN = re.compile(
    r"(\w+)\s+(eq|ne|gt|ge|lt|le)\s+(?:'([^']*)'|(\S+))"
)


def parse_filter(filter_str: str) -> list[tuple[str, str, str]]:
    """
    Parse a simplified $filter expression into a list of (field, op, value) clauses.
    Supports 'and' conjunctions only (matches the majority real-world usage pattern).
    Example: "CompanyCode eq '1000' and PaymentPriority eq 'FIXED'"
    """
    if not filter_str:
        return []
    clauses = []
    for part in re.split(r"\s+and\s+", filter_str, flags=re.IGNORECASE):
        match = _FILTER_TOKEN.match(part.strip())
        if not match:
            raise HTTPException(
                status_code=400,
                detail=f"Malformed $filter clause: '{part}'. "
                       f"Expected format: Field eq 'value' [and Field2 gt value2 ...]",
            )
        field, op, quoted_val, bare_val = match.groups()
        clauses.append((field, op, quoted_val if quoted_val is not None else bare_val))
    return clauses


def apply_filter(records: list[dict], clauses: list[tuple[str, str, str]]) -> list[dict]:
    """Apply parsed $filter clauses to a list of dict records (AND semantics)."""
    def matches(record: dict) -> bool:
        for field, op, value in clauses:
            if field not in record:
                raise HTTPException(status_code=400, detail=f"Unknown field in $filter: '{field}'")
            record_val = record[field]
            # naive numeric coercion for gt/ge/lt/le on numeric-looking fields
            try:
                cmp_val = type(record_val)(value)
            except (TypeError, ValueError):
                cmp_val = value
            if not _OPS[op](record_val, cmp_val):
                return False
        return True

    return [r for r in records if matches(r)]


def paginate(
    records: list[dict],
    request: Request,
    entity_set_path: str,
) -> tuple[list[dict], str | None]:
    """
    Apply $top/$skip pagination. Returns (page_of_records, next_link_or_none),
    mirroring SAP's __next cursor convention.
    """
    top = int(request.query_params.get("$top", PAGE_SIZE_DEFAULT))
    top = min(top, PAGE_SIZE_MAX)
    skip = int(request.query_params.get("$skip", 0))

    page = records[skip : skip + top]
    next_link = None
    if skip + top < len(records):
        next_skip = skip + top
        base = str(request.url).split("?")[0]
        # preserve existing query params, override $skip
        params = dict(request.query_params)
        params["$skip"] = str(next_skip)
        params["$top"] = str(top)
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        next_link = f"{base}?{qs}"

    return page, next_link


def apply_select(records: list[dict], select_str: str | None) -> list[dict]:
    """Apply $select field projection."""
    if not select_str:
        return records
    fields = [f.strip() for f in select_str.split(",")]
    return [{k: v for k, v in r.items() if k in fields} for r in records]


def envelope(records: list[dict], next_link: str | None = None) -> dict:
    """Wrap records in the real OData v2 { d: { results: [...] } } shape."""
    body = {"results": records}
    if next_link:
        body["__next"] = next_link
    return {"d": body}


def envelope_single(record: dict) -> dict:
    """Wrap a single entity in the OData v2 { d: {...} } shape (no results array)."""
    return {"d": record}


def query_entity_set(records: list[dict], request: Request, entity_set_path: str) -> dict:
    """
    Full pipeline for a GET EntitySet request: filter -> select -> paginate -> envelope.
    Use this as the one-liner inside each router endpoint.
    """
    filter_str = request.query_params.get("$filter")
    select_str = request.query_params.get("$select")

    filtered = apply_filter(records, parse_filter(filter_str)) if filter_str else records
    page, next_link = paginate(filtered, request, entity_set_path)
    projected = apply_select(page, select_str)

    return envelope(projected, next_link)
