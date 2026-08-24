"""Cross-entry consistency checks over a finished summary.

Every other stage looks at one page or one entry at a time, which is what let a
row of an EMR results index become an imaging report asserting a normal
impression for a study that was abnormal - reported alongside the correct
abnormal entry for the same exam, on the same date.

Nothing could see both at once. This runs after the summary is assembled, when
all the entries are in hand, and says plainly when two of them cannot both be
true. It never edits or deletes a summary: a reviewer decides which entry is
wrong, and a silently dropped medical finding is worse than a flagged one.
"""
from __future__ import annotations

import re

from app.schemas.extraction import ConsistencyWarning, SummaryParagraph
from app.services.extraction.header import canonical_date_iso
from app.services.extraction.models import DocumentSegment, PatientBundle

# Wording that asserts a study found nothing. Deliberately narrow: these have
# to be claims about the study as a whole, not a normal incidental finding
# inside an otherwise abnormal report ("cardiac contours within normal limits").
_NEGATIVE = re.compile(
    r"\b(?:impression(?:\s+\w+){0,3}\s+(?:is|as|of|:)?\s*)?"
    r"(?:normal|unremarkable|no acute (?:abnormality|finding|process)|"
    r"within normal limits|negative study)\b",
    re.IGNORECASE,
)

# Wording that asserts the study found something. A study reported as normal
# does not say these things.
_POSITIVE = re.compile(
    r"\b(?:opacification|fracture|lesion|mass|effusion|consolidation|"
    r"haemorrhage|hemorrhage|infarct|stenosis|herniation|compression|"
    r"lucency|nodule|tumou?r|abnormal(?:ity|ities)?|deformity|"
    r"displacement|oedema|edema)\b",
    re.IGNORECASE,
)

# Body region words, so two studies on the same date are only compared when
# they plausibly examine the same thing.
_REGIONS = (
    "sinus",
    "chest",
    "rib",
    "brain",
    "head",
    "skull",
    "spine",
    "abdomen",
    "pelvis",
    "knee",
    "shoulder",
    "hip",
    "ankle",
    "wrist",
    "hand",
    "foot",
    "neck",
)


def _regions(text: str) -> set[str]:
    lowered = text.lower()
    return {region for region in _REGIONS if region in lowered}


def _leading_date(paragraph: SummaryParagraph) -> str | None:
    """The date an entry opens with, canonicalized for comparison."""
    match = re.match(r"\s*([A-Za-z]{3,9}\.?\s+\d{1,2},\s*\d{4})", paragraph.text)
    if not match:
        return None
    return canonical_date_iso(match.group(1))


# A negated finding is not a finding. Without stripping these first, "no acute
# abnormality" and "no pleural effusion" read as positive assertions because
# they contain the words the positive pattern looks for.
_NEGATED = re.compile(
    r"\b(?:no|without|free of|negative for)\s+"
    r"(?:\w+\s+){0,3}?"
    r"(?:" + "|".join(
        [
            "abnormalit(?:y|ies)",
            "acute (?:abnormality|finding|process|intrathoracic process)",
            "aggressive",
            "consolidation",
            "effusion",
            "fracture",
            "haemorrhage",
            "hemorrhage",
            "infarct",
            "lesions?",
            "mass(?: effect)?",
            "nodules?",
            "oedema",
            "edema",
        ]
    ) + r")",
    re.IGNORECASE,
)


def _strip_negations(text: str) -> str:
    return _NEGATED.sub(" ", text)


def _asserts_negative(text: str) -> bool:
    stripped = _strip_negations(text)
    return bool(_NEGATIVE.search(text)) and not _POSITIVE.search(stripped)


def _asserts_positive(text: str) -> bool:
    return bool(_POSITIVE.search(_strip_negations(text)))


