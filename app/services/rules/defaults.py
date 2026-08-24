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

DEFAULT_GOLDEN_RULE_PROMPT = """GOLDEN RULES (locked mode). These govern every output and override any habit to the contrary.

EVIDENCE
- Every statement must be traceable to the source pages. Never synthesize, infer, or speculate.
- Compress by deletion only. Do not reorganize the record and do not rephrase detail into a new claim.
- If a value cannot be found as printed, omit it. When uncertain, omit rather than assume.
- Never assume a document exists because it is referenced. A reference, an expectation, an earlier summary, or a typical workflow is not evidence that a document is present. If a referenced document is not physically in the file, say it is missing evidence rather than describing or reasoning from it.
- Never infer completeness from context, probability, or prior experience.

WRITING
- Plain professional text only. No markdown, no bullets, no headings, no emojis, no decorative formatting, no em dashes.
- Hard paragraph returns only. No soft returns and no unnecessary blank lines. Each paragraph carries one complete thought.
- Professional, objective, neutral medico-legal tone. No advocacy, no emotive or persuasive language, no rhetorical questions.
- Summaries state fact. Opinions state analysis. Never blend the two roles.

NAMING AND DATES
- Physicians and psychologists styled as doctors are "Dr. LastName". Everyone else is "FirstName LastName". Use one format throughout.
- The patient is "the claimant", and is never the author of a clinical document.
- Write dates in full, as printed in the source: January 11, 2026. Never guess a date and never reformat one from memory.

SCOPE
- Summarize clinical documents, plus the standing exception for job descriptions, functional capacity documents, and other work-capacity documents.
- Exclude administrative, billing, consent, and fax cover material unless a rule says otherwise.
- Preserve the original file order. Every dated clinical entry must be accounted for; never drop an entry because it resembles another.

IMAGING VERIFICATION
- Before treating an imaging record as an index entry only, look for an accession number, the exam title, a radiologist name or signature, and narrative headers (Clinical History, Technique, Findings, Impression), then sweep the surrounding pages for a faxed or scanned narrative report. If no narrative report is found, say so explicitly.

DEFINITIONS (apply exactly, never interchangeably)
- Symptom: a subjective complaint. Never by itself a restriction or a limitation.
- Contraindication: an activity that must be avoided completely because of a high risk of harm.
- Restriction: an activity that can be performed but should be avoided because of excess risk.
- Limitation: an objectively observed reduction in capability.
- Tolerance: the ability to sustain an activity. Not objectively measurable and often less than capacity."""


DEFAULT_SUMMARY_PRESENTATION = """How the finished summary should read:
- One paragraph per document, in the file's original order.
- Open on the same line with the full date, then the document type, then the author, then continue the sentence from there. No heading, no label, no bullet, no colon after the author.
- Write "March 01, 2023 attending physician statement by Dr. Pask indicates ...", never "March 1, 2023, Attending Physician Statement, Dr. Pask."
- Keep each paragraph to one complete thought in plain connected prose. Vary the connecting verbs; do not open every paragraph the same way.
- State findings in the words of the record. Do not add interpretation, significance, or commentary.
- Where a document adds nothing beyond an earlier one, still give it its own paragraph with its own date."""


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
        summary_prompt=None,
        opinion_prompt=None,
        opinion_template=OpinionTemplate.DISABILITY,
        is_default=True,
        rules=[
            DocumentRuleInput(
                document_type="clinical",
                match_prompt=(
                    "Consultations, clinical notes, referral letters, hospital records, "
                    "telephone interviews, case-management notes, and member or patient-filled "
                    "claim forms containing symptoms, diagnoses, or medical history."
                ),
                action=RuleAction.EXTRACT,
                instruction_prompt=(
                    "Get the presenting complaint and its onset, the relevant history, objective "
                    "examination findings, investigations and their results, the assessment or "
                    "diagnosis, the treatment plan and medications, and any functional abilities, "
                    "restrictions, limitations, or return-to-work guidance. Leave out identifiers, "
                    "facility details, routine preparation, consent, and normal incidental findings."
                ),
                max_words=200,
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
                    "job descriptions, work-capacity and restrictions documents "
                    "(the standing exception - always included)."
                ),
                action=RuleAction.EXTRACT,
                instruction_prompt=(
                    "Get the demonstrated abilities and the measured tolerances with their values, "
                    "the restrictions and limitations stated, any effort or validity testing, the "
                    "physical demands of the occupation where described, and the return-to-work "
                    "recommendation with hours and duties. Keep measured numbers exactly as printed."
                ),
                max_words=500,
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
                max_words=150,
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
