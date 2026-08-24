"""Printed pagination as a boundary signal.

The case this exists for: a Physician's Initial Report Form printed "Page 1 of
5" through "Page 5 of 5" across five pages. Its last page carries only Part 8 -
a signature block and a fee note - so read on its own it looks administrative,
and the run split it off as a separate document, discarding the form's author
and its signature date. The footer said "Page 5 of 5" the whole time.
"""
from __future__ import annotations

from app.services.extraction.grouping import (
    apply_printed_page_markers,
    group_dated_entries,
    group_documents,
)
from app.services.extraction.models import DocumentFingerprint, EvidenceItem, PageMarker, ParsedPage


def page(
    number: int,
    *,
    index: int = 0,
    total: int = 0,
    kind: str = "clinical",
    starts: bool = False,
    title: str | None = None,
) -> ParsedPage:
    return ParsedPage(
        page_number=number,
        page_marker=PageMarker(index=index, total=total),
        starts_new_document=starts,
        page_kind=kind,
        include_in_output=kind not in {"admin", "empty"},
        document=DocumentFingerprint(bucket="clinical", title=title, date="March 01, 2023"),
        evidence=[EvidenceItem(kind="finding", text=f"finding {number}")],
    )


def test_a_marker_is_only_usable_when_it_is_coherent():
    assert PageMarker(index=1, total=5).is_usable
    assert not PageMarker().is_usable
    assert not PageMarker(index=0, total=5).is_usable
    assert not PageMarker(index=6, total=5).is_usable
    # A one-page document carries no boundary information beyond itself.
    assert not PageMarker(index=1, total=1).is_usable


def test_the_signature_page_of_a_form_stays_with_its_form():
    """Part 8 on its own looks administrative. 'Page 5 of 5' says otherwise."""
    pages = [
        page(3, index=1, total=5, starts=True, title="PHYSICIAN'S INITIAL REPORT FORM"),
        page(4, index=2, total=5),
        page(5, index=3, total=5),
        page(6, index=4, total=5),
        page(7, index=5, total=5, kind="admin", starts=True),
    ]
    apply_printed_page_markers(pages)

    assert pages[4].starts_new_document is False
    # It is part of the form, so it is not dropped from the output either.
    assert pages[4].include_in_output is True


def test_a_first_page_opens_a_document_even_when_it_looked_like_a_continuation():
    pages = [page(3, index=3, total=3), page(4, index=1, total=4, starts=False)]
    apply_printed_page_markers(pages)

    assert pages[1].starts_new_document is True


def test_a_form_with_markers_groups_into_one_document():
    pages = [
        page(3, index=1, total=5, starts=True, title="PHYSICIAN'S INITIAL REPORT FORM"),
        page(4, index=2, total=5),
        page(5, index=3, total=5),
        page(6, index=4, total=5),
        page(7, index=5, total=5, kind="admin", starts=True),
    ]
    segments = group_documents(pages)

    assert len(segments) == 1
    assert [p.page_number for p in segments[0].pages] == [3, 4, 5, 6, 7]


def test_a_marker_that_does_not_follow_its_predecessor_is_ignored():
    """A misread footer must never glue unrelated documents together."""
    pages = [
        page(1, index=1, total=3, starts=True, title="First"),
        # Claims to continue a 9-page run that never started here.
        page(2, index=4, total=9, starts=True, title="Second"),
    ]
    apply_printed_page_markers(pages)

    assert pages[1].starts_new_document is True


def test_a_different_total_does_not_continue_the_run():
    pages = [
        page(1, index=1, total=5, starts=True, title="First"),
        page(2, index=2, total=7, starts=True, title="Second"),
    ]
    apply_printed_page_markers(pages)

    assert pages[1].starts_new_document is True


def test_pages_without_markers_keep_the_boundary_they_were_given():
    """Most bundle pages print nothing. The heuristics still own those."""
    pages = [page(1, starts=True, title="First"), page(2, starts=True, title="Second")]
    apply_printed_page_markers(pages)

    assert [p.starts_new_document for p in pages] == [True, True]


