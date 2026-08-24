"""The eval scorer, checked against the run that motivated it.

The regression these guard is not a crash: it is a scorer that silently passes
bad output. So the central test replays the real defects from the August run
and asserts each one is caught.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.score import load_entries, render, score

KEY = json.loads(
    (Path(__file__).resolve().parents[1] / "evals/cases/lilian_30_pages.json").read_text()
)
CEILINGS = {"clinical": 150, "imaging": 50, "functional": 200, "other": 90}


def job(paragraphs: list[dict]) -> dict:
    return {"patients": [{"id": "p1", "summary_paragraphs": paragraphs}]}


def para(text, start, end, registered_type=None, placeholder=False) -> dict:
    return {
        "text": text,
        "page_start": start,
        "page_end": end,
        "registered_type": registered_type,
        "is_placeholder": placeholder,
    }


def perfect_job() -> dict:
    """Output that satisfies the key - the fixed point the pipeline aims at."""
    out = []
    for doc in KEY["documents"]:
        start, end = doc["pages"]
        bits = []
        if doc.get("date"):
            bits.append(doc["date"])
        bits.append(doc.get("title_contains") or "document")
        if doc.get("author"):
            bits.append(f"by {doc['author']}")
        bits.append("records the findings.")
        out.append(
            para(
                " ".join(bits),
                start,
                end,
                registered_type=doc["bucket"],
                placeholder=not doc.get("summarized"),
            )
        )
    return job(out)


def test_the_key_covers_every_page_exactly_once():
    seen: set[int] = set()
    for doc in KEY["documents"]:
        start, end = doc["pages"]
        pages = set(range(start, end + 1))
        assert not (pages & seen), f"{doc['id']} overlaps an earlier document"
        seen |= pages
    assert seen == set(range(1, KEY["page_count"] + 1))


def test_a_conforming_run_scores_clean():
    report = score(perfect_job(), KEY, CEILINGS)
    assert report.findings == [], render(report, KEY["case_id"])
    assert report.score == 1.0


def test_a_dropped_page_is_critical():
    paragraphs = perfect_job()["patients"][0]["summary_paragraphs"]
    without_pft = [p for p in paragraphs if p["page_start"] != 17]
    report = score(job(without_pft), KEY, CEILINGS)

    assert any(f.check == "page_coverage" for f in report.critical)


# --------------------------------------------------------------------------
# The real defects from the August run.


def test_it_catches_documents_invented_from_the_results_index():
    """Two rows of an EMR index table became two imaging reports."""
    paragraphs = perfect_job()["patients"][0]["summary_paragraphs"]
    paragraphs += [
        para("May 07, 2022 imaging report by Leane Norma Pask lists the impression as Normal.",
             10, 10, "imaging"),
        para("April 28, 2022 imaging report by Claire Chao documents an X-ray of the "
             "sinuses/chest with an impression of Normal.", 10, 10, "imaging"),
    ]
    report = score(job(paragraphs), KEY, CEILINGS)

    checks = {f.check for f in report.critical}
    assert "trap:page10_results_index_is_not_documents" in checks
    assert "trap:no_false_normal_for_28apr" in checks
    assert "trap:routing_metadata_is_not_authorship" in checks


def test_it_catches_the_signature_page_split_off_from_its_form():
    """Page 7 is Part 8 of the report form; splitting it off lost the author."""
    paragraphs = [
        p for p in perfect_job()["patients"][0]["summary_paragraphs"] if p["page_start"] != 3
    ]
    paragraphs += [
        para("March 01, 2023 Physical Restrictions / Limitations notes the findings.",
             4, 6, "clinical"),
        para("March 01, 2023 Part 8 - Attending Physician - administrative content only.",
             7, 7, "administrative", placeholder=True),
    ]
    report = score(job(paragraphs), KEY, CEILINGS)

    checks = {f.check for f in report.findings}
    # The form's author is gone, and its pages are split.
    assert "author:physicians_initial_report" in checks
    assert "trap:signature_page_belongs_to_its_form" in checks


def test_it_catches_two_instruments_merged_into_one_entry():
    paragraphs = [
        p
        for p in perfect_job()["patients"][0]["summary_paragraphs"]
        if p["page_start"] not in {20, 22}
    ]
    paragraphs.append(
        para("Undated functional report is an Appendix A screening tool.", 20, 24, "functional")
    )
    report = score(job(paragraphs), KEY, CEILINGS)

    assert "trap:pcfs_is_its_own_document" in {f.check for f in report.findings}


def test_it_catches_a_photograph_described_as_a_radiograph():
    paragraphs = [
        p for p in perfect_job()["patients"][0]["summary_paragraphs"] if p["page_start"] != 29
    ]
    paragraphs.append(
        para("Imaging report documents a radiographic image of teeth and jaw with marker "
             "text visible stating RIGHT SIDE.", 29, 29, "imaging")
    )
    report = score(job(paragraphs), KEY, CEILINGS)

    assert "trap:photograph_is_not_a_radiograph" in {f.check for f in report.findings}


def test_it_catches_a_date_carried_over_from_the_previous_study():
    paragraphs = [
        p for p in perfect_job()["patients"][0]["summary_paragraphs"] if p["page_start"] != 12
    ]
    paragraphs.append(
        para("April 28, 2022 imaging report documents DX Chest 2 Views.", 12, 12, "imaging")
    )
    report = score(job(paragraphs), KEY, CEILINGS)

    assert "date:chest_07may" in {f.check for f in report.findings}


@pytest.mark.parametrize(
    ("words", "bucket", "severity"),
    [(400, "clinical", "error"), (160, "clinical", "warning"), (150, "clinical", None)],
)
def test_it_scores_ceiling_overruns(words, bucket, severity):
    """A 500-word wall against a 150-word ceiling shipped once because nothing
    measured it."""
    paragraphs = [
        p for p in perfect_job()["patients"][0]["summary_paragraphs"] if p["page_start"] != 3
    ]
    prefix = "March 01, 2023 PHYSICIAN'S INITIAL REPORT FORM by Leane Pask records"
    body = " ".join(["word"] * (words - len(prefix.split())))
    paragraphs.append(para(f"{prefix} {body}", 3, 7, bucket))
    report = score(job(paragraphs), KEY, CEILINGS)

    matching = [f for f in report.findings if f.check == f"ceiling:{bucket}"]
    if severity is None:
        assert not matching
    else:
        assert matching and matching[0].severity == severity


def test_an_empty_job_is_reported_rather_than_scoring_perfectly():
    report = score(job([]), KEY, CEILINGS)
    assert report.critical
    assert report.score == 0.0


def test_load_entries_reads_the_nested_result_shape():
    entries = load_entries({"result": {"patients": [{"summary_paragraphs": [para("x", 1, 2)]}]}})
    assert len(entries) == 1 and entries[0].pages == {1, 2}
