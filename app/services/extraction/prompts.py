"""Prompts and JSON schemas for the new extraction package.

Two LLM calls only:
  1. Page parsing  -> Gemini, returns evidence-item array per page.
  2. Opinion build -> OpenAI, returns header (re-validation) + opinion only.

Summaries are NEVER LLM-generated - they are template-filled from the
`evidence` array using `summary.py`.
"""
from __future__ import annotations

from typing import Any


PAGE_PARSE_SYSTEM_PROMPT = """You are an extractive medico-legal document parser. You receive PDF page images as input. For each page you MUST return a JSON object that strictly follows the provided schema.

ABSOLUTE RULES (golden rules locked mode):
- Evidence-item style: every clinical detail goes into the `evidence` array as a SHORT VERBATIM phrase copied from the page. NEVER paraphrase. NEVER synthesize. NEVER infer.
- If you cannot find a value verbatim, omit it. Empty string for unknown text fields, false for unknown booleans, [] for empty arrays.
- Plain text only. No markdown. No bullets. No commentary outside the JSON.
- Dates must be copied verbatim as printed (e.g. "May 26, 2022" or "26-May-2022"). Do NOT reformat or guess.

PAGE CLASSIFICATION:
- `page_kind`: choose ONE of clinical, imaging, pathology, functional, admin, signature_only, empty.
  - imaging: radiology reports (CT, MRI, X-ray, US, PET, etc.).
  - pathology: lab specimens, biopsy, microbiology results.
  - functional: FAE/FCE/job description/work-capacity/restrictions documents.
  - admin: cover sheets, billing, consent, tracking pages, fax cover, blank logos, third-party correspondence with no clinical content.
  - signature_only: page contains only signature/credentials/closing of prior page.
  - empty: blank or near-blank page (logos, page numbers only).
  - clinical: anything else medical (consults, notes, referrals, hospital records, telephone interviews, case-management notes).
- `include_in_output`: true ONLY for clinical, imaging, pathology, functional, signature_only. False for admin and empty.

DOCUMENT BOUNDARIES:
- `starts_new_document`: true if THIS page begins a new physical document (new title block, new author letterhead, new patient, or different date).
- If the page is a continuation (same letterhead, same author, same date, same patient), set false.
- A signature-only page is NOT a new document.

PATIENT IDENTITY:
- `patient.name`: exact spelling and order as printed on this page (preserve original case). If the page only references a patient by another page, leave empty.
- `patient.dob`: copy verbatim. Strip a leading "DOB:" or "Date of Birth:" but keep the date as printed.
- `patient.identifier`: claim/file/MRN if visible.

DOCUMENT METADATA:
- `document.title`: the document's main title (e.g. "CT Brain w/o Contrast", "Medical Consultant Referral Form", "Functional Abilities Evaluation"). Use the PRIMARY title only.
- `document.bucket`: clinical | imaging | pathology | functional | administrative | unknown.
- `document.date`: the report/visit/specimen date PRINTED on this page. NOT the fax timestamp. NOT today's date. Copy verbatim.

AUTHOR / RECIPIENT:
- `author`: the person who WROTE/SIGNED this document. NOT the recipient.
  - `name`: copy as printed (e.g. "Carolyn Flegg", "Dr. Sarah Pask").
  - `credentials`: post-nominal letters as printed (MD, FRCPC, RN, etc.). Do NOT make a credential the name.
  - `is_doctor`: true only if the author has MD / DO / FRCPC / FRCP / DDS / DPM credentials, OR the page introduces them as "Dr.".
  - `is_signing`: true if the page contains their signature line.
- `recipient`: the person/entity the document is addressed TO ("Attention:", "To:"). Copy verbatim. NEVER swap recipient and author.
- The patient is NEVER the author. If the printed signer is the patient, leave author empty.
- Form-letter recipients ("Dear Doctor", "To Whom It May Concern") -> leave recipient.name empty.

HEADER FIELDS (claimant header data when visible on this page):
- `header_fields.to`, `from`, `claim_number`, `occupation`, `review_date`, `diagnosis_dod`.
- Only fill what is visibly printed on THIS page. Do not synthesize.

EVIDENCE ARRAY:
- Add an EvidenceItem for every clinically meaningful printed phrase. Each item:
  - `kind`: one of diagnosis, symptom, finding, measurement, medication, history, exam, impression, imaging_finding, imaging_impression, recommendation, restriction, limitation, return_to_work, hospitalization, onset, mechanism, investigation, score, checklist.
  - `text`: VERBATIM phrase from the page, ideally <= 25 words. Strip line breaks. Keep numbers/units exactly.
  - `value`: optional canonical value (e.g. "DLCO 59%", "MoCA 25/30") if helpful.
- For imaging pages, ALWAYS extract any phrase under FINDINGS (kind=imaging_finding) and IMPRESSION (kind=imaging_impression).
- For pathology, capture specimen/findings/diagnosis phrases.
- For clinical notes, capture: presenting complaint (symptom), onset, mechanism, history, exam findings, vitals (measurement), labs/PFT/scores (measurement|score), assessment (diagnosis|impression), plan (recommendation), medications, hospitalizations, restrictions/limitations/RTW.
- DO NOT include PII: addresses, phone numbers, fax numbers, email, OHIP/health card, SIN, payment info.
- DO NOT include filler ("Patient seen today", "Reviewed in clinic"). Only clinically substantive phrases.

`raw_text_excerpt`: optional short (<= 60 word) verbatim excerpt of the most substantive line on the page, used for debugging. Empty string if nothing relevant.
"""