def find_contradictions(paragraphs: list[SummaryParagraph]) -> list[ConsistencyWarning]:
    """Pairs of entries that cannot both describe the same study truthfully.

    Two imaging entries carrying the same date and a shared body region, where
    one reports the study as normal and the other reports findings, are almost
    always one study reported twice - once from a real report and once from an
    index row, a duplicate scan, or a hallucinated impression.
    """
    warnings: list[ConsistencyWarning] = []
    imaging = [
        p
        for p in paragraphs
        if not p.is_placeholder
        and (p.registered_type or p.document_type or "").lower() == "imaging"
    ]

    for index, first in enumerate(imaging):
        first_date = _leading_date(first)
        if not first_date:
            continue
        for second in imaging[index + 1 :]:
            if _leading_date(second) != first_date:
                continue
            shared = _regions(first.text) & _regions(second.text)
            if not shared:
                continue
            negative, positive = None, None
            if _asserts_negative(first.text) and _asserts_positive(second.text):
                negative, positive = first, second
            elif _asserts_negative(second.text) and _asserts_positive(first.text):
                negative, positive = second, first
            if not negative or not positive:
                continue
            warnings.append(
                ConsistencyWarning(
                    kind="contradictory_imaging",
                    document_numbers=sorted(
                        {negative.document_number, positive.document_number}
                    ),
                    page_ranges=sorted(
                        {
                            f"{negative.page_start}-{negative.page_end}",
                            f"{positive.page_start}-{positive.page_end}",
                        }
                    ),
                    detail=(
                        f"Two imaging entries share the date and the "
                        f"{'/'.join(sorted(shared))} region, but one reports the study as "
                        f"normal and the other reports findings. One of them is probably not "
                        f"a real report - check the source pages before relying on either."
                    ),
                )
            )
    return warnings


# --------------------------------------------------------------------------
# Whole-file checks
#
# These need no answer key and no per-file configuration. They ask questions
# that only make sense once the whole file is in hand, which is precisely what
# a page-at-a-time reader can never do.


def _document_dates(bundle: PatientBundle) -> list[str]:
    return [iso for iso in (canonical_date_iso(doc.date) for doc in bundle.documents) if iso]


def find_absent_references(bundle: PatientBundle) -> list[ConsistencyWarning]:
    """Documents the file points at but does not contain.

    The golden rules require saying plainly when a referenced document is not
    present. Leaving that to the summarizer means it is judged one entry at a
    time, with no way to know whether the attachment turns up twenty pages
    later. Here the whole file is in hand, so the question is answerable.
    """
    warnings: list[ConsistencyWarning] = []
    present = set(_document_dates(bundle))

    for doc in bundle.documents:
        if doc.is_placeholder:
            continue
        pointers = [
            item.text.strip()
            for page in doc.pages
            for item in page.evidence
            if item.provenance == "referenced" and item.text.strip()
        ]
        if not pointers:
            continue
        # A pointer naming a date the file does not hold anywhere is a document
        # the reviewer will look for and not find.
        unresolved: list[str] = []
        for pointer in pointers:
            match = re.search(r"([A-Za-z]{3,9}\.?\s+\d{1,2},?\s*\d{2,4}|\d{1,2}[-/][A-Za-z]{3}[-/]?\d{2,4})", pointer)
            iso = canonical_date_iso(match.group(1)) if match else None
            if iso and iso not in present:
                unresolved.append(pointer)
        if not unresolved:
            continue
        warnings.append(
            ConsistencyWarning(
                kind="referenced_document_absent",
                page_ranges=[f"{doc.page_start}-{doc.page_end}"],
                detail=(
                    "This document refers to material that is not in the file: "
                    + "; ".join(sorted(set(unresolved))[:3])
                    + ". Treat it as missing evidence rather than as something read."
                ),
            )
        )
    return warnings


