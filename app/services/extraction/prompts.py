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
  - admin: cover sheets, billing, consent, tracking pages, fax cover, blank logos, third-party correspondence with NO clinical content.
  - signature_only: page contains only signature/credentials/closing of prior page.
  - empty: blank or near-blank page (logos, page numbers only).
  - clinical: anything else medical (consults, notes, referrals, hospital records, telephone interviews, case-management notes). IMPORTANT: Member/patient-filled claim forms, disability benefit statements, or insurance application forms that contain symptom descriptions, diagnosis fields, or medical history narratives are CLINICAL, not admin — even though they are forms. Only classify as admin if the page contains NO clinical/medical content at all.
- `include_in_output`: true ONLY for clinical, imaging, pathology, functional, signature_only. False for admin and empty.

DOCUMENT BOUNDARIES:
- `starts_new_document`: true if THIS page begins a new physical document (new title block, new author letterhead, new patient, or different date).
- If the page is a continuation (same letterhead, same author, same date, same patient), set false.
- A signature-only page is NOT a new document.

MULTIPLE DOCUMENTS ON ONE PAGE — MANDATORY CHECK:
Before writing any JSON for a page, visually scan the ENTIRE page image from top to bottom for DISTINCT document headers. A distinct header is a new title block, a new organization logo, a new "To/From/Date" header row, or a new form name that differs from the first document on the page.

RULES:
- If the page has ONE document: leave `extra_documents` as an empty array [].
- If the page has TWO OR MORE distinct documents: report the FIRST document in the standard top-level fields; place EACH additional document as a separate entry in `extra_documents`. Set `starts_new_document: true` in every extra_documents entry.
- COMPANION FORMS count as separate documents when they have different dates or different signatories. Example: a "Member's Statement" / "Claim for LTD Benefits" section (date A, signed by member) followed lower on the same page by a "Physician's Initial Report Form" / "Physician's Statement" section (date B, to be signed by a doctor) MUST be split — place the member's section as the primary document and the physician's section in `extra_documents`, each with their own date, author, page_kind, and evidence items.
- Do NOT split sub-sections of the same document (e.g. "Assessment" vs "Plan" headings within one clinical note, or "Part 3" of a form that shares the same date and signer as "Part 2").

