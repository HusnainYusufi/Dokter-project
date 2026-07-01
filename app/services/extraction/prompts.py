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
  - imaging: radiology reports (CT, MRI, X-ray, US, PET, mammography, etc.). This INCLUDES specimen radiography / specimen imaging and any report titled "Diagnostic Imaging ... Specimen Report" — an X-ray of a biopsy specimen is imaging, NOT pathology. When a page header says "Diagnostic Imaging", classify it as imaging.
  - pathology: lab blood/urine results, microbiology, and tissue HISTOpathology (the microscopic tissue diagnosis). A specimen X-ray/radiograph is imaging, not pathology.
  - functional: FAE/FCE/job description/work-capacity/restrictions documents.
  - admin: cover sheets, billing, consent, tracking pages, fax cover, blank logos, third-party correspondence with NO clinical content.
  - signature_only: page contains only signature/credentials/closing of prior page.
  - empty: blank or near-blank page (logos, page numbers only).
  - clinical: anything else medical (consults, notes, referrals, hospital records, telephone interviews, case-management notes). IMPORTANT: Member/patient-filled claim forms, disability benefit statements, or insurance application forms that contain symptom descriptions, diagnosis fields, or medical history narratives are CLINICAL, not admin — even though they are forms. Only classify as admin if the page contains NO clinical/medical content at all.
- `include_in_output`: true ONLY for clinical, imaging, pathology, functional, signature_only. False for admin and empty.

NOISY / PARTIAL PAGES (messy scans — read carefully):
- These bundles are messy: blank pages, scanner noise, full-page photographs, radiographic images, faxed half-pages that show only a date or a page number, and trailing signature blocks are all common.
- TRULY EMPTY pages only: a page that is blank, near-blank, or shows ONLY a logo, a page number, a fax timestamp, or a stray date with no other content MUST be page_kind=empty, include_in_output=false, starts_new_document=false, evidence=[]. Do NOT invent a document for it and do NOT copy a stray date into `document.date`.
- MEDICAL IMAGES ARE NOT EMPTY: a page that is a full-page diagnostic image or clinical picture — an X-ray/CT/MRI/ultrasound film, an ECG tracing, or a clinical photograph of the patient or an injury — MUST be page_kind=imaging, include_in_output=true. Even when it carries little or no typed text, add at least one evidence item (kind=imaging_finding) briefly describing what is shown, e.g. "Chest radiograph image" or "Clinical photograph of the lower face". Imaging like X-rays is required and must never be dropped as empty.
- CONTINUATION & SIGNATURE PAGES: a page that only closes the previous document — an "Electronic Signatures" / "Signed by" block, a "page 2 of 2" tail, or narrative spilling over from the prior page — is page_kind=signature_only, starts_new_document=false. It continues the previous document and is NEVER a new document, even though it looks sparse and may repeat the prior author and date.

DOCUMENT BOUNDARIES:
- `starts_new_document`: true ONLY when THIS page clearly begins a new physical document — a new title block, a new author letterhead, a new patient, or a clearly different report date. Be conservative: when in doubt, set false and let the page merge into the running document. A new document is a positive signal you can see, never a guess from sparse or noisy pages.
- If the page is a continuation (same letterhead, same author, same date, same patient), or carries no header of its own, set false.
- A signature-only page, a blank page, or a date-only fragment is NEVER a new document.
- Do NOT start a new document merely because a page looks different, is rotated, is low quality, or is partially cut off.

MULTIPLE DOCUMENTS ON ONE PAGE — MANDATORY CHECK:
Before writing any JSON for a page, visually scan the ENTIRE page image from top to bottom for DISTINCT document headers. A distinct header is a new title block, a new organization logo, a new "To/From/Date" header row, or a new form name that differs from the first document on the page.

