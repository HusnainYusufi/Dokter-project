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
