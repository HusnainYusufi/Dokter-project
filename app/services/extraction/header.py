"""Deterministic patient header builder.

Pulls header_fields and author/recipient signals from the most informative
pages of a patient bundle. Normalizes dates to full written form.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from app.schemas.extraction import PatientHeader
from app.services.extraction.formatting import format_author
from app.services.extraction.models import DocumentSegment, ParsedPage, PatientBundle


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


# Blank-form template placeholders such as "MMM-DD-YYYY", "DD/MM/YYYY" or
# "YYYY-MM-DD" are sometimes captured verbatim as a document date. They are not
# real dates and must never surface in output.
_PLACEHOLDER_DATE_RE = re.compile(
    r"^[\s]*[MDY]{1,4}[\s\-/.]+[MDY]{1,4}[\s\-/.]+[MDY]{2,4}[\s]*$",
    re.IGNORECASE,
)


def is_placeholder_date(raw: str | None) -> bool:
    """True when ``raw`` is a blank-form date placeholder (e.g. 'MMM-DD-YYYY')."""
    if not raw:
        return False
    return bool(_PLACEHOLDER_DATE_RE.match(raw.strip()))


def _expand_year(year: int) -> int:
    if year >= 100:
        return year
    return 1900 + year if year > 30 else 2000 + year


def _parse_date_parts_raw(text: str) -> tuple[int, int, int] | None:
    """Return (year, month, day) tuple if parsable, else None."""
    if not text:
        return None
    cleaned = text.strip().rstrip(".,;").replace(",", " ")
    # Handwritten chart dates: "July 6th/2023", "Aug 10th / 2023" - drop the
    # ordinal suffix and tighten slash spacing so they parse like "July 6/2023".
    cleaned = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*/\s*", "/", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None

    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%b-%d",
        "%Y-%B-%d",
        "%Y/%b/%d",
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
        "%B %d/%Y",
        "%b %d/%Y",
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

    month_first = re.match(r"^([A-Za-z]{3,9})[-/.\s]+(\d{1,2})[,\s/.-]+(\d{2,4})$", cleaned)
    if month_first:
        month_key = month_first.group(1)[:3].lower()
        if month_key in _MONTH_ABBREV:
            month_name = _MONTH_ABBREV[month_key]
            month_num = _MONTH_NAMES.index(month_name) + 1
            day = int(month_first.group(2))
            year = _expand_year(int(month_first.group(3)))
            return year, month_num, day

    # Last resort for a noisy handwritten capture that no whole-string format
    # fits (e.g. "Feb 6. 14/2024" - stray marks copied in with the date):
    # pull the first month-name + day, and a 4-digit year from anywhere in
    # the string. Only fires when everything above failed, so clean dates
    # never take this path.
    noisy = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})\b", cleaned)
    if noisy:
        # The year belonging to this day is the one printed after it
        # ("through March 14, 2024" must yield 2024, not an earlier year
        # appearing elsewhere in the string); fall back to any year present.
        year_match = re.search(r"\b(19|20)\d{2}\b", cleaned[noisy.end():]) or re.search(
            r"\b(19|20)\d{2}\b", cleaned
        )
        month_key = noisy.group(1)[:3].lower()
        day = int(noisy.group(2))
        if year_match and month_key in _MONTH_ABBREV and 1 <= day <= 31:
            month_name = _MONTH_ABBREV[month_key]
            month_num = _MONTH_NAMES.index(month_name) + 1
            return int(year_match.group(0)), month_num, day

    return None


def _parse_date_parts(text: str) -> tuple[int, int, int] | None:
    """Parse a date and reject implausible years.

    Real clinical/DOB dates fall within 1900-2100. An OCR slip such as a
    dropped digit ("May 4, 166") would otherwise parse to a bogus year and,
    when used as a patient-identity key, split one patient into two bundles.
    Treat anything outside the plausible window as unparseable.
    """
    parts = _parse_date_parts_raw(text)
    if not parts:
        return None
    year, _, _ = parts
    if year < 1900 or year > 2100:
        return None
    return parts


def normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    if is_placeholder_date(raw):
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
    if is_placeholder_date(raw):
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


def _first_truthy(values: list[str | None]) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _most_common(values: list[str | None]) -> str | None:
    cleaned = [v for v in values if v]
    if not cleaned:
        return None
    counter = Counter(cleaned)
    return counter.most_common(1)[0][0]


def _primary_pages(primary_doc: DocumentSegment | None, pages: list[ParsedPage]) -> list[ParsedPage]:
    if primary_doc and primary_doc.pages:
        return list(primary_doc.pages)
    return pages


def _collect_diagnoses(pages: list[ParsedPage], limit: int = 5) -> str | None:
    """Build the header diagnosis line from distinct diagnosis evidence."""
    seen: set[str] = set()
    out: list[str] = []
    for page in pages:
        for item in page.evidence:
            if item.kind != "diagnosis":
                continue
            text = item.text.strip().rstrip(".")
            key = text.lower()
            if not text or key in seen:
                continue
            seen.add(key)
            out.append(text)
            if len(out) >= limit:
                return ", ".join(out)
    return ", ".join(out) or None


def build_header(bundle: PatientBundle) -> PatientHeader:
    pages: list[ParsedPage] = [p for doc in bundle.documents for p in doc.pages]
    primary_doc = bundle.documents[0] if bundle.documents else None
    primary_pages = _primary_pages(primary_doc, pages)

    def _from_primary(field: str) -> str | None:
        return _first_truthy([getattr(p.header_fields, field) for p in primary_pages])

    to_name = _from_primary("to") or _most_common([p.header_fields.to for p in pages])
    from_field = _from_primary("from_") or _most_common([p.header_fields.from_ for p in pages])
    claim_number = _from_primary("claim_number") or _most_common([p.header_fields.claim_number for p in pages])
    occupation = _from_primary("occupation") or _most_common([p.header_fields.occupation for p in pages])
    diagnosis_dod = (
        _from_primary("diagnosis_dod")
        or _most_common([p.header_fields.diagnosis_dod for p in pages])
        or _collect_diagnoses(pages)
    )

    review_date_raw = (primary_doc.date if primary_doc else None) or _from_primary("review_date") or _most_common([p.header_fields.review_date for p in pages])
    review_date = normalize_date(review_date_raw)

    age_dob = normalize_date(bundle.dob) or _first_truthy([
        normalize_date(doc.patient_dob) for doc in bundle.documents
    ])

    from_name = from_field
    if not from_name and primary_doc:
        from_name = format_author(primary_doc.author)

    claimant = bundle.name or (primary_doc.patient_name if primary_doc else None)

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
