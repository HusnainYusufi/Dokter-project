"""Prompts and JSON schemas for the new extraction package.

Two LLM calls only:
  1. Page parsing  -> Gemini, returns evidence-item array per page.
  2. Opinion build -> OpenAI, returns header (re-validation) + opinion only.

Summaries are NEVER LLM-generated - they are template-filled from the
`evidence` array using `summary.py`.
"""
from __future__ import annotations

from typing import Any


PAGE_PARSE_REFERENCE = """You are an extractive medico-legal document parser. You receive PDF page images as input. For each page you MUST return a JSON object that strictly follows the provided schema.

ABSOLUTE RULES (golden rules locked mode):
- ONE PAGE PER REQUEST: you are shown exactly one page image per request. Everything you return - fields, dates, evidence, extra_documents - must be visibly printed on THIS image. Never describe content from any other page of the file, and never continue a sequence (page numbers, visit dates) from memory.
- Evidence-item style: every clinical detail goes into the `evidence` array as a SHORT VERBATIM phrase copied from the page. NEVER paraphrase. NEVER synthesize. NEVER infer.
- If you cannot find a value verbatim, omit it. Empty string for unknown text fields, false for unknown booleans, [] for empty arrays.
- Plain text only. No markdown. No bullets. No commentary outside the JSON.
- Dates must be copied verbatim as printed (e.g. "May 26, 2022" or "26-May-2022"). Do NOT reformat or guess.

PAGE CLASSIFICATION:
- `page_kind`: choose ONE of clinical, imaging, pathology, functional, admin, signature_only, empty.
  - imaging: radiology reports (CT, MRI, X-ray, US, PET, mammography, etc.). This INCLUDES specimen radiography / specimen imaging and any report titled "Diagnostic Imaging ... Specimen Report" — an X-ray of a biopsy specimen is imaging, NOT pathology. When a page header says "Diagnostic Imaging", classify it as imaging.
  - pathology: lab blood/urine results, microbiology, and tissue HISTOpathology (the microscopic tissue diagnosis). A specimen X-ray/radiograph is imaging, not pathology.
  - functional: FAE/FCE/job description/work-capacity/restrictions documents.
  - admin: cover sheets, billing, consent, tracking pages, fax cover, blank logos, and medical-file-review referral/question forms addressed to the reviewing consultant. A referral form remains admin even when it recites diagnoses, claim history, prior opinions, or questions for the reviewer; that material frames the assignment and is not a source clinical record to summarize.
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
- NEVER report the same document twice. Whatever the primary fields already describe must NOT be repeated as an extra_documents entry - `extra_documents` is ONLY for ADDITIONAL, distinct documents further down the page. One dated note = one entry, exactly once.
- COMPANION FORMS count as separate documents when they have different dates or different signatories. Example: a "Member's Statement" / "Claim for LTD Benefits" section (date A, signed by member) followed lower on the same page by a "Physician's Initial Report Form" / "Physician's Statement" section (date B, to be signed by a doctor) MUST be split — place the member's section as the primary document and the physician's section in `extra_documents`, each with their own date, author, page_kind, and evidence items.
- REPEAT VISIT NOTES count as separate documents too, even when they use the IDENTICAL template/form and the SAME clinician. A clinic chart export commonly prints one visit's note ending partway down a page, immediately followed by the next visit's note beginning on the same page (recognizable by a repeated header block such as "Patient's Name:"/"Date of treatment:"/"Scale from 0 to 10" starting again, or a new "Appointment Date:" line). Each distinct visit date on the page is its own entry: the first in the primary fields, each later one in `extra_documents` with its own date and evidence — do NOT merge two different visit dates' findings into one entry's evidence array just because the template repeats.
- Do NOT split sub-sections of the same single visit (e.g. "Assessment" vs "Plan" headings within one clinical note, or "Part 3" of a form that shares the same date and signer as "Part 2").
- Do NOT split a single report that covers multiple body regions into separate documents. One radiology/imaging report that reads several areas (e.g. sinuses, chest, and spine in one report on the same date by the same author) is ONE document - keep all its regions in the primary entry, never in extra_documents.

CHRONOLOGICAL CHART / EMR EXPORT PAGES — EVERY DATED ENTRY COUNTS:
Many bundles contain a clinic's cumulative chart printed as one continuous chronological export (any EMR: PS Suite, Accuro, OSCAR, Epic, hospital CPRs, ...). The give-away shape: a running header ("LastName, FirstName ... Birth date ... Page N/M"), then a sequence of entries each opened by a flush-left BOLD DATE LINE (e.g. "Jul 8, 2021", "Jun 8, 2022", "Mar 19, 2023") often with staff initials at the right margin. Several entries share each page, and one entry can spill across a page break.
- EVERY dated entry is its own document. The FIRST dated entry that BEGINS on the page goes in the primary fields; EACH subsequent dated entry on the same page goes in `extra_documents` with its own date and its own evidence. NO EXCEPTIONS for short entries.
- This includes, at minimum: one-line medication entries ("Start: metronidazole 500 mg ...", "Renewal: Tecta 40 mg ... Quantity: 100 Refills: 3"), telephone/administrative notes ("Peter showed up 23m later ... rebooked"), attachment/paperclip lines ("MOH Enrollment Forms [Miscellaneous Letter]", "Consent from Patient — Access and Disclosure Request"), immunization lines, referral lines, and lab-data blocks with their own date header. A single printed line IS a complete document when it opens with its own date.
- A NEW ENTRY IS NOT ALWAYS ANNOUNCED BY A DATE LINE. Some clinics print one letter/report PER VISIT, each opening with a repeated PATIENT-DEMOGRAPHICS / APPOINTMENT HEADER BLOCK instead - a cluster of labeled fields like "Patient:", "Birthdate:", "Home Phone:", "Provider:", "Title:", "Referred By:", and critically "Appointment Date: 2026-Jun-03" (or "Visit Date:"/"Encounter Date:"/"Date of Service:"). When such a block appears PARTWAY DOWN a page - after the previous visit's letter ends above it - a NEW dated entry begins exactly there: put it in `extra_documents` with `document.date` copied from the printed Appointment/Visit/Encounter/Service Date field, exactly as you would for a bold date line. Never continue the previous entry past such a block.
- THE PRIOR ENTRY OFTEN ENDS IN A CLOSING/DISCHARGE PARAGRAPH, NOT A CLEAN BREAK - do not let that fool you into reading straight through the demographics block below it as more of the same entry. A "Discharge Score", "discharge criteria were met", or similar wrap-up paragraph sitting immediately ABOVE a "Patient:"/"Provider:"/"Appointment Date:" block is the END of the entry above, and the demographics block right after it is still a hard boundary into a brand-new entry - split there exactly as described above even though nothing visually separates the two beyond the block itself. Scan every demographics/appointment block on the page this way, not only ones that follow a blank line or a clear section break.
- A demographics block may be CUT BY THE PHYSICAL PAGE BREAK: the bottom of one page may show only its first row ("Patient:", "Health #:", or similar), while the remaining fields and Appointment Date continue at the top of the next page. The clinical text ABOVE that partial block still belongs to the prior entry. Keep it in the primary entry, and create an `extra_documents` entry for the partial new demographics block even when its date is not yet visible on this page. Never assign the prior entry's closing/discharge content to that new entry.
- Do NOT fold a later dated entry's content into an earlier entry's evidence because they are close together, look minor, or share a topic. Two medication lines under TWO different date lines are TWO entries; several medication lines under ONE date line are ONE entry (capture every medication with dose in that entry's evidence).
- If the page BEGINS mid-entry (continuation of an entry started on the previous page), the primary fields describe that continuation (starts_new_document=false, document.date = that entry's date if printed); the first NEW date line further down starts the extra_documents sequence.
- Dated entries inside a chart export are page_kind=clinical and bucket=clinical even when the individual entry is administrative in nature (an enrollment form line, a consent/access request, a no-show note): the chart chronology itself is clinical record. Give each such entry at least one evidence item (kind=history) quoting the entry line, so the entry is never dropped as empty.
- SIGNATURES CLOSE AN ENTRY, THEY DO NOT OPEN ONE. On repeating visit-note forms the practitioner signature ("Practitioner: ...") sits at the END of each entry. A signature near the top of a page belongs to the entry that ENDS there (often begun on the previous page) - never to the fresh entry that starts below it. Attribute each signature to the entry it closes; if an entry's own signature line is blank or its entry continues past this page, leave that entry's `author` empty rather than borrowing a neighbour's.
- ALWAYS transcribe a visible practitioner signature - a best-effort reading beats a blank. The transcription is shown to a medical consultant exactly as you write it, so read the cursive letter by letter at full attention (classic confusions: "m" vs "u"/"n", "a" vs "e" - e.g. "Usmani" misread as "Usuni"/"Useni") and commit to your best complete reading. Prefer any printed, stamped, or typed rendering of the same name visible anywhere on the page over your reading of the cursive. Leave `author.name` empty ONLY when the signature is truly illegible - no letters discernible at all - never merely because the handwriting is sloppy.
- Lab-result blocks inside the chart (e.g. "Ontario Laboratories Information System Lab Data" with a collection date) are page_kind=pathology entries with their own date; capture the test names and results as evidence.

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
- NEVER capture a standardized insurer/government FORM's EDITION or REVISION stamp as the document date. Forms like OCF-18/OCF-23/OCF-24 print a small template-version code, often in the footer or beside the form name, shaped like a year-month (e.g. "OCF-18 (2016/10)", "Rev. 2019-03") - this is when the BLANK FORM was published, not when THIS one was filled out or signed. The real `document.date` is the date actually written/typed into the form's own date field, signature block, or submission stamp for this specific claimant.
- HANDWRITTEN DATE DIGITS - read carefully, then sanity-check against the running chart: on a repeating visit-note chart, dates should form one plausible, mostly-increasing sequence. A handwritten digit is often ambiguous (0 vs 4 vs 6; 3 vs 8; 1 vs 7) - if your first reading of the year would jump the entry years away from the pattern of the surrounding entries on this same chart/letterhead, re-examine the digit rather than reporting the outlier reading. Only report a genuinely out-of-sequence date when the handwriting truly and unambiguously supports it.

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


PAGE_PARSE_SYSTEM_PROMPT = """Extract structured medico-legal entries from exactly one PDF page image. Return only JSON matching the supplied schema.