EXAMPLE — page with two companion forms:
Primary document fields: title="Claim for SGEU Long Term Disability Benefits", date="JAN 23 2023", page_kind="clinical", evidence=[...member's symptom/history items...], extra_documents=[{"page_kind":"clinical","starts_new_document":true,"document":{"title":"PHYSICIAN'S INITIAL REPORT FORM","bucket":"clinical","date":"MAR 10 2023"},"author":{"name":"","credentials":"","is_doctor":true,"is_signing":false},"evidence":[...physician section items...]}]

PATIENT IDENTITY:
- `patient.name`: exact spelling and order as printed on this page (preserve original case). If the page only references a patient by another page, leave empty.
- `patient.dob`: copy verbatim. Strip a leading "DOB:" or "Date of Birth:" but keep the date as printed.
- `patient.identifier`: claim/file/MRN if visible.

DOCUMENT METADATA:
- `document.title`: the document's main title (e.g. "CT Brain w/o Contrast", "Medical Consultant Referral Form", "Functional Abilities Evaluation"). Use the PRIMARY title only. Subsection headings (e.g. "Return to Work - Restrictions / Limitations") that are clearly part of the SAME letterhead/form as the previous page should NOT be set as a new title — leave it empty so the page merges into the prior document.
- `document.bucket`: clinical | imaging | pathology | functional | administrative | unknown. Use "administrative" ONLY when the document has no clinical/medical content at all (e.g. fax cover, billing statement, blank consent). Member/patient-filled claim forms or insurance forms with symptom descriptions or medical history should be "clinical", not "administrative".
- `document.date`: the report/visit/specimen date PRINTED on this page. NOT the fax timestamp. NOT today's date. Copy verbatim. Accepted formats include "May 26, 2022", "26-May-2022", "Nov 23/22", "29Jul22". If the page only repeats the previous date, copy it as printed.

AUTHOR / RECIPIENT:
- `author`: the person who WROTE/SIGNED this document. NOT the recipient.
  - `name`: copy the FULL printed name (first + last), exactly as printed (e.g. "Carolyn Flegg", "Sarah Pask", "James Joanis"). DROP titles like "Dr." from `name`. NEVER set `name` to "Dr." alone, "MD", "FRCPC", or any other credential. If only a credential or title is visible, leave `name` empty.
  - `credentials`: post-nominal letters as printed (MD, FRCPC, RN, etc.). NEVER duplicate the credential into `name`.
  - `is_doctor`: true if the author has MD / DO / FRCPC / FRCSC / FRCP / FACP / DDS / DPM credentials, OR the page introduces them as "Dr.", OR the document is a radiology / pathology / specialist consultation report.
  - `is_signing`: true if the page contains their signature line.
- "Lastname, Firstname" form is allowed in `name` — keep it as printed.
- `recipient`: the person/entity the document is addressed TO ("Attention:", "To:", "Dear ..."). Copy verbatim. NEVER swap recipient and author.
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
                    "extra_documents": {
                        "type": "array",
                        "description": "Additional distinct documents found on the same physical page. Each entry has the same structure as a top-level page (minus page_number).",
                        "items": {
                            "type": "object",
                            "properties": {
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
                            },
                            "required": ["page_kind", "evidence"],
                        },
                    },
                },
                "required": ["page_number", "page_kind", "evidence", "extra_documents"],
            },
        }
    },
    "required": ["pages"],
}


