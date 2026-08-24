"""The seeded Default rule configuration.

This is the data-driven translation of the behavior that used to be hardcoded
across `prompts.py`, `summary.py`, and `opinion.py`, and of the client's MASTER
UNIVERSAL GOLDEN RULE SET in `@docs/golden_rules.md`.

The seed is inserted once at startup when no seeded configuration exists; after
that the user owns and edits it from Rule Studio.
"""
from __future__ import annotations

from app.schemas.rules import DocumentRuleInput, OpinionTemplate, RuleAction, RuleConfigCreate

DEFAULT_CONFIG_NAME = "Default (Golden Rules)"

DEFAULT_GOLDEN_RULE_PROMPT = """GOLDEN RULES (locked mode). These govern every stage - page reading, summaries, and opinions - and override any habit to the contrary.

EVIDENCE
- Every statement must be traceable to the source pages. Never synthesize, infer, or speculate.
- Compress by deletion only. Do not reorganize the record and do not rephrase detail into a new claim.
- If a value cannot be found as printed, omit it. When uncertain, omit rather than assume.
- Never assume a document exists because it is referenced. A reference, an expectation, an earlier summary, or a typical workflow is not evidence that a document is present. If a referenced document is not physically in the file, say it is missing evidence rather than describing or reasoning from it.
- Never infer completeness from context, probability, or prior experience.
- Before treating an imaging record as an index entry only, look for an accession number, the exam title, a radiologist name or signature, and narrative headers (Clinical History, Technique, Findings, Impression), then sweep the surrounding pages for a faxed or scanned narrative report. If no narrative report is found, say so explicitly.

ROLE
- Summaries state fact. Opinions state analysis. Never blend the two.
- Professional, objective, neutral medico-legal tone. No advocacy, no emotive or persuasive language, no rhetorical questions.
- Plain text in every output. No markdown, no bullets, no headings, no emojis, no decorative formatting, no em dashes.

NAMING AND DATES
- Physicians and psychologists styled as doctors are "Dr. LastName". Everyone else is "FirstName LastName". Use one format throughout.
- The patient is "the claimant", and is never the author of a clinical document.
- Write dates in full, as printed in the source: January 11, 2026. Never guess a date and never reformat one from memory.

SCOPE
- Cover clinical documents, plus the standing exception for job descriptions, functional capacity documents, and other work-capacity documents.
- Exclude administrative, billing, consent, and fax cover material unless a rule says otherwise.
- Preserve the original file order. Every dated clinical entry must be accounted for; never drop an entry because it resembles another.

DEFINITIONS (apply exactly, never interchangeably)
- Symptom: a subjective complaint. Never by itself a restriction or a limitation.
- Contraindication: an activity that must be avoided completely because of a high risk of harm.
- Restriction: an activity that can be performed but should be avoided because of excess risk.
- Limitation: an objectively observed reduction in capability.
- Tolerance: the ability to sustain an activity. Not objectively measurable and often less than capacity."""


DEFAULT_SUMMARY_PRESENTATION = """How the Summary section is written. The golden rules still apply; this governs shape and wording only.

STRUCTURE
- One paragraph per document, in the file's original order.
- Hard paragraph returns only. No soft returns, no blank lines between paragraphs.
- Each paragraph carries one complete thought and ends there.

HOW EACH PARAGRAPH OPENS
- Open on the same line with the full date, then the document's own name, then the author, then continue the sentence from there.
- Write the date as "Month DD, YYYY" with a zero-padded day, whatever form it appears in on the page. "26-May-22", "26 May 2022" and "5/26/22" are all written "May 26, 2022". Never copy a date through in the form the page printed it.
- Name the document as it names itself. Write "physician's initial report form", "operative report", "pulmonary function report" - not a generic "clinical note" or "functional report" - and fall back to the generic label only when the page shows no name of its own.
- Write "March 01, 2023 attending physician statement by Dr. Pask indicates ...", never "March 1, 2023, Attending Physician Statement, Dr. Pask."
- No heading, no label, no bullet, and no colon after the author.
- Where no date could be read at all, open with the document's name and say plainly that it carries no legible date. Never open with the type alone and leave the reader to notice.

LENGTH
- Default to about four lines. A routine visit, a questionnaire, a form, or a single imaging study is one to four sentences.
- A type that is presented differently carries its own ceiling and wording instead of this one.

SUMMARIZE A FORM, DO NOT TRANSCRIBE IT
- A completed form is a source to read, not a table to copy out. Never walk its fields in order, and never reproduce a checklist item by item.
- Do not write parenthetical field values - "(yes)", "(no)", "(frequency 3, severity 2)", "marked absent" - and do not list the items that were left blank or answered in the ordinary way.
- Instead state what the form establishes, then name only the entries that carry weight: the ones marked severe, worsening, new, or abnormal, and any that bear on capacity for work. "The screening tool is positive for post-exertional symptom exacerbation, with symptoms worsening after both physical and mental effort and lasting more than a day" carries what twenty parenthetical ratings do not.
- Numbers stay exactly as printed when they are the finding - a score, a measured value, a ceiling on hours or weight. Ratings on a scale are not findings; report the pattern they show.

WORDING
- Plain connected clinical prose. Vary the connecting verbs; do not open every paragraph the same way.
- With no author identified, continue straight from the document name. Never write "by an unnamed author" or "by an unspecified author".
- State findings in the words of the record. Add no interpretation, significance, or commentary.
- Where a document repeats an earlier one, still give it its own paragraph with its own date.
"""


