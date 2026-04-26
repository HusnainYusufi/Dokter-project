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


def normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    text = re.sub(r"^\s*(?:date of birth|dob|d\.o\.b\.|review date)\s*[:#-]?\s*", "", raw.strip(), flags=re.IGNORECASE)
    text = text.strip().rstrip(".,;")
    if not text:
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
        "%B %d, %Y",
        "%b %d, %Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(text.replace(",", " ").replace("  ", " ").strip(), fmt.replace(",", "").strip())
            return f"{_MONTH_NAMES[dt.month - 1]} {dt.day}, {dt.year}"
        except ValueError:
            continue

    match = re.match(r"(\d{1,2})[-/.\s](\w{3,9})[-/.\s](\d{2,4})$", text)
    if match:
        day = int(match.group(1))
        month_lower = match.group(2)[:4].lower().rstrip(".")
        if month_lower[:3] in _MONTH_ABBREV:
            month_name = _MONTH_ABBREV[month_lower[:3]] if len(month_lower) <= 4 else _MONTH_ABBREV.get(month_lower, _MONTH_ABBREV[month_lower[:3]])
            year = int(match.group(3))
            if year < 100:
                year += 1900 if year > 30 else 2000
            return f"{month_name} {day}, {year}"

    match = re.match(r"(\w{3,9})[-/.\s](\d{1,2}),?[-/.\s]?(\d{2,4})$", text)
    if match:
        month_lower = match.group(1)[:4].lower().rstrip(".")
        if month_lower[:3] in _MONTH_ABBREV:
            month_name = _MONTH_ABBREV.get(month_lower, _MONTH_ABBREV[month_lower[:3]])
            day = int(match.group(2))
            year = int(match.group(3))
            if year < 100:
                year += 1900 if year > 30 else 2000
            return f"{month_name} {day}, {year}"

    return text


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
