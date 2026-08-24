"""Rows of a results listing that became documents of their own.

The reference failure: page 10 ends with a two-row EMR results index, both rows
became imaging documents, and one asserted a normal impression for the 28 Apr
study that the real report on page 11 found abnormal.

A structure service would say those rows are a table. Without one, the parsed
output already carries the evidence, and the tests below pin both halves of it:
the listing is caught, and genuinely stacked documents survive untouched.
"""
from __future__ import annotations

from app.services.extraction.index_rows import demote_index_rows
from app.services.extraction.models import (
    AuthorFingerprint,
    DocumentFingerprint,
    DocumentSegment,
    EvidenceItem,
    ParsedPage,
)


def doc(
    doc_id: str,
    *,
    page: int,
    date: str | None,
    evidence: int,
    bucket: str = "imaging",
    pages: int = 1,
) -> DocumentSegment:
    parsed = [
        ParsedPage(
            page_number=page + offset,
            page_kind=bucket,
            document=DocumentFingerprint(bucket=bucket, date=date),
            evidence=[
                EvidenceItem(kind="finding", text=f"{doc_id} finding {i}")
                for i in range(evidence if offset == 0 else 0)
            ],
        )
        for offset in range(pages)
    ]
    return DocumentSegment(
        id=doc_id,
        pages=parsed,
        bucket=bucket,
        date=date,
        author=AuthorFingerprint(name="Someone"),
        include_in_output=True,
    )


def page_ten_file() -> list[DocumentSegment]:
    """The real shape: a questionnaire and two listing rows on page 10, with
    the studies those rows point at on pages 11 and 12."""
    return [
        doc("gad7", page=10, date="May 21, 2022", evidence=8, bucket="clinical"),
        doc("row_07may", page=10, date="May 07, 2022", evidence=1),
        doc("row_28apr", page=10, date="April 28, 2022", evidence=1),
        doc("sinuses", page=11, date="April 28, 2022", evidence=9),
        doc("chest", page=12, date="May 07, 2022", evidence=7),
    ]


def test_the_two_listing_rows_are_dropped_and_the_real_reports_kept():
    documents = page_ten_file()
    warnings = demote_index_rows(documents)

    kept = {d.id for d in documents if d.include_in_output}
    assert kept == {"gad7", "sinuses", "chest"}
    assert len(warnings) == 1
    assert warnings[0].kind == "listing_rows_not_reports"
    assert warnings[0].page_ranges == ["10-10"]


def test_the_warning_names_the_report_that_covers_each_dropped_row():
    """A shorter summary with no explanation is indistinguishable from a bug."""
    documents = page_ten_file()
    detail = demote_index_rows(documents)[0].detail

    assert "2 entries on page 10" in detail
    assert "April 28, 2022 (pages 11-11)" in detail
    assert "May 07, 2022 (pages 12-12)" in detail
    assert "remain on the page as evidence" in detail


def test_two_stacked_documents_are_left_alone():
    """A member's claim form above a physician's report is two real documents.
    Three-on-a-page is the threshold precisely so this survives."""
    documents = [
        doc("claim", page=2, date="January 23, 2023", evidence=6, bucket="clinical"),
        doc("physician", page=2, date="March 01, 2023", evidence=9, bucket="clinical"),
        doc("elsewhere", page=8, date="March 01, 2023", evidence=12, bucket="clinical"),
    ]
    assert demote_index_rows(documents) == []
    assert all(d.include_in_output for d in documents)


def test_a_thin_entry_with_no_fuller_twin_survives():
    """A one-line report that is the only record of its date is still the
    record of its date, and dropping it would lose evidence outright."""
    documents = [
        doc("gad7", page=10, date="May 21, 2022", evidence=8, bucket="clinical"),
        doc("row", page=10, date="May 07, 2022", evidence=1),
        doc("other", page=10, date="April 28, 2022", evidence=1),
        doc("unrelated", page=11, date="July 15, 2022", evidence=9),
    ]
    assert demote_index_rows(documents) == []
    assert all(d.include_in_output for d in documents)


def test_a_substantial_entry_is_never_called_a_row():
    documents = page_ten_file()
    substantial = next(d for d in documents if d.id == "row_28apr")
    substantial.pages[0].evidence = [
        EvidenceItem(kind="finding", text=f"detail {i}") for i in range(6)
    ]

    demote_index_rows(documents)
    assert substantial.include_in_output


def test_an_entry_sharing_the_pages_own_date_is_not_a_pointer():
    """It is part of the page's own document, not a reference away from it."""
    documents = [
        doc("main", page=10, date="May 21, 2022", evidence=8, bucket="clinical"),
        doc("fragment", page=10, date="May 21, 2022", evidence=1, bucket="clinical"),
        doc("third", page=10, date="May 21, 2022", evidence=1, bucket="clinical"),
        doc("elsewhere", page=12, date="May 21, 2022", evidence=9, bucket="clinical"),
    ]
    demote_index_rows(documents)
    assert all(d.include_in_output for d in documents)


def test_an_undated_thin_entry_is_left_alone():
    documents = [
        doc("main", page=10, date="May 21, 2022", evidence=8, bucket="clinical"),
        doc("undated", page=10, date=None, evidence=1),
        doc("row", page=10, date="April 28, 2022", evidence=1),
        doc("sinuses", page=11, date="April 28, 2022", evidence=9),
    ]
    demote_index_rows(documents)

    assert next(d for d in documents if d.id == "undated").include_in_output
    assert not next(d for d in documents if d.id == "row").include_in_output


def test_a_multi_page_entry_is_never_a_row():
    """A listing row occupies one line of one page, by definition."""
    documents = [
        doc("main", page=10, date="May 21, 2022", evidence=8, bucket="clinical"),
        doc("spread", page=10, date="April 28, 2022", evidence=1, pages=3),
        doc("third", page=10, date="May 07, 2022", evidence=1),
        doc("sinuses", page=13, date="April 28, 2022", evidence=9),
        doc("chest", page=14, date="May 07, 2022", evidence=7),
    ]
    demote_index_rows(documents)

    assert next(d for d in documents if d.id == "spread").include_in_output


def test_placeholders_are_never_considered():
    documents = page_ten_file()
    for document in documents:
        document.is_placeholder = True

    assert demote_index_rows(documents) == []


def test_an_ordinary_file_is_untouched():
    documents = [
        doc("a", page=1, date="May 07, 2022", evidence=6),
        doc("b", page=2, date="July 15, 2022", evidence=7),
        doc("c", page=3, date="August 09, 2022", evidence=8),
    ]
    assert demote_index_rows(documents) == []
    assert all(d.include_in_output for d in documents)