def default_rule_config() -> RuleConfigCreate:
    return RuleConfigCreate(
        name=DEFAULT_CONFIG_NAME,
        description=(
            "Seeded from the built-in golden rules: extractive summaries, "
            "impression-only imaging, lab placeholders, administrative pages "
            "used as referral context for the opinion."
        ),
        golden_rule_prompt=DEFAULT_GOLDEN_RULE_PROMPT,
        summary_presentation=DEFAULT_SUMMARY_PRESENTATION,
        # The ceiling for every type that has not opted into its own. Left
        # empty this fell through to a budget computed from the entry's page
        # and evidence count, which reached 500 words on a long form.
        summary_max_words=90,
        summary_prompt=None,
        opinion_prompt=None,
        opinion_template=OpinionTemplate.DISABILITY,
        is_default=True,
        rules=[
            DocumentRuleInput(
                document_type="clinical",
                match_prompt=(
                    "Consultations, clinical notes, referral letters, hospital records, "
                    "telephone interviews, case-management notes, attending physician and "
                    "physician's initial report forms, and member or patient-filled claim forms "
                    "containing symptoms, diagnoses, or medical history."
                ),
                action=RuleAction.EXTRACT,
                instruction_prompt=(
                    "Get the presenting complaint and its onset, the relevant history, objective "
                    "examination findings, investigations and their results, the assessment or "
                    "diagnosis, the treatment plan and medications, and any functional abilities, "
                    "restrictions, limitations, or return-to-work guidance. Leave out identifiers, "
                    "facility details, routine preparation, consent, and normal incidental findings."
                ),
                override_presentation=True,
                presentation_prompt=(
                    "A routine visit or follow-up runs one to three sentences; a substantial "
                    "consultation uses the full ceiling. Keep the opening format above."
                ),
                max_words=150,
            ),
            DocumentRuleInput(
                document_type="imaging",
                match_prompt=(
                    "Radiology reports (CT, MRI, X-ray, ultrasound, PET, mammography), "
                    "specimen radiography, and standalone diagnostic images such as "
                    "X-ray films, ECG tracings, or clinical photographs."
                ),
                action=RuleAction.EXTRACT,
                instruction_prompt=(
                    "Get the date of imaging, the modality and body region examined, and the "
                    "radiologist's impression. Leave out the technique, the contrast detail, and "
                    "normal incidental findings unless the impression turns on them."
                ),
                override_presentation=True,
                presentation_prompt=(
                    "One short paragraph: the date of imaging, the modality and body region, "
                    "then the radiologist's impression. Nothing else."
                ),
                max_words=50,
            ),
            DocumentRuleInput(
                document_type="pathology",
                match_prompt=(
                    "Lab blood and urine results, microbiology, and tissue histopathology "
                    "reports. The controlling date is the specimen or procedure date."
                ),
                action=RuleAction.SKIP,
            ),
            DocumentRuleInput(
                document_type="functional",
                match_prompt=(
                    "Functional abilities evaluations, functional capacity evaluations, "
                    "job descriptions, and standalone work-capacity or restrictions "
                    "documents (the standing exception - always included). A treating "
                    "physician's report form is clinical even when it states restrictions."
                ),
                action=RuleAction.EXTRACT,
                instruction_prompt=(
                    "Get the demonstrated abilities and the measured tolerances with their values, "
                    "the restrictions and limitations stated, any effort or validity testing, the "
                    "physical demands of the occupation where described, and the return-to-work "
                    "recommendation with hours and duties. Keep measured numbers exactly as printed."
                ),
                override_presentation=True,
                presentation_prompt=(
                    "Give this type the detail it needs, up to the full ceiling. Report measured "
                    "values as printed rather than characterizing them. Report the findings that "
                    "bear on capacity - not every item, score, and checkbox on the form. Keep the "
                    "opening format above."
                ),
                max_words=200,
            ),
            DocumentRuleInput(
                document_type="Other",
                match_prompt=(
                    "Fallback for any document that matched no other rule. Not detected by "
                    "the parser - it is applied to whatever is left over."
                ),
                action=RuleAction.EXTRACT,
                instruction_prompt=(
                    "Get the date, the kind of document, the author, and whatever clinical or "
                    "functional content it carries. Keep it short unless the document clearly "
                    "warrants more."
                ),
                override_presentation=True,
                presentation_prompt=(
                    "Keep it to a few sentences unless the document clearly warrants more. "
                    "Keep the opening format above."
                ),
                max_words=90,
            ),
            DocumentRuleInput(
                document_type="administrative",
                match_prompt=(
                    "Cover sheets, billing, consent forms, fax covers, and medical-file-review "
                    "referral or question forms addressed to the reviewing consultant."
                ),
                action=RuleAction.SKIP,
                use_as_context=True,
            ),
        ],
    )