def test_two_consecutive_forms_each_keep_their_own_run():
    pages = [
        page(1, index=1, total=2, starts=True, title="First form"),
        page(2, index=2, total=2),
        page(3, index=1, total=2, starts=False, title="Second form"),
        page(4, index=2, total=2),
    ]
    pages[2].document.date = "June 14, 2023"
    pages[3].document.date = "June 14, 2023"
    apply_printed_page_markers(pages)

    assert [p.starts_new_document for p in pages] == [True, False, True, False]
    assert len(group_documents(pages)) == 2


def test_the_pass_runs_on_the_entry_point_the_pipeline_actually_calls():
    """The regression that motivated this test: the marker pass was wired into
    group_documents, which the pipeline never calls. Markers were captured,
    the quality check could see them, and the boundaries they were meant to fix
    were still wrong - because the pass never ran in production at all."""
    pages = [
        page(3, index=1, total=5, starts=True, title="PHYSICIAN'S INITIAL REPORT FORM"),
        page(4, index=2, total=5),
        page(5, index=3, total=5),
        page(6, index=4, total=5),
        page(7, index=5, total=5, kind="admin", starts=True),
    ]
    segments = group_dated_entries(pages)

    assert len(segments) == 1
    assert [p.page_number for p in segments[0].pages] == [3, 4, 5, 6, 7]


def test_a_stated_continuation_survives_a_changed_date_stamp():
    """A form's signature page routinely carries its own signing date. Without
    the marker the date change alone splits it off."""
    pages = [
        page(3, index=1, total=2, starts=True, title="FORM"),
        page(4, index=2, total=2, kind="admin", starts=True),
    ]
    pages[1].document.date = "March 10, 2023"

    assert len(group_dated_entries(pages)) == 1


def test_a_stated_first_page_still_opens_its_own_card():
    pages = [
        page(1, index=1, total=2, starts=True, title="First"),
        page(2, index=2, total=2),
        page(3, index=1, total=2, starts=False, title="Second"),
        page(4, index=2, total=2),
    ]

    assert len(group_dated_entries(pages)) == 2


def test_a_report_form_is_one_document_titled_and_whole():
    """The three-way split this was written for.

    A five-page report form came back as: its title page dropped as
    administrative, its middle pages as an untitled "clinical note", and its
    signature page split off with the author. Every page of the run was judged
    on its own appearance. Resolved as a run, it is one titled document.
    """
    pages = [
        # Page 1 of 5 carries the form name and a member authorization block,
        # so alone it reads as administrative.
        page(3, index=1, total=5, kind="admin", starts=True, title="PHYSICIAN'S INITIAL REPORT FORM"),
        page(4, index=2, total=5, title=None),
        page(5, index=3, total=5, title=None),
        page(6, index=4, total=5, title=None),
        # Page 5 of 5 is Part 8: a signature block and a fee note.
        page(7, index=5, total=5, kind="admin", starts=True, title=None),
    ]
    segments = group_dated_entries(pages)

    assert len(segments) == 1
    assert [p.page_number for p in segments[0].pages] == [3, 4, 5, 6, 7]
    # The title page was not dropped, and its title reached the whole run.
    assert segments[0].title == "PHYSICIAN'S INITIAL REPORT FORM"
    assert segments[0].include_in_output


def test_an_administrative_run_stays_administrative():
    """Unification must not promote a genuinely administrative document into
    the output just because it paginates itself."""
    pages = [
        page(1, index=1, total=2, kind="admin", starts=True, title="Consent form"),
        page(2, index=2, total=2, kind="admin"),
    ]
    for p in pages:
        p.include_in_output = False

    assert group_dated_entries(pages) == []


def test_a_truncated_run_still_groups_the_pages_that_are_present():
    pages = [
        page(1, index=1, total=5, starts=True, title="FORM"),
        page(2, index=2, total=5),
        page(3, index=3, total=5),
    ]
    segments = group_dated_entries(pages)

    assert len(segments) == 1
    assert [p.page_number for p in segments[0].pages] == [1, 2, 3]