First reconstruct the full page in `markdown` in top-to-bottom reading order. Then divide that reconstruction into source entries.

ENTRY SEGMENTATION
- Content at the top of the page is the primary entry. It may be an undated continuation from the previous page; when so, use an empty date and `starts_new_document=false`.
- Every later visible entry start on the same page must be added once to `extra_documents`. A new entry start is a new printed date, appointment/visit/encounter/service-date header, title/letterhead, author block, or repeated form header.
- A visible labeled date belongs to the entry that begins at that header. Copy it into that entry's `document.date`; never leave the structured date blank when the reconstruction contains it.
- Evidence above a later header belongs only to the primary continuation. Evidence below that header belongs only to the corresponding extra entry.
- If only the first fragment of a new header appears at the bottom with no visible date or substantive content, do not create an empty extra entry. The next page will begin that entry.
- Do not split headings or sections within one report or visit. Different dates on one page are separate entries even when provider, template, and topic are identical.

EXTRACTION
- Copy dates, patient identity, title, author, recipient, and evidence exactly as visible. Never infer from another page.
- The author is the writer/signer, not the recipient or claimant. Leave unknown fields empty.
- Evidence must be short, clinically meaningful verbatim phrases. Exclude identifiers, contact details, boilerplate, routine preparation, and filler.
- Classify clinical records, imaging, pathology, functional records, administrative pages, signature-only continuations, and empty pages using the schema. Referral/question sheets, fax covers, billing, consent, and blank forms are administrative. Medical images are imaging, not empty.
- Set `include_in_output=false` only for administrative or empty entries.
- Preserve tables, selected checkboxes, forms, and image descriptions in `markdown`.