def find_duplicate_studies(bundle: PatientBundle) -> list[ConsistencyWarning]:
    """The same study appearing twice.

    A merged bundle routinely re-encloses the same report, and a parse can also
    split one report into two. Same date, same bucket, and the same author is
    almost never two genuine studies.
    """
    warnings: list[ConsistencyWarning] = []
    seen: dict[tuple[str, str, str], DocumentSegment] = {}

    for doc in bundle.documents:
        if doc.is_placeholder or doc.bucket not in {"imaging", "pathology"}:
            continue
        iso = canonical_date_iso(doc.date)
        author = (doc.author.name or "").strip().lower()
        if not iso or not author:
            continue
        key = (iso, doc.bucket, author)
        earlier = seen.get(key)
        if earlier is None:
            seen[key] = doc
            continue
        warnings.append(
            ConsistencyWarning(
                kind="duplicate_study",
                page_ranges=sorted(
                    {
                        f"{earlier.page_start}-{earlier.page_end}",
                        f"{doc.page_start}-{doc.page_end}",
                    }
                ),
                detail=(
                    "Two entries carry the same date, type, and author, so they are "
                    "probably one study appearing twice. Confirm before treating them "
                    "as independent findings."
                ),
            )
        )
    return warnings


def find_temporal_outliers(bundle: PatientBundle, *, review_date_iso: str | None = None) -> list[ConsistencyWarning]:
    """Dates that cannot be right.

    A date after the review itself, or decades away from every other document
    in the file, is a misread rather than a record - most often a two-digit
    year expanded the wrong way, or a form's revision code read as a date.
    """
    warnings: list[ConsistencyWarning] = []
    dated = [
        (doc, iso)
        for doc, iso in ((d, canonical_date_iso(d.date)) for d in bundle.documents)
        if iso and not doc.is_placeholder
    ]
    if len(dated) < 3:
        # Too few dates for "away from the others" to mean anything.
        return warnings

    years = sorted(int(iso[:4]) for _, iso in dated)
    median = years[len(years) // 2]

    for doc, iso in dated:
        year = int(iso[:4])
        if review_date_iso and iso > review_date_iso:
            warnings.append(
                ConsistencyWarning(
                    kind="date_after_review",
                    page_ranges=[f"{doc.page_start}-{doc.page_end}"],
                    detail=(
                        f"This entry is dated {doc.date}, after the review date. A record "
                        f"cannot postdate the review that reads it - check the source page."
                    ),
                )
            )
        elif abs(year - median) > 25:
            warnings.append(
                ConsistencyWarning(
                    kind="date_far_from_the_file",
                    page_ranges=[f"{doc.page_start}-{doc.page_end}"],
                    detail=(
                        f"This entry is dated {doc.date}, {abs(year - median)} years from the "
                        f"rest of the file. Most often a two-digit year expanded the wrong "
                        f"way or a form revision code read as a date."
                    ),
                )
            )
    return warnings


def find_unattributed_records(bundle: PatientBundle) -> list[ConsistencyWarning]:
    """Clinical documents with no author at all.

    Not an error - plenty of forms are genuinely unsigned - but an entry a
    medico-legal reviewer cannot attribute is one they cannot weigh, so it is
    worth naming rather than leaving them to notice.
    """
    orphans = [
        doc
        for doc in bundle.documents
        if not doc.is_placeholder
        and doc.include_in_output
        and doc.bucket in {"clinical", "imaging"}
        and not (doc.author.name or "").strip()
    ]
    if not orphans:
        return []
    return [
        ConsistencyWarning(
            kind="unattributed_records",
            page_ranges=[f"{doc.page_start}-{doc.page_end}" for doc in orphans],
            detail=(
                f"{len(orphans)} clinical or imaging entr"
                f"{'y carries' if len(orphans) == 1 else 'ies carry'} no identifiable author. "
                "Check the signature block on those pages before relying on them."
            ),
        )
    ]


def reconcile(
    bundle: PatientBundle,
    paragraphs: list[SummaryParagraph],
    *,
    review_date_iso: str | None = None,
) -> list[ConsistencyWarning]:
    """Every whole-file check, in the order a reviewer would want to read them.

    Nothing here edits or removes a summary. A silently dropped medical finding
    is worse than a flagged one, so each check reports and lets a person decide.
    """
    return [
        *find_contradictions(paragraphs),
        *find_duplicate_studies(bundle),
        *find_absent_references(bundle),
        *find_temporal_outliers(bundle, review_date_iso=review_date_iso),
        *find_unattributed_records(bundle),
    ]
