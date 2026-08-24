"""The seeded Default rule configuration.

This is the data-driven translation of the behavior that used to be
hardcoded across `prompts.py`, `summary.py`, and `opinion.py` (and of the
client's MASTER UNIVERSAL GOLDEN RULE SET in `@docs/golden_rules.md`):

  - pathology/lab reports are surfaced as cards but never summarized,
  - administrative pages are skipped in the summary yet still feed the
    opinion stage as referral/assignment context,
  - imaging is summarized as impression-only within ~50 words,
  - clinical documents get extractive summaries within ~200 words,
  - functional/work-capacity documents are always included (the standing
    exception).

The seed is inserted once at startup when no seeded configuration exists;
after that the user owns and edits it from Rule Studio.
"""
from __future__ import annotations

from app.schemas.rules import DocumentRuleInput, OpinionTemplate, RuleAction, RuleConfigCreate

DEFAULT_CONFIG_NAME = "Default (Golden Rules)"

DEFAULT_GOLDEN_RULE_PROMPT = """GOLDEN RULES (locked mode) - these govern every output:
- Extractive discipline: every statement must be traceable to the source pages. Never synthesize, infer, or speculate. Compress by deletion only: do not reorganize, do not rephrase into new claims. If a value cannot be found verbatim, omit it. When uncertain, omit rather than assume.
- Plain professional text only: no markdown, no bullets, no headings, no emojis, no decorative formatting, no em dashes.
- Paragraph structure: hard paragraph returns only, no soft returns, and no unnecessary blank lines. Each paragraph must represent one complete thought.
- Professional, objective, neutral medico-legal tone. No advocacy, no emotive or persuasive language, no rhetorical questions.
- Summaries are factual only. Opinions are analytical only. Never blend the two roles.
- Clinician naming: physicians and psychologists styled as doctors are "Dr. LastName"; everyone else is "FirstName LastName". Use one naming format consistently. The patient/claimant is never the author of a clinical document.
- Refer to the subject as "the claimant".
- Dates are written in full (e.g. January 11, 2026) and copied from the source, never guessed or reformatted from memory.
- Scope: summarize clinical documents plus the standing exception for job descriptions, functional capacity documents, and other work-capacity documents. Exclude administrative, billing, consent, and fax cover material unless instructed otherwise.
- Document presence: never assume a document exists because it is referenced. A reference, an expectation, a prior summary, or a typical workflow is not evidence that a document is present. If a referenced document is not physically in the file, state that it is missing evidence rather than describing or reasoning from it. Never infer completeness from context, probability, or prior experience.
- Imaging verification: before treating an imaging record as an index entry only, search for an accession number, the exam title, a radiologist name or signature, and narrative headers (Clinical History, Technique, Findings, Impression), then sweep the surrounding pages for a faxed or scanned narrative report. If no narrative report is located, state that explicitly.
- Standard definitions apply: symptoms are subjective complaints; a contraindication is an activity that must be completely avoided; a restriction is an activity that should be avoided due to excess risk; a limitation is an objectively observed reduction in capability; tolerance is psychophysiological and not objectively measurable.
- Preserve original file order. Every dated clinical entry must be accounted for; never omit an entry because it resembles another."""


def default_rule_config() -> RuleConfigCreate:
    return RuleConfigCreate(
        name=DEFAULT_CONFIG_NAME,
        description=(
            "Seeded from the built-in golden rules: extractive summaries, "
            "impression-only imaging, lab placeholders, administrative pages "
            "used as referral context for the opinion."
        ),
        golden_rule_prompt=DEFAULT_GOLDEN_RULE_PROMPT,
        summary_prompt=None,
        opinion_prompt=None,
        opinion_template=OpinionTemplate.DISABILITY,
        is_default=True,
        rules=[
            DocumentRuleInput(
                document_type="clinical",
                match_prompt=(
                    "Consultations, clinical notes, referral letters, hospital records, "
                    "telephone interviews, case-management notes, and member/patient-filled "
                    "claim forms containing symptoms, diagnoses, or medical history."
                ),
                action=RuleAction.EXTRACT,
                instruction_prompt=(
                    "Summarize the clinically important history, objective findings, "
                    "assessment, treatment plan, functional abilities, restrictions, "
                    "limitations, and return-to-work guidance."
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
                    "Report the date of imaging, the type of imaging, and the "
                    "radiologist's impression only."
                ),
                max_words=50,
            ),
            DocumentRuleInput(
                document_type="pathology",
                match_prompt=(
                    "Lab blood/urine results, microbiology, and tissue histopathology "
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
                    "Summarize demonstrated abilities, measured limitations, restrictions, "
                    "and return-to-work guidance in detail."
                ),
                max_words=500,
            ),
            DocumentRuleInput(
                document_type="administrative",
                match_prompt=(
                    "Cover sheets, billing, consent forms, fax covers, and medical-file-review "
                    "referral/question forms addressed to the reviewing consultant."
                ),
                action=RuleAction.SKIP,
                use_as_context=True,
            ),
        ],
    )
