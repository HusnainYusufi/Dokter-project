"""Filtering / scrubbing pass over parsed pages.

- Strips PII from evidence text and raw excerpts (defense-in-depth; the prompt
  also asks Gemini to skip these).
- Forces `include_in_output=False` for admin / empty / signature_only pages
  that have no evidence.
"""
from __future__ import annotations

import re

from app.services.extraction.models import EvidenceItem, ParsedPage


_PII_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"),
    re.compile(r"\b\(\d{3}\)\s?\d{3}[-.\s]\d{4}\b"),
    re.compile(r"\b\d{3}[-.\s]\d{2}[-.\s]\d{3,4}\b"),
    re.compile(r"\b[A-Z]\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
    re.compile(r"\b\d{10}\b"),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    re.compile(r"\b\d{1,5}\s+[\w.\s-]+(?:Street|St\.|Avenue|Ave\.|Road|Rd\.|Boulevard|Blvd\.|Drive|Dr\.|Lane|Ln\.|Court|Ct\.|Crescent|Cres\.|Place|Pl\.)\b", re.IGNORECASE),
]

_PII_LABELS = re.compile(
    r"\b(?:OHIP|Health\s*Card|MRN|HCN|SIN|Tel(?:ephone)?|Phone|Fax|Email|E-mail|Address)\s*[:#]?\s*[^\s,]+",
    re.IGNORECASE,
)


def _scrub_text(text: str) -> str:
    if not text:
        return text
    cleaned = text
    for pattern in _PII_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = _PII_LABELS.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;:|")
    return cleaned


def _scrub_evidence(evidence: list[EvidenceItem]) -> list[EvidenceItem]:
    out: list[EvidenceItem] = []
    for item in evidence:
        cleaned = _scrub_text(item.text)
        if not cleaned:
            continue
        out.append(EvidenceItem(kind=item.kind, text=cleaned, value=item.value))
    return out


def scrub_pages(pages: list[ParsedPage]) -> list[ParsedPage]:
    scrubbed: list[ParsedPage] = []
    for page in pages:
        page.evidence = _scrub_evidence(page.evidence)
        page.raw_text_excerpt = _scrub_text(page.raw_text_excerpt)
        if page.page_kind in {"admin", "empty"} and not page.evidence:
            page.include_in_output = False
        scrubbed.append(page)
    return scrubbed