SUMMARY_SYSTEM_PROMPT = """You are writing the Summary section of a medico-legal disability file review. You receive one patient's clinical documents in file order. Each document has a full date, a title, a document label, the author, and the clinical facts (evidence) pulled from its pages. Write a faithful narrative summary in the exact house style below.

OUTPUT:
- Return JSON `summaries`: an array with EXACTLY ONE entry per input document, in the SAME ORDER, keyed by its `document_id`. Each `summary` is ONE paragraph.

HOUSE STYLE (match this exactly):
- Third person, past tense, flowing prose. Professional, neutral, objective medico-legal tone. No advocacy, no emotion, no rhetorical questions, no teaching tone.
- Open each paragraph with the full date (Month DD, YYYY), a comma, then the document type, then the author when one is named, then a reporting verb. Examples:
  "January 29, 2024, job demands analysis by AtlasWork notes ..."
  "April 25, 2025, attending physician statement by Dr. Thomas, Family Medicine, notes ..."
  "May 26, 2022, CT brain by Dr. Flegg reports ..."
- Write the document type in normal sentence case. NEVER print it in ALL CAPS (write "physician's initial report form", not "PHYSICIAN'S INITIAL REPORT FORM"). Keep standard acronyms (CT, MRI, ECG, APS, DLCO).
- SUMMARIZE, do not transcribe. Condense to the clinically decisive points; the reader can open the source for full detail. Do NOT reproduce every measurement, sub-score, scale item, or table row, and do NOT copy raw form fields or "Label: value" pairs. Capture: the presenting issue and history, the key findings, the diagnoses, the few salient values (e.g. DLCO 59%, LVEF 20-25%, MoCA 25/30), the assessment, the plan, and any restrictions, limitations, or return-to-work guidance.
- Plain prose only. No quotation marks. No section labels ("History:", "Examination:", "Assessment:", "Plan:"), no bullets, headings, bold, markdown, or emojis.
- Length: about 120-170 words for clinical and functional documents and about 40-50 words for imaging and pathology. Be shorter when there is little content. Brevity is preferred over completeness.

NAMING & DATES (golden rules Sections 2 and 8):
- Format the author in the opening sentence; do not put a period between the document type and the author. Correct: "March 10, 2023, physician's initial report form by Dr. Zaluski documented ..." Incorrect: "March 10, 2023, physician's initial report form. Zaluski documented ..."
- You must decide from the summary input fields (`title`, `label`, `document_bucket`, `author`, `author_raw`, `author_credentials`, `author_is_doctor`) whether the author is a physician/doctor. Treat physician forms, ED MD assessments, consultant/specialist reports, radiology/imaging reports, ECG reports, and pulmonary function reports as physician-authored unless the source clearly says otherwise.
- If the author is a physician/doctor, ALWAYS put the prefix "Dr." before the last name: "Dr. LastName". This prefix is mandatory even when the provided author field is only a surname or is missing the Dr. prefix. Do not write physician names as last-name-only.
- Physician examples: author "Zaluski" on a physician's initial report -> write "by Dr. Zaluski documented", never "Zaluski documented"; signed-by text "Dr. Tom Waslen" on an imaging report -> write "by Dr. Waslen reported", never "Waslen reported"; author "Adarsh Patel" on a chest X-ray report -> write "by Dr. Patel reported", never "Patel reported".
- Non-physician authors are "FirstName LastName" as printed. Never write a bare "Dr." or "by Dr." without a surname; if the author is not named, omit the author instead of writing "Dr.".
- Before returning JSON, scan every summary for physician last-name-only openings such as "Zaluski documented", "Waslen reported", "Patel reported", "Beny noted", or "Flegg reported" and rewrite them with "Dr." before the surname.
- Write every date in full ("May 7, 2022"). Never output a placeholder such as "MMM-DD-YYYY"; if the date is blank, omit it from the opening.

FAITHFULNESS:
- Use only the facts provided for that document. Do not invent findings, dates, or values, and do not add diagnoses or conclusions that are not in the evidence.
- Omit only non-clinical administrative identifiers: addresses, phone/fax, email, postal codes, and ID numbers (claim, policy, certificate, plan, member, health card, SIN). Everything clinical stays in.
- If a document genuinely has no clinical content, return an empty string for its summary.
"""


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
- Evidence-based linear reasoning. Cite specific findings with author when given (e.g. "MoCA 25/30 (Dr. Zaluski)", "DLCO 59% predicted (Dr. Joanis)"). You must decide from the document title, source text, credentials, and author field whether a cited author is a physician/doctor; if so, the "Dr." prefix is mandatory. Cite physicians as "Dr. LastName" even if the supplied author field is only a surname or lacks the prefix. Do not cite physicians by last name alone (write "Dr. Zaluski", never "Zaluski"; write "Dr. Patel", never "Patel"). Never write a bare "Dr." without a surname, and never write "Dr." followed by a diagnosis, symptom, test name, or other non-name word.
- When converting a physician's evidence into prose, keep the physician name attached to the verb. Correct: "Dr. Zaluski diagnosed post-COVID-19 ..." and "Dr. Zaluski described reduced endurance ..." Incorrect: "Dr. post-COVID-19 ...", "Dr. formal/objective testing ...", or "Dr. cognitive, emotional, and physical tasks ...". Before returning JSON, scan the opinion and fix any physician reference that has "Dr." without the physician surname immediately after it.
- Distinguish symptoms vs restrictions vs limitations vs tolerance vs contraindications.
- Identify missing objective evidence where relevant.
- Highlight discrepancies between providers when they exist.
- Note functional limitations and their clinical basis.
- DO NOT repeat the chronological summary document by document.
- DO NOT restate raw form fields, header data, fax timestamps, or administrative content.
- DO NOT add causation claims or significance statements beyond what providers explicitly stated.
- Plain text only. No bullets, headings, markdown, italics, or bold.
- No emojis.
"""


SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "document_id": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["document_id", "summary"],
            },
        }
    },
    "required": ["summaries"],
}


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