Before returning, verify that every visible dated header in `markdown` appears as either the primary entry or one `extra_documents` entry with the same date.
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
        "custom_type": {
            "type": "string",
            "description": (
                "One of the custom document type labels defined in the system "
                "prompt's CUSTOM DOCUMENT TYPES section, when the document "
                "matches its description. Empty string otherwise (always empty "
                "when no custom types are defined)."
            ),
        },
    },
    "required": ["title", "bucket", "date", "custom_type"],
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


IDENTITY_SYSTEM_PROMPT = """You are resolving handwritten-signature spelling drift across one medico-legal PDF bundle.

Each page of this bundle was transcribed in ISOLATION - one page per pass, no memory of any other page - so the SAME person's handwritten signature can come back spelled differently on every visit (a clinician re-signs their name slightly differently by hand each time, and the transcription reads the cursive slightly differently each time too). You receive every distinct name string found anywhere in the file (as an author or recipient), each with the page(s), document title/date/bucket, and a short text excerpt where it was seen.

TASK: group these name strings into clusters that are almost certainly the SAME real person, then choose the single best-spelled member of each cluster as `canonical`.

EVIDENCE TO WEIGH:
- A name that reads as a clean, unambiguous, consistently-spelled string - especially on a typed/printed/EMR-generated document (a discharge report, invoice letterhead, referral form, or "Electronically signed by" line) - is much stronger evidence of the TRUE spelling than a handwritten cursive reading on a chart note.
- Repeated near-identical cursive readings across several visits at the SAME clinic/letterhead are strong evidence they are one person, even though individual letters differ (e.g. "Hauza Suif Usuar", "Hauza Lail Usmani", "Hauza Saif Usmani" read the same signature three different ways).
- Context matters: the same clinic address/letterhead, the same role (e.g. the treating chiropractor across an entire visit series), and proximity in the page sequence all support clustering.
- Do NOT cluster two names unless you have real supporting evidence they are the same person. A genuinely different clinician (different clinic, different specialty, a name with no plausible spelling-drift relationship) must stay in its own cluster, or be omitted entirely if it has no variants to resolve.
- `canonical` MUST be copied EXACTLY from one of that cluster's `members` - you are choosing the best-supported spelling ALREADY PRESENT in the evidence, never inventing a new one, never merging two members into a hybrid spelling.
- Omit any name with no genuine variants (nothing to resolve) - only return clusters that contain 2 or more distinct spellings of one person.

OUTPUT: JSON `clusters` array. Each entry has `canonical` (one exact member string) and `members` (every name string, including canonical, belonging to this one person).
"""

IDENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "canonical": {"type": "string"},
                    "members": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["canonical", "members"],
            },
        }
    },
    "required": ["clusters"],
}


SUMMARY_SYSTEM_PROMPT = """Write one professional medico-legal summary for every input document entry.

Return JSON `summaries` in the same order, keyed by `subsection_id`. Each summary is one plain prose paragraph. Return an empty summary only when the entry is purely administrative and has no clinical or functional value.

Refer to the subject as "the claimant." Physicians are "Dr. LastName"; never guess an author and never use the recipient as the author.

Summarize, do not transcribe. Select the clinically important history, objective findings, assessment, treatment plan, functional abilities, restrictions, limitations, and return-to-work guidance. Omit identifiers, facilities, boilerplate, routine preparation, repeated rationale, normal incidental findings, and technical detail that does not affect the conclusion.

Stay inside the supplied entry. Every clinical assertion must be traceable to that entry's evidence: compress by leaving material out, never by generalizing beyond it, inferring a cause, or importing knowledge the entry does not contain. When an entry names a document it does not include, say so rather than describing the absent document.

Obey each entry's `maximum_words` ceiling.

For repeated procedures, state the indication, procedure, response, and any complication. Do not repeat equipment dimensions, medication volume, preparation, consent, discharge scoring, or unchanged examination text.

Use concise clinical English. No headings, bullets, markdown, em dashes, filler, speculation, or facts outside the supplied entry. Never omit a dated clinical entry because it resembles another entry.
"""