PARSED_PAGES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page_number": {"type": "integer"},
                    "starts_new_document": {"type": "boolean"},
                    "include_in_output": {"type": "boolean"},
                    "page_kind": {
                        "type": "string",
                        "enum": [
                            "clinical",
                            "imaging",
                            "pathology",
                            "functional",
                            "admin",
                            "signature_only",
                            "empty",
                        ],
                    },
                    "patient": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "dob": {"type": "string"},
                            "identifier": {"type": "string"},
                        },
                    },
                    "document": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "bucket": {
                                "type": "string",
                                "enum": [
                                    "clinical",
                                    "imaging",
                                    "pathology",
                                    "functional",
                                    "administrative",
                                    "unknown",
                                ],
                            },
                            "date": {"type": "string"},
                        },
                    },
                    "author": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "credentials": {"type": "string"},
                            "is_doctor": {"type": "boolean"},
                            "is_signing": {"type": "boolean"},
                        },
                    },
                    "recipient": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "credentials": {"type": "string"},
                            "is_doctor": {"type": "boolean"},
                            "is_signing": {"type": "boolean"},
                        },
                    },
                    "header_fields": {
                        "type": "object",
                        "properties": {
                            "to": {"type": "string"},
                            "from": {"type": "string"},
                            "claim_number": {"type": "string"},
                            "occupation": {"type": "string"},
                            "review_date": {"type": "string"},
                            "diagnosis_dod": {"type": "string"},
                        },
                    },
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "diagnosis",
                                        "symptom",
                                        "finding",
                                        "measurement",
                                        "medication",
                                        "history",
                                        "exam",
                                        "impression",
                                        "imaging_finding",
                                        "imaging_impression",
                                        "recommendation",
                                        "restriction",
                                        "limitation",
                                        "return_to_work",
                                        "hospitalization",
                                        "onset",
                                        "mechanism",
                                        "investigation",
                                        "score",
                                        "checklist",
                                    ],
                                },
                                "text": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["kind", "text"],
                        },
                    },
                    "raw_text_excerpt": {"type": "string"},
                },
                "required": ["page_number", "page_kind", "evidence"],
            },
        }
    },
    "required": ["pages"],
}


OPINION_SYSTEM_PROMPT = """You are generating a medico-legal opinion and validating the patient header for a single patient bundle. Inputs include the deterministic header we already built, plus a list of cited evidence items extracted from the source PDF (each with its source phrase, kind, document title, page number, and author).

Return JSON with exactly two top-level fields: `header` and `opinion`.

HEADER VALIDATION:
- Use the provided header as the baseline. Override a field ONLY if you can see a better value in the cited evidence (e.g. clearer claim number, full review date, full occupation).
- `from_name`: the writer/signer of the primary review/consult. NEVER the recipient. NEVER the patient.
- `to_name`: the intended recipient of the primary correspondence.
- `age_dob`: full written date of birth ("May 4, 1966"). No "DOB:" prefix.
- `review_date`: full written date of the primary review ("January 11, 2026").
- `claimant`: full patient name as printed.
- `diagnosis_dod`: primary diagnosis and/or date of disability if visible.
- Keep "" for any field with no support.

OPINION RULES (Section 5 of golden rules):
- 3 to 5 short paragraphs. Plain English at Grade 11 to early-undergraduate level.
- First-person voice ("I").
- Evidence-based linear reasoning. Cite specific findings with author when given (e.g. "MoCA 25/30 (Dr. Zaluski)", "DLCO 59% predicted (Dr. Joanis)").
- Distinguish symptoms vs restrictions vs limitations vs tolerance vs contraindications.
- Identify missing objective evidence where relevant.
- Highlight discrepancies between providers when they exist.
- Note functional limitations and their clinical basis.
- DO NOT repeat the chronological summary document by document.
- DO NOT restate raw form fields, header data, fax timestamps, or administrative content.
- DO NOT add causation claims or significance statements beyond what providers explicitly stated.
- Forbidden phrases: "this suggests", "consistent with", "underscores", "highlights the need", "indicative of", "speaks to".
- Plain text only. No bullets, headings, markdown, italics, or bold.
- No emojis.
"""


OPINION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "header": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "to_name": {"type": "string"},
                "claim_number": {"type": "string"},
                "from_name": {"type": "string"},
                "age_dob": {"type": "string"},
                "review_date": {"type": "string"},
                "occupation": {"type": "string"},
                "claimant": {"type": "string"},
                "diagnosis_dod": {"type": "string"},
            },
            "required": [
                "to_name",
                "claim_number",
                "from_name",
                "age_dob",
                "review_date",
                "occupation",
                "claimant",
                "diagnosis_dod",
            ],
        },
        "opinion": {"type": "string"},
    },
    "required": ["header", "opinion"],
}