RULES:
- If the page has ONE document: leave `extra_documents` as an empty array [].
- If the page has TWO OR MORE distinct documents: report the FIRST document in the standard top-level fields; place EACH additional document as a separate entry in `extra_documents`. Set `starts_new_document: true` in every extra_documents entry.
- COMPANION FORMS count as separate documents when they have different dates or different signatories. Example: a "Member's Statement" / "Claim for LTD Benefits" section (date A, signed by member) followed lower on the same page by a "Physician's Initial Report Form" / "Physician's Statement" section (date B, to be signed by a doctor) MUST be split — place the member's section as the primary document and the physician's section in `extra_documents`, each with their own date, author, page_kind, and evidence items.
- REPEAT VISIT NOTES count as separate documents too, even when they use the IDENTICAL template/form and the SAME clinician. A clinic chart export commonly prints one visit's note ending partway down a page, immediately followed by the next visit's note beginning on the same page (recognizable by a repeated header block such as "Patient's Name:"/"Date of treatment:"/"Scale from 0 to 10" starting again, or a new "Appointment Date:" line). Each distinct visit date on the page is its own entry: the first in the primary fields, each later one in `extra_documents` with its own date and evidence — do NOT merge two different visit dates' findings into one entry's evidence array just because the template repeats.
- Do NOT split sub-sections of the same single visit (e.g. "Assessment" vs "Plan" headings within one clinical note, or "Part 3" of a form that shares the same date and signer as "Part 2").
- Do NOT split a single report that covers multiple body regions into separate documents. One radiology/imaging report that reads several areas (e.g. sinuses, chest, and spine in one report on the same date by the same author) is ONE document - keep all its regions in the primary entry, never in extra_documents.