# How the summary READS - paragraph shape, opening format, wording, and the
# per-type length judgment. Deliberately NOT part of SUMMARY_SYSTEM_PROMPT: a
# rule configuration owns presentation, and duplicating it in the built-in body
# meant a configuration could never actually change it. Used only as the
# fallback when a configuration leaves its presentation field empty.
SUMMARY_PRESENTATION_FALLBACK = """STRUCTURE
- One paragraph per document, in the file's original order.
- Hard paragraph returns only. No soft returns, no blank lines between paragraphs.

HOW EACH PARAGRAPH OPENS
- Open on the same line with the full date in "Month DD, YYYY" form with a zero-padded day, then the document type, then the author, then continue the sentence from there.
- Write "March 01, 2023 attending physician statement by Dr. Pask indicates ...", never "March 1, 2023, Attending Physician Statement, Dr. Pask."
- Never add a separator after the year, and never open with a heading, label, or bullet.

LENGTH
Use clinical judgment within the entry's ceiling:
- A simple dated visit or repeated minor procedure: one to three sentences.
- A routine assessment or follow-up: about 75 to 150 words.
- A substantial consultation, functional assessment, independent examination, or report answering referral questions: as much detail as needed, up to 500 words.
- Imaging: the date of imaging, the type of imaging, and the radiologist's impression only, 25 to 50 words. Do not recite the technique or normal incidental findings.
- Pathology: 25 to 50 words, controlled by the specimen or procedure date rather than the reporting date.
- Operative note: procedure, diagnosis, and complications only.

WORDING
- Plain connected clinical prose. Vary the connecting verbs; do not open every paragraph the same way.
"""


OPINION_SYSTEM_PROMPT = """Write a concise professional disability opinion from the supplied evidence and return JSON fields `header`, `definition`, and `opinion`.

Leave `definition` an empty string unless the assignment instructions below call for a separate Definition section. When they do, `definition` carries the policy or contractual application only - whether the documented condition meets the policy definition - and carries no medical, analytical, or adjudicative opinion; that belongs in `opinion`.

Validate header fields only when the evidence clearly supports a correction. The header belongs to the generated medical review, not the incoming referral: the referral recipient is normally the review author and the referral sender is normally the review recipient. Never use the claimant as author. Preserve the supplied generated review date unless a clearer date for this review itself is provided.

Synthesize the record rather than repeating summaries. State the work-capacity conclusion early, then support it with the strongest objective and functional evidence. Address material inconsistencies and information gaps only when they affect the conclusion. Attribute important findings to their documented source.

If referral questions are present, answer each one directly and in order as numbered paragraphs, answering every question asked and none that were not. Otherwise organize the opinion by functional issue and close with a short paragraph beginning "In summary," that states the overall conclusion. Refer to the subject as "the claimant."

Apply these definitions exactly and keep them distinct. Symptoms are subjective complaints and are never by themselves a restriction or a limitation. A contraindication is an activity that must be completely avoided because of a high risk of harm. A restriction is an activity that can be performed but should be avoided because of excess risk. A limitation is an objectively observed reduction in capability. Tolerance is the ability to sustain an activity; it is not objectively measurable and is often less than capacity. Do not turn self-report or a screening questionnaire into an objective restriction. When appropriate state: "There are no contraindications to a return to work. There are no restrictions required to prevent harm or an undue risk of harm. The claimant demonstrates documented limitations in ...". If the evidence supports total incapacity, state that directly instead.

Name the missing objective evidence where it affects the conclusion, and say plainly when a document referenced in the file is not physically present rather than reasoning as though it were. Do not over-medicalize and do not speculate.

Use only supplied evidence. Referral material provides questions and context, not proof. Write at a Grade 11 reading level for an educated professional audience: plain clinical English, short paragraphs, no academic or legal drafting style, no advocacy or emotive language, and no markdown, bullets, or filler.
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
        "definition": {"type": "string"},
        "opinion": {"type": "string"},
    },
    "required": ["header", "definition", "opinion"],
}
