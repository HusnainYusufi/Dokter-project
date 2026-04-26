"""Deterministic patient header builder.

Pulls header_fields and author/recipient signals from the most informative
pages of a patient bundle. Normalizes dates to full written form.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from app.schemas.extraction import PatientHeader
from app.services.extraction.models import ParsedPage, PatientBundle


_MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
_MONTH_ABBREV = {
    "jan": "January",
    "feb": "February",
    "mar": "March",
    "apr": "April",
    "may": "May",
    "jun": "June",
    "jul": "July",
    "aug": "August",
    "sep": "September",
    "sept": "September",
    "oct": "October",
    "nov": "November",
    "dec": "December",
}


def _expand_year(year: int) -> int:
    if year >= 100:
        return year
    return 1900 + year if year > 30 else 2000 + year


def _parse_date_parts(text: str) -> tuple[int, int, int] | None:
    """Return (year, month, day) tuple if parsable, else None."""
    if not text:
        return None
    cleaned = text.strip().rstrip(".,;").replace(",", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None

    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%m-%d-%Y",
        "%m/%d/%Y",
        "%d-%b-%Y",
        "%d %b %Y",
        "%d-%B-%Y",
        "%d %B %Y",
        "%B %d %Y",
        "%b %d %Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.year, dt.month, dt.day
        except ValueError:
            continue

    numeric_2y = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2})$", cleaned)
    if numeric_2y:
        a, b, raw_year = (int(x) for x in numeric_2y.groups())
        year = _expand_year(raw_year)
        if 1 <= a <= 12 and 1 <= b <= 31:
            return year, a, b
        if 1 <= b <= 12 and 1 <= a <= 31:
            return year, b, a

    compact = re.match(r"^(\d{1,2})([A-Za-z]{3,9})(\d{2,4})$", cleaned)
    if compact:
        month_key = compact.group(2)[:3].lower()
        if month_key in _MONTH_ABBREV:
            month_name = _MONTH_ABBREV[month_key]
            month_num = _MONTH_NAMES.index(month_name) + 1
            day = int(compact.group(1))
            year = _expand_year(int(compact.group(3)))
            return year, month_num, day

    sep = re.match(r"^(\d{1,2})[-/.\s]+([A-Za-z]{3,9})[-/.\s]+(\d{2,4})$", cleaned)
    if sep:
        month_key = sep.group(2)[:3].lower()
        if month_key in _MONTH_ABBREV:
            month_name = _MONTH_ABBREV[month_key]
            month_num = _MONTH_NAMES.index(month_name) + 1
            day = int(sep.group(1))
            year = _expand_year(int(sep.group(3)))
            return year, month_num, day

    month_first = re.match(r"^([A-Za-z]{3,9})[-/.\s]+(\d{1,2})[,\s]+(\d{2,4})$", cleaned)
    if month_first:
        month_key = month_first.group(1)[:3].lower()
        if month_key in _MONTH_ABBREV:
            month_name = _MONTH_ABBREV[month_key]
            month_num = _MONTH_NAMES.index(month_name) + 1
            day = int(month_first.group(2))
            year = _expand_year(int(month_first.group(3)))
            return year, month_num, day

    return None


def normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    text = re.sub(
        r"^\s*(?:date of birth|dob|d\.o\.b\.|review date)\s*[:#-]?\s*",
        "",
        raw.strip(),
        flags=re.IGNORECASE,
    )
    parts = _parse_date_parts(text)
    if not parts:
        return text or None
    year, month, day = parts
    return f"{_MONTH_NAMES[month - 1]} {day}, {year}"


def canonical_date_iso(raw: str | None) -> str | None:
    """Return YYYY-MM-DD canonical form for keying. None if unparsable."""
    if not raw:
        return None
    cleaned = re.sub(
        r"^\s*(?:date of birth|dob|d\.o\.b\.|review date)\s*[:#-]?\s*",
        "",
        raw.strip(),
        flags=re.IGNORECASE,
    )
    parts = _parse_date_parts(cleaned)
    if not parts:
        return None
    year, month, day = parts
    return f"{year:04d}-{month:02d}-{day:02d}"


def _most_common(values: list[str | None]) -> str | None:
    cleaned = [v for v in values if v]
    if not cleaned:
        return None
    counter = Counter(cleaned)
    return counter.most_common(1)[0][0]


def _format_doctor(name: str | None, credentials: str | None, is_doctor: bool) -> str | None:
    if not name:
        return None
    name = name.strip().rstrip(",")
    if is_doctor and not name.lower().startswith("dr"):
        last = name.split()[-1]
        return f"Dr. {last}"
    return name


def build_header(bundle: PatientBundle) -> PatientHeader:
    pages: list[ParsedPage] = [p for doc in bundle.documents for p in doc.pages]

    to_name = _most_common([p.header_fields.to for p in pages])
    from_field = _most_common([p.header_fields.from_ for p in pages])
    claim_number = _most_common([p.header_fields.claim_number for p in pages])
    occupation = _most_common([p.header_fields.occupation for p in pages])
    diagnosis_dod = _most_common([p.header_fields.diagnosis_dod for p in pages])

    primary_doc = bundle.documents[0] if bundle.documents else None
    review_date_raw = primary_doc.date if primary_doc else None
    if not review_date_raw:
        review_date_raw = _most_common([p.header_fields.review_date for p in pages])
    review_date = normalize_date(review_date_raw)

    age_dob = normalize_date(bundle.dob)

    from_name = from_field
    if not from_name and primary_doc and primary_doc.author and primary_doc.author.name:
        from_name = _format_doctor(primary_doc.author.name, primary_doc.author.credentials, primary_doc.author.is_doctor)

    claimant = bundle.name

    return PatientHeader(
        to_name=to_name,
        claim_number=claim_number,
        from_name=from_name,
        age_dob=age_dob,
        review_date=review_date,
        occupation=occupation,
        claimant=claimant,
        diagnosis_dod=diagnosis_dod,
    )