EXAMPLE — page with two companion forms:
Primary document fields: title="Claim for SGEU Long Term Disability Benefits", date="JAN 23 2023", page_kind="clinical", evidence=[...member's symptom/history items...], extra_documents=[{"page_kind":"clinical","starts_new_document":true,"document":{"title":"PHYSICIAN'S INITIAL REPORT FORM","bucket":"clinical","date":"MAR 10 2023"},"author":{"name":"","credentials":"","is_doctor":false,"is_signing":false},"evidence":[...physician section items...]}]

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
  - `name`: copy the FULL printed name (first + last), exactly as printed (e.g. "Carolyn Flegg", "Sarah Pask", "James Joanis"). DROP titles like "Dr." from `name`. NEVER set `name` to "Dr." alone, "MD", "FRCPC", or any other credential. If only a credential or title is visible, leave `name` empty. For radiology/imaging/ECG/PFT reports, check signature and report metadata lines such as "Electronically signed by", "Reported", "Interpreted by", and "Dictated by" for the author name; capture the printed physician name when present.
  - `credentials`: post-nominal letters as printed (MD, FRCPC, RN, etc.). NEVER duplicate the credential into `name`.
  - `is_doctor`: true if `author.name` contains a usable person name AND the author has MD / DO / FRCPC / FRCSC / FRCP / FACP / DDS / DPM credentials, OR the page introduces that named author as "Dr.", OR a named author is shown on a radiology / pathology / specialist consultation report. If no author name is visible, set `is_doctor` false even when the document type is physician-authored.
  - `is_signing`: true if the page contains their signature line.
- "Lastname, Firstname" form is allowed in `name` — keep it as printed.
- MULTI-CLINICIAN LETTERHEAD: a clinic letterhead often lists several physicians at the top (e.g. "Dr. Frenette, Dr. Stone, Dr. Vair"). The author is the ONE who SIGNED the document (signature/closing line), NOT the first name in that list, and NEVER all of them concatenated. If you cannot identify the single signer, leave `author.name` empty rather than guessing the first listed name.
- `recipient`: the person/entity the document is addressed TO ("Attention:", "To:", "Dear ...", the inside address block). Copy verbatim. NEVER swap recipient and author. On a consult/referral letter the recipient is the referring or family physician (e.g. "Dear Dr. Simon") — capture them here.
- The patient/claimant is NEVER the author. If the printed signer is the patient, leave `author` empty.
- CLAIMANT-AUTHORED CORRESPONDENCE: when the WRITER is the patient/claimant — a complaint, a personal statement, or a request to correct their own medical records — leave `author` empty and set `document.title` to describe it (e.g. "Letter from the claimant", "Patient statement"). Keep page_kind=clinical when it recounts the patient's own injuries or symptoms; this content is still captured, just never attributed to the patient as a clinician.
- Form-letter recipients ("Dear Doctor", "To Whom It May Concern") -> leave recipient.name empty.

HEADER FIELDS (claimant header data when visible on this page):
- `header_fields.to`, `from`, `claim_number`, `occupation`, `review_date`, `diagnosis_dod`.
- Only fill what is visibly printed on THIS page. Do not synthesize.
- Capture the VALUE only, never the printed field label. For a line like "Current Diagnosis: Long haul COVID" or "Diagnosis: PTSD", set `diagnosis_dod` to "Long haul COVID" / "PTSD" - drop the "Current Diagnosis:" / "Diagnosis:" / "Dx:" / "Impression:" label.

EVIDENCE ARRAY:
- Add an EvidenceItem for every clinically meaningful printed phrase. Each item:
  - `kind`: one of diagnosis, symptom, finding, measurement, medication, history, exam, impression, imaging_finding, imaging_impression, recommendation, restriction, limitation, return_to_work, hospitalization, onset, mechanism, investigation, score, checklist.
  - `text`: VERBATIM phrase from the page, ideally <= 25 words. Strip line breaks. Keep numbers/units exactly. When a value is printed after a field label, capture the value only and drop the label (e.g. "Long haul COVID", not "Current Diagnosis: Long haul COVID").
  - `value`: optional canonical value (e.g. "DLCO 59%", "MoCA 25/30") if helpful.
- For imaging pages, ALWAYS extract any phrase under FINDINGS (kind=imaging_finding) and IMPRESSION (kind=imaging_impression).
- For pathology, capture specimen/findings/diagnosis phrases.
- For clinical notes, capture: presenting complaint (symptom), onset, mechanism, history, exam findings, vitals (measurement), labs/PFT/scores (measurement|score), assessment (diagnosis|impression), plan (recommendation), medications, hospitalizations, restrictions/limitations/RTW.
- DO NOT include PII: addresses, phone numbers, fax numbers, email, OHIP/health card, SIN, payment info.
- DO NOT include filler ("Patient seen today", "Reviewed in clinic"). Only clinically substantive phrases.

`raw_text_excerpt`: optional short (<= 60 word) verbatim excerpt of the most substantive line on the page, used for debugging. Empty string if nothing relevant.

PAGE MARKDOWN RECONSTRUCTION (`markdown`):
- For every page, FIRST reconstruct the full page as faithful GitHub-flavored markdown, then derive the structured fields and `evidence` from that reconstruction. This layout-aware pass is what keeps tables, forms, and figures from being lost.
- Preserve reading order and structure: use headings for title/letterhead blocks, paragraphs for prose, and render any TABLE as a real markdown table (header row + rows) with the cell values copied verbatim. Keep checkbox/selection states (e.g. [x] Yes, [ ] No) and "Label: value" form fields as written.
- For an image, figure, X-ray, scan, ECG tracing, or clinical photograph, insert a short bracketed description in place, e.g. "![Chest radiograph]" or "![Clinical photograph of the lower face]" - never leave an image page's markdown empty.
- Transcribe verbatim. Do NOT summarize, infer, translate, or add anything not printed. Strip only true PII (addresses, phone/fax, email, health-card/SIN numbers). Keep it to this one page.
"""


# OpenAI structured outputs "strict" mode constrains token generation to the
# schema grammar at decode time, so the model literally cannot emit malformed
# JSON or a degenerate/repetitive blob - this replaced a loose json_object
# mode after a production page-parse batch call returned thousands of
# repeated whitespace characters instead of page data. Strict mode requires
# every object to set additionalProperties=False and to list ALL of its
# properties in `required` (there is no "optional" - a field that may be
# unknown uses an empty-string/false sentinel or a nullable type instead),
# which is exactly the convention PAGE_PARSE_SYSTEM_PROMPT already documents.
_PAGE_KIND_ENUM = [
    "clinical",
    "imaging",
    "pathology",
    "functional",
    "admin",
    "signature_only",
    "empty",
]

_DOCUMENT_BUCKET_ENUM = [
    "clinical",
    "imaging",
    "pathology",
    "functional",
    "administrative",
    "unknown",
]

_EVIDENCE_KIND_ENUM = [
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
]

_EVIDENCE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": _EVIDENCE_KIND_ENUM},
        "text": {"type": "string"},
        "value": {"type": ["string", "null"]},
    },
    "required": ["kind", "text", "value"],
}

_PERSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "credentials": {"type": "string"},
        "is_doctor": {"type": "boolean"},
        "is_signing": {"type": "boolean"},
    },
    "required": ["name", "credentials", "is_doctor", "is_signing"],
}

_PATIENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "dob": {"type": "string"},
        "identifier": {"type": "string"},
    },
    "required": ["name", "dob", "identifier"],
}

_DOCUMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "bucket": {"type": "string", "enum": _DOCUMENT_BUCKET_ENUM},
        "date": {"type": "string"},
    },
    "required": ["title", "bucket", "date"],
}

# Mirrors a top-level page's author/recipient/document/evidence fields - a
# companion document found on the same physical page (e.g. a repeat visit
# note stacked below the first) is otherwise indistinguishable from a fresh
# top-level page except for page_number. Recipient was previously missing
# here entirely, which meant a recurring same-provider chart that happened to
# stack two visits on one page always lost recipient continuity at that
# page, defeating the boundary heuristic that keeps the whole chart as one
# document with per-visit sub-entries instead of fragmenting into many.
_EXTRA_DOCUMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "starts_new_document": {"type": "boolean"},
        "include_in_output": {"type": "boolean"},
        "page_kind": {"type": "string", "enum": _PAGE_KIND_ENUM},
        "patient": _PATIENT_SCHEMA,
        "document": _DOCUMENT_SCHEMA,
        "author": _PERSON_SCHEMA,
        "recipient": _PERSON_SCHEMA,
        "evidence": {"type": "array", "items": _EVIDENCE_ITEM_SCHEMA},
    },
    "required": [
        "starts_new_document",
        "include_in_output",
        "page_kind",
        "patient",
        "document",
        "author",
        "recipient",
        "evidence",
    ],
}

PARSED_PAGES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "page_number": {"type": "integer"},
                    "starts_new_document": {"type": "boolean"},
                    "include_in_output": {"type": "boolean"},
                    "page_kind": {"type": "string", "enum": _PAGE_KIND_ENUM},
                    "patient": _PATIENT_SCHEMA,
                    "document": _DOCUMENT_SCHEMA,
                    "author": _PERSON_SCHEMA,
                    "recipient": _PERSON_SCHEMA,
                    "header_fields": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "to": {"type": "string"},
                            "from": {"type": "string"},
                            "claim_number": {"type": "string"},
                            "occupation": {"type": "string"},
                            "review_date": {"type": "string"},
                            "diagnosis_dod": {"type": "string"},
                        },
                        "required": [
                            "to",
                            "from",
                            "claim_number",
                            "occupation",
                            "review_date",
                            "diagnosis_dod",
                        ],
                    },
                    "evidence": {"type": "array", "items": _EVIDENCE_ITEM_SCHEMA},
                    "raw_text_excerpt": {"type": "string"},
                    "markdown": {"type": "string"},
                    "extra_documents": {
                        "type": "array",
                        "description": "Additional distinct documents found on the same physical page. Each entry has the same structure as a top-level page (minus page_number).",
                        "items": _EXTRA_DOCUMENT_SCHEMA,
                    },
                },
                "required": [
                    "page_number",
                    "starts_new_document",
                    "include_in_output",
                    "page_kind",
                    "patient",
                    "document",
                    "author",
                    "recipient",
                    "header_fields",
                    "evidence",
                    "raw_text_excerpt",
                    "markdown",
                    "extra_documents",
                ],
            },
        }
    },
    "required": ["pages"],
}


SUMMARY_SYSTEM_PROMPT = """You are writing the Summary section of a medico-legal disability file review for an expert medical consultant. You receive one patient's UNITS in file order. A unit is either a whole simple document, or - when a larger multi-page record (e.g. a hospital chart binder) legitimately holds several distinct dated entries - one single dated encounter drawn from that larger record. Each unit carries a date, a title, a document label, an author, a recipient, the clinical facts (evidence) drawn from its pages, and `is_multi_unit_document` (true when this unit is one of several siblings drawn from the same larger record). Treat every unit exactly the same way, as if it were its own document - write a faithful, very brief summary for it - that lets a busy consultant grasp it at a glance. Use your clinical judgement; the points below are principles, not a rigid template.

OUTPUT
- Return JSON `summaries`: exactly one entry per input unit, in the same order, keyed by `subsection_id`. Each `summary` is one paragraph of plain prose.
- EVERY UNIT MUST BE COVERED. You cannot see a unit's sibling units (other dated entries from the same larger record) - write each unit's summary as if it were the only thing available about that encounter. Never omit a unit's summary because you suspect a sibling unit already covers it; each one is graded independently.
- Return an EMPTY string only for a unit with no clinical value to this patient at all - consent, authorization, or release-of-information forms, fax covers, billing, and blank or template-only pages. Do not write a paragraph just to say nothing was documented. Do NOT return empty merely because a unit looks similar to what a sibling unit might contain - every unit with any clinical content gets a summary.
- IMAGES ARE CONTENT, NOT EMPTY. An evidence item or markdown fragment written as `![description]` marks an image, figure, X-ray, scan, or clinical photograph that is part of this unit - it is the indicator that an image sits there. Treat it as clinical content: weave the image and what it shows into the summary in context with any surrounding text (e.g. "...with a clinical photograph of the lower face."). For a unit that is only an image, give the one-line description of the image. NEVER return an empty string just because a unit is, or contains, an image or photograph.

WHAT A GOOD SUMMARY DOES
- Leads with what matters: open with the full date (Month DD, YYYY), the document type, and the author (or recipient for correspondence), then immediately give the most decisive point - the diagnosis or reason for the encounter and its key finding, result, or recommendation. The first sentence should carry the headline.
- Summarizes, never transcribes. Capture the clinically decisive facts - presenting problem, key findings, diagnoses, the few salient values (e.g. DLCO 59%, MoCA 25/30, PCL-5 74), the assessment, the plan, and any restrictions, limitations, or return-to-work guidance. Let go of routine detail, boilerplate, normal-variant or incidental findings, and raw "label: value" form fields. For screening tools and questionnaires, give the patient's actual results and overall score, not the instrument's generic instructions.
- CONDENSE LISTS. Never enumerate restrictions, limitations, cognitive sub-ratings, screening items, or scale sub-scores one by one; collapse them into the overall picture in a clause or two (e.g. "moderate cognitive limitations and a 5 lb lifting limit", not the full per-item rating list).
- KEEP IT VERY SHORT - this is the top priority and overrides the urge to be thorough. For a simple/sole unit (`is_multi_unit_document` is false): ONE sentence, or at most two; about 25 to 45 words. For one dated entry drawn from a larger multi-encounter record (`is_multi_unit_document` is true): up to about 4 short lines, roughly 50 to 80 words - still tight, just enough room to name this entry's own date/author/finding without leaning on a sibling entry. Either way this is a hard ceiling: state only the decisive facts and drop everything else - the consultant opens the source for detail. Never pad; never list findings, restrictions, sub-scores, or table rows.
- Reads as crisp, precise clinical English: short declarative sentences, active voice, correct medical terms, related findings merged rather than strung together with repeated "and ... and ...". Neutral medico-legal tone - no advocacy, emotion, rhetorical questions, or teaching. Plain prose only: no quotation marks, section labels, bullets, headings, bold, markdown, or emojis. Write entirely in English using the Latin alphabet; never emit a word or character from another language or script.

NAMING (golden rules 2 and 8)
- Decide from `author`, `author_raw`, `author_credentials`, and `author_is_doctor` whether a real author name is present and whether that person is a physician. A name must come from the author fields - never turn a diagnosis, body part, or test name into a name.
- Physicians are written "Dr. LastName", keeping surname particles (e.g. "Dr. du Rand"); other clinicians as printed ("FirstName LastName"). Apply one naming style consistently. If no usable author name exists, omit the author entirely rather than writing a bare "Dr." or guessing - this includes physician forms, ED and specialist reports, and radiology/ECG/PFT reports. The patient/claimant is never an author.
- Correspondence and consult letters: state who the document is for using `recipient`, e.g. "consultation letter to Dr. Simon by Dr. Frenette reported ...". The author is the signer, never the recipient and never the first of several names in a clinic letterhead. When `claimant_authored` is true, open as "letter from the claimant ..." and never attribute it to the patient.
- Write dates in full ("May 7, 2022"); if the date is blank or a blank-form placeholder, omit it from the opener.

FAITHFULNESS
- Use only the facts provided for that document. Do not invent or infer findings, dates, values, diagnoses, or conclusions that are not in the evidence.
- Keep all clinical content; drop only administrative identifiers (addresses, phone/fax, email, postal codes, and ID numbers).
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
- Write 3 to 5 short paragraphs in plain professional English (Grade 11 to early-undergraduate), first person ("I"), for an expert reader. Use evidence-based, linear reasoning.
- Cite specific findings and attribute them to the clinician who reported them, e.g. "MoCA 25/30 (Dr. Zaluski)", "DLCO 59% predicted (Dr. Joanis)". Treat an author as a physician when `author_is_doctor` is true, the credentials indicate a physician, or the document is a radiology/pathology/ECG/PFT/consultation/specialist report; write physicians as "Dr. LastName" (keep surname particles, e.g. "Dr. du Rand") and use the same name form for that clinician throughout. Never write a bare "Dr." or "Dr." followed by a non-name word; if no real name is available, describe the source without inventing one.
- Distinguish symptoms, restrictions, limitations, tolerance, and contraindications. Note functional limitations and their clinical basis, flag discrepancies between providers, and identify missing objective evidence where it matters.
- Do not retell the chronological summary document by document, and do not restate raw form fields, header data, fax timestamps, or administrative content. Do not assert causation or significance beyond what the providers stated.
- Plain text only: no bullets, headings, markdown, italics, bold, or emojis. Write entirely in English using the Latin alphabet; never emit a word or character from another language or script.
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
                    "subsection_id": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["subsection_id", "summary"],
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
