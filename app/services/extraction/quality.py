"""Per-document extraction quality, derived from observable signals.

A model asked how confident it is will answer confidently. Self-reported
confidence is poorly calibrated and, worse, unexplainable: a reviewer told "0.62"
learns nothing about what to check.

So this derives quality from things that are true or not true about the parse -
did the date resolve, is there an author, did the pages yield evidence, does the
printed pagination line up - and reports the specific reasons alongside the
score. A reviewer gets "no author was identified and the date did not parse",
which tells them which page to open and what to look for.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.extraction.header import canonical_date_iso
from app.services.extraction.models import DocumentSegment

# Each signal costs its weight when absent. The weights say what matters to a
# medico-legal reviewer: an entry they cannot date or attribute is far harder to
# weigh than one that is merely thin.
_MISSING_DATE = 0.35
_MISSING_AUTHOR = 0.25
_NO_EVIDENCE = 0.30
_THIN_EVIDENCE = 0.15
_MISSING_TITLE = 0.10
_BROKEN_PAGINATION = 0.15
_MOSTLY_SECOND_HAND = 0.20

# Below this an entry is worth a reviewer's attention before it is relied on.
REVIEW_THRESHOLD = 0.65


@dataclass
class ExtractionQuality:
    """How well one document came out of the parse, and why."""

    score: float
    reasons: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.score < REVIEW_THRESHOLD


def _pagination_is_broken(doc: DocumentSegment) -> bool:
    """A document whose own printed pagination does not match what we grouped.

    The clearest case: pages printed "Page 1 of 5" but only three were gathered,
    so two pages of the document went somewhere else.
    """
    markers = [p.page_marker for p in doc.pages if p.page_marker.is_usable]
    if not markers:
        return False
    total = markers[0].total
    if any(marker.total != total for marker in markers):
        return True
    # Every page of the run should be present exactly once.
    indices = sorted(marker.index for marker in markers)
    return indices != list(range(1, len(indices) + 1)) or len(indices) < total


def assess(doc: DocumentSegment) -> ExtractionQuality:
    """Quality of one document's extraction, with the reasons spelled out."""
    score = 1.0
    reasons: list[str] = []

    if not canonical_date_iso(doc.date):
        score -= _MISSING_DATE
        reasons.append(
            "no usable date was read" if not doc.date else f"the date {doc.date!r} did not parse"
        )

    # A claimant-authored document has no clinical author by design: the golden
    # rules forbid attributing one to the claimant, so an empty author there is
    # the correct answer, not a gap. Flagging it sends a reviewer to a
    # signature block that should be empty.
    if not (doc.author.name or "").strip() and not doc.claimant_authored:
        score -= _MISSING_AUTHOR
        reasons.append("no author was identified")

    evidence = [item for page in doc.pages for item in page.evidence if item.text.strip()]
    page_count = len({page.page_number for page in doc.pages})
    if not evidence:
        score -= _NO_EVIDENCE
        reasons.append("no evidence was captured from these pages")
    elif len(evidence) < page_count:
        score -= _THIN_EVIDENCE
        reasons.append(f"only {len(evidence)} evidence items across {page_count} pages")
    elif evidence and not any(item.is_first_hand for item in evidence):
        # Everything it holds was recited or pointed at - it reports nothing of
        # its own, which is exactly the shape of an index row read as a report.
        score -= _MOSTLY_SECOND_HAND
        reasons.append("nothing here is first-hand; it recites or points at other records")

    if not (doc.title or "").strip():
        score -= _MISSING_TITLE
        reasons.append("no document title was read")

    if _pagination_is_broken(doc):
        score -= _BROKEN_PAGINATION
        reasons.append("its printed pagination does not match the pages gathered here")

    return ExtractionQuality(score=max(0.0, round(score, 2)), reasons=reasons)
