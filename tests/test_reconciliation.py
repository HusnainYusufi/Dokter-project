"""Whole-file reconciliation.

These checks need no answer key and no per-file configuration. They ask
questions that only become answerable once the whole file is in hand, which is
exactly what a page-at-a-time reader can never do.
"""
from __future__ import annotations

from app.services.extraction.consistency import (
    find_absent_references,
    find_duplicate_studies,
    find_temporal_outliers,
    find_unattributed_records,
    reconcile,
)
from app.services.extraction.models import (
    AuthorFingerprint,
    DocumentFingerprint,
    DocumentSegment,
    EvidenceItem,
    ParsedPage,
    PatientBundle,
)


def doc(
    doc_id: str,
    *,
    page: int,
    date: str | None,
    bucket: str = "imaging",
    author: str | None = "Waslen",
    evidence: list[EvidenceItem] | None = None,
) -> DocumentSegment:
    parsed = ParsedPage(
        page_number=page,
        page_kind=bucket if bucket != "administrative" else "admin",
        evidence=evidence or [EvidenceItem(kind="finding", text=f"finding {page}")],
        document=DocumentFingerprint(bucket=bucket, date=date),
    )
    return DocumentSegment(
        id=doc_id,
        pages=[parsed],
        bucket=bucket,
        date=date,
        author=AuthorFingerprint(name=author),
        include_in_output=True,
    )


def bundle(*documents: DocumentSegment) -> PatientBundle:
    return PatientBundle(id="p1", key="k", name="Claimant", documents=list(documents))


# --------------------------------------------------------------------------
# Referenced but absent


def test_a_reference_to_a_date_the_file_does_not_hold_is_flagged():
    referring = doc(
        "a",
        page=12,
        date="May 07, 2022",
        evidence=[
            EvidenceItem(kind="finding", text="Lungs hyperinflated but clear."),
            EvidenceItem(
                kind="finding", text="COMPARISON: April 20, 2022", provenance="referenced"
            ),
        ],
    )
    warnings = find_absent_references(bundle(referring, doc("b", page=13, date="July 15, 2022")))

    assert len(warnings) == 1
    assert warnings[0].kind == "referenced_document_absent"
    assert "April 20, 2022" in warnings[0].detail


def test_a_reference_the_file_does_hold_is_not_flagged():
    referring = doc(
        "a",
        page=12,
        date="May 07, 2022",
        evidence=[
            EvidenceItem(
                kind="finding", text="COMPARISON: April 28, 2022", provenance="referenced"
            )
        ],
    )
    present = doc("b", page=11, date="April 28, 2022")

    assert find_absent_references(bundle(referring, present)) == []


def test_a_first_hand_finding_is_never_treated_as_a_reference():
    only_primary = doc(
        "a",
        page=12,
        date="May 07, 2022",
        evidence=[EvidenceItem(kind="finding", text="Seen on April 20, 2022 film.")],
    )
    assert find_absent_references(bundle(only_primary)) == []


# --------------------------------------------------------------------------
# Duplicates


def test_the_same_study_twice_is_flagged():
    warnings = find_duplicate_studies(
        bundle(
            doc("a", page=11, date="April 28, 2022", author="Waslen"),
            doc("b", page=26, date="April 28, 2022", author="Waslen"),
        )
    )

    assert len(warnings) == 1
    assert warnings[0].page_ranges == ["11-11", "26-26"]


def test_two_studies_on_one_date_by_different_authors_are_not_duplicates():
    assert (
        find_duplicate_studies(
            bundle(
                doc("a", page=11, date="April 28, 2022", author="Waslen"),
                doc("b", page=12, date="April 28, 2022", author="Patel"),
            )
        )
        == []
    )


def test_an_unattributed_study_is_never_called_a_duplicate():
    """Without an author the key is not distinctive enough to accuse."""
    assert (
        find_duplicate_studies(
            bundle(
                doc("a", page=11, date="April 28, 2022", author=None),
                doc("b", page=12, date="April 28, 2022", author=None),
            )
        )
        == []
    )


# --------------------------------------------------------------------------
# Temporal


def test_a_date_after_the_review_is_flagged():
    warnings = find_temporal_outliers(
        bundle(
            doc("a", page=1, date="May 07, 2022"),
            doc("b", page=2, date="July 15, 2022"),
            doc("c", page=3, date="August 09, 2027"),
        ),
        review_date_iso="2026-08-24",
    )

    assert [w.kind for w in warnings] == ["date_after_review"]


def test_a_date_decades_from_the_rest_of_the_file_is_flagged():
    warnings = find_temporal_outliers(
        bundle(
            doc("a", page=1, date="May 07, 2022"),
            doc("b", page=2, date="July 15, 2022"),
            doc("c", page=3, date="March 04, 1966"),
        )
    )

    assert [w.kind for w in warnings] == ["date_far_from_the_file"]


def test_an_ordinary_span_of_years_is_not_flagged():
    assert (
        find_temporal_outliers(
            bundle(
                doc("a", page=1, date="May 07, 2012"),
                doc("b", page=2, date="July 15, 2019"),
                doc("c", page=3, date="August 09, 2022"),
            )
        )
        == []
    )


def test_too_few_dates_to_have_an_outlier():
    """With two dates, neither is 'away from the others'."""
    assert (
        find_temporal_outliers(
            bundle(doc("a", page=1, date="May 07, 2022"), doc("b", page=2, date="March 04, 1966"))
        )
        == []
    )


# --------------------------------------------------------------------------
# Attribution


def test_unattributed_clinical_records_are_named_once():
    warnings = find_unattributed_records(
        bundle(
            doc("a", page=1, date="May 07, 2022", bucket="clinical", author=None),
            doc("b", page=2, date="July 15, 2022", bucket="imaging", author=None),
            doc("c", page=3, date="July 16, 2022", bucket="imaging", author="Patel"),
        )
    )

    assert len(warnings) == 1
    assert warnings[0].page_ranges == ["1-1", "2-2"]
    assert "2 clinical or imaging entries carry" in warnings[0].detail


def test_a_fully_attributed_file_raises_nothing():
    assert find_unattributed_records(bundle(doc("a", page=1, date="May 07, 2022"))) == []


# --------------------------------------------------------------------------
# The whole pass


def test_reconcile_runs_every_check_and_a_clean_file_is_silent():
    clean = bundle(
        doc("a", page=1, date="May 07, 2022", author="Patel"),
        doc("b", page=2, date="July 15, 2022", author="du Rand"),
        doc("c", page=3, date="August 09, 2022", author="Joanis"),
    )
    assert reconcile(clean, [], review_date_iso="2026-08-24") == []


def test_reconcile_collects_findings_from_more_than_one_check():
    messy = bundle(
        doc("a", page=1, date="May 07, 2022", author="Patel"),
        doc("b", page=2, date="July 15, 2022", author="du Rand"),
        doc("c", page=3, date="July 15, 2022", author="du Rand"),
        doc("d", page=4, date="March 04, 1966", author="Someone"),
    )
    kinds = {w.kind for w in reconcile(messy, [], review_date_iso="2026-08-24")}

    assert "duplicate_study" in kinds
    assert "date_far_from_the_file" in kinds
