"""Find entries that are rows of a listing rather than reports.

Page 10 of the reference file ends with a two-row EMR results index. Both rows
became imaging documents with impressions of their own, and one asserted a
normal result for a study the real report - two pages later - found abnormal.

A layout service would say outright that those rows are a table. Without one,
the evidence is still there in what was parsed, and it is decisive:

  * three separate documents came off ONE physical page;
  * two of them carry almost nothing, one or two evidence items each;
  * their dates are not the page's own date; and
  * for each of those dates, a fuller document exists ELSEWHERE in the file.

A real report is not one line long, does not share a page with two others, and
does not have a richer twin of itself on another page. All four together are a
listing, and each condition alone is ordinary - which is what makes the
conjunction safe to act on.

Genuinely stacked documents are common and must survive: a member's claim form
and a physician's report printed on one page are two real documents, each with
substantial content and no duplicate elsewhere. They fail the second and fourth
conditions and are left alone.

Demoted entries are removed from the summary but never from the record: the
rows stay on their page as evidence, and a warning names what happened and
which real report covers each date.
"""
from __future__ import annotations

from app.schemas.extraction import ConsistencyWarning
from app.services.extraction.header import canonical_date_iso
from app.services.extraction.models import DocumentSegment

# Above this an entry has said too much to be one line of a table.
_MAX_ROW_EVIDENCE = 2

# A page carrying this many separate documents is a listing until shown
# otherwise. Two is ordinary - a claim form above a physician's report.
_MIN_DOCUMENTS_ON_PAGE = 3

# How much richer the real report must be before a thin twin is called a row.
_RICHER_BY = 3


def _sole_page(doc: DocumentSegment) -> int | None:
    """The page this document lives on, when it lives on exactly one."""
    pages = {page.page_number for page in doc.pages}
    return next(iter(pages)) if len(pages) == 1 else None


def _evidence_count(doc: DocumentSegment) -> int:
    return len([item for item in doc.all_evidence if item.text.strip()])


def demote_index_rows(documents: list[DocumentSegment]) -> list[ConsistencyWarning]:
    """Drop listing rows out of the summary, in place, and say what was dropped.

    Returns one warning per page acted on, so the reviewer sees the decision
    rather than a silently shorter summary.
    """
    warnings: list[ConsistencyWarning] = []
    candidates = [d for d in documents if not d.is_placeholder and d.include_in_output]

    by_page: dict[int, list[DocumentSegment]] = {}
    for doc in candidates:
        page = _sole_page(doc)
        if page is not None:
            by_page.setdefault(page, []).append(doc)

    for page, stacked in sorted(by_page.items()):
        if len(stacked) < _MIN_DOCUMENTS_ON_PAGE:
            continue

        # The page's own document is the richest thing on it.
        richest = max(stacked, key=_evidence_count)
        page_date = canonical_date_iso(richest.date)

        demoted: list[DocumentSegment] = []
        covered_by: list[str] = []

        for doc in stacked:
            if doc is richest or _evidence_count(doc) > _MAX_ROW_EVIDENCE:
                continue
            iso = canonical_date_iso(doc.date)
            if not iso or (page_date and iso == page_date):
                # Undated, or part of the page's own document rather than a
                # pointer away from it.
                continue
            # A fuller document for that same date, on a different page.
            twin = next(
                (
                    other
                    for other in candidates
                    if other is not doc
                    and _sole_page(other) != page
                    and canonical_date_iso(other.date) == iso
                    and _evidence_count(other) >= _evidence_count(doc) + _RICHER_BY
                ),
                None,
            )
            if twin is None:
                continue
            demoted.append(doc)
            covered_by.append(f"{doc.date} (pages {twin.page_start}-{twin.page_end})")

        if not demoted:
            continue

        for doc in demoted:
            doc.include_in_output = False

        warnings.append(
            ConsistencyWarning(
                kind="listing_rows_not_reports",
                page_ranges=[f"{page}-{page}"],
                detail=(
                    f"{len(demoted)} entr{'y' if len(demoted) == 1 else 'ies'} on page {page} "
                    f"looked like rows of a results listing rather than reports: each carried "
                    f"almost no content, shared the page with other entries, and named a date "
                    f"whose full report appears elsewhere in the file. They were left out of "
                    f"the summary in favour of "
                    + "; ".join(covered_by)
                    + ". The rows remain on the page as evidence."
                ),
            )
        )

    return warnings
