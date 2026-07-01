"""Document and patient grouping heuristics.

These run AFTER Gemini parsing so we can override `starts_new_document` based
on cross-page signals (patient changes, date+author changes, signature-only
continuations, etc.).
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from app.services.extraction.header import canonical_date_iso
from app.services.extraction.models import (
    AuthorFingerprint,
    DocumentSegment,
    DocumentSubsection,
    ParsedPage,
    PatientBundle,
)


def _strip_accents(value: str) -> str:
    decoded = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decoded if not unicodedata.combining(c))


def _normalize_key(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]", "", _strip_accents(value).lower())


def _normalize_name_tokens(value: str | None) -> str:
    """Sorted, accent-free, lowercase name tokens, dropping single-letter
    middle initials and salutations. Makes 'Russell Wanda C',
    'Wanda Russell', 'RUSSELL, WANDA' all collapse to the same key."""
    if not value:
        return ""
    text = _strip_accents(value).lower()
    text = re.sub(r"[,.;:]", " ", text)
    tokens = [t for t in re.split(r"\s+", text) if t]
    SKIPS = {"dr", "mr", "mrs", "ms", "miss", "the", "patient", "claimant"}
    cleaned: list[str] = []
    for token in tokens:
        token = token.strip("'\"")
        if not token or token in SKIPS:
            continue
        if len(token) <= 1:
            continue
        if re.fullmatch(r"[a-z]\.?", token):
            continue
        cleaned.append(re.sub(r"[^a-z0-9]", "", token))
    cleaned = [t for t in cleaned if t]
    cleaned.sort()
    return "|".join(cleaned)


def _canonical_dob(value: str | None) -> str:
    iso = canonical_date_iso(value)
    return iso or _normalize_key(value)


def _patient_key(page: ParsedPage) -> str:
    name = _normalize_name_tokens(page.patient.name)
    dob = _canonical_dob(page.patient.dob)
    if name and dob:
        return f"{name}|{dob}"
    if dob:
        return f"|{dob}"
    if name:
        return name
    return ""


def _propagate_patient(pages: list[ParsedPage]) -> None:
    """Pages with no patient info inherit from the prior page in the same doc."""
    last: ParsedPage | None = None
    for page in pages:
        if page.patient.name or page.patient.dob:
            last = page
            continue
        if last is not None:
            page.patient.name = last.patient.name
            page.patient.dob = last.patient.dob
            page.patient.identifier = page.patient.identifier or last.patient.identifier


def _is_bare_image(page: ParsedPage) -> bool:
    """A medical image page (X-ray/CT/photograph) with no header of its own."""
    return (
        page.page_kind == "imaging"
        and not page.document.title
        and not page.author.name
        and not page.document.date
    )


def group_documents(pages: list[ParsedPage]) -> list[DocumentSegment]:
    """Apply boundary heuristics on top of Gemini's `starts_new_document` hints."""
    if not pages:
        return []
    _propagate_patient(pages)

    segments: list[list[ParsedPage]] = []
    current: list[ParsedPage] = []
    last_title: str | None = None
    last_date: str | None = None
    last_author: str | None = None
    last_patient: str = ""

    for page in pages:
        # Blank, photo-only, or date-only pages are rampant noise in messy faxed
        # bundles and carry NO boundary signal. Closing the segment here is what
        # fragments one report into several phantom "documents". Skip them
        # transparently and keep the running document context so the next real
        # content page re-attaches to the same document.
        if not page.include_in_output and page.page_kind == "empty":
            continue
        # A pure administrative page (fax cover, billing) is a genuine separator.
        if not page.include_in_output and page.page_kind == "admin":
            if current:
                segments.append(current)
                current = []
                last_title = None
                last_date = None
                last_author = None
                last_patient = ""
            continue

        patient_key = _patient_key(page)
        date = page.document.date
        author = page.author.name
        title = page.document.title
        kind = page.page_kind

        if not current:
            current = [page]
            last_title = title
            last_date = date
            last_author = author
            last_patient = patient_key
            continue

        # A page that is purely a medical image (X-ray/CT/photograph) with no
        # header of its own is its own document, not a continuation of whatever
        # text report preceded it - unless the previous page was ALSO a bare
        # image (a multi-image series stays together). A preceding imaging REPORT
        # (which has its own title/date/author) does not absorb a loose image.
        prev_is_bare_image = bool(current) and _is_bare_image(current[-1])
        is_image_only = (
            kind == "imaging" and not title and not author and not date and bool(page.evidence)
        )
        standalone_image = is_image_only and not prev_is_bare_image

        force_new = False
        merge = False

        # Two ParsedPage entries with the same page_number are always split
        # (they were produced by expanding extra_documents from one physical page).
        last_page_number = current[-1].page_number if current else None
        if last_page_number is not None and page.page_number == last_page_number:
            force_new = True
        elif patient_key and last_patient and patient_key != last_patient:
            force_new = True
        elif page.starts_new_document and title:
            force_new = True
        # A new report date together with this page's own title is a new document
        # even when the author is unchanged (e.g. two reports by the same clinic
        # on different dates). Guarded by a differing/explicitly-new title so a
        # continuation page that merely repeats a stray date does not split.
        elif (
            date
            and last_date
            and _normalize_key(date) != _normalize_key(last_date)
            and title
            and (page.starts_new_document or _normalize_key(title) != _normalize_key(last_title or ""))
        ):
            force_new = True
        elif date and last_date and _normalize_key(date) != _normalize_key(last_date) and author and last_author and _normalize_key(author) != _normalize_key(last_author):
            force_new = True
        elif standalone_image:
            force_new = True

        if standalone_image:
            merge = False
        elif is_image_only and prev_is_bare_image:
            merge = True
        elif kind == "signature_only":
            merge = True
        elif not title and not author and not date:
            merge = True
        elif (
            not date
            and not author
            and (not patient_key or not last_patient or patient_key == last_patient)
        ):
            merge = True

        if force_new and not merge:
            segments.append(current)
            current = [page]
            last_title = title
            last_date = date
            last_author = author
            last_patient = patient_key
        else:
            current.append(page)
            if title:
                last_title = title
            if date:
                last_date = date
            if author:
                last_author = author
            if patient_key:
                last_patient = patient_key

    if current:
        segments.append(current)

    document_segments = [_segment_from_pages(seg, idx) for idx, seg in enumerate(segments, start=1)]
    return _coalesce_segments(document_segments)


def _is_pure_continuation(seg: DocumentSegment) -> bool:
    """A fragment with no identifying header of its own: no title, date, or author.

    A standalone medical image (X-ray/CT/photograph) is intentionally NOT a
    continuation - it must keep its own document card even though it carries no
    title, date, or author.
    """
    if any(p.page_kind == "imaging" for p in seg.pages):
        return False
    return not seg.title and not seg.date and not (seg.author and seg.author.name)


def _evidence_signature(seg: DocumentSegment) -> str:
    parts = sorted(_normalize_key(item.text) for item in seg.all_evidence if item.text)
    return "|".join(p for p in parts if p)


def _is_duplicate(prev: DocumentSegment, seg: DocumentSegment) -> bool:
    """True when seg is a re-scan / re-fax of prev (same header, same evidence)."""
    if not seg.title or not prev.title:
        return False
    if _normalize_key(seg.title) != _normalize_key(prev.title):
        return False
    if seg.date and prev.date and _normalize_key(seg.date) != _normalize_key(prev.date):
        return False
    seg_sig = _evidence_signature(seg)
    if not seg_sig:
        return False
    prev_sig = _evidence_signature(prev)
    return seg_sig == prev_sig or seg_sig in prev_sig


def _same_patient(a: str | None, b: str | None) -> bool:
    return not a or not b or a == b


def _coalesce_segments(segments: list[DocumentSegment]) -> list[DocumentSegment]:
    """Heal documents that messy scans split apart, and drop exact duplicates.

    Two failure modes are endemic to 500-page faxed bundles:
      1. A blank/photo/date-only page splits one report into a head fragment and
         an orphan continuation that carries no header of its own.
      2. The same report is faxed or scanned twice, yielding two identical
         segments that look like two separate documents.
    Both surface as adjacent segments that are really one document. We absorb a
    pure continuation into its predecessor, and drop an exact duplicate, so the
    document count and numbering reflect real documents only.
    """
    if not segments:
        return segments
    merged: list[DocumentSegment] = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        if _same_patient(prev.patient_key, seg.patient_key):
            if _is_duplicate(prev, seg):
                # Exact re-scan: keep prev as the canonical copy, drop the clone.
                continue
            if _is_pure_continuation(seg):
                prev.pages.extend(seg.pages)
                prev.include_in_output = prev.include_in_output or seg.include_in_output
                continue
        merged.append(seg)
    for index, seg in enumerate(merged, start=1):
        seg.id = f"doc-{index}-{seg.pages[0].page_number}"
    return merged


def _segment_from_pages(pages: list[ParsedPage], index: int) -> DocumentSegment:
    title = next((p.document.title for p in pages if p.document.title), None)
    date = next((p.document.date for p in pages if p.document.date), None)
    bucket = next(
        (p.document.bucket for p in pages if p.document.bucket and p.document.bucket != "unknown"),
        "unknown",
    )
    # Fall back to the page kind when the model left the bucket unknown, so an
    # image-only page still summarises as imaging (short, one-line) rather than
    # defaulting to a full clinical write-up.
    if bucket == "unknown":
        kind_to_bucket = {
            "imaging": "imaging",
            "pathology": "pathology",
            "functional": "functional",
            "clinical": "clinical",
        }
        for p in pages:
            mapped = kind_to_bucket.get(p.page_kind)
            if mapped:
                bucket = mapped  # type: ignore[assignment]
                break
    author_page = next((p for p in pages if p.author.name), None)
    author = author_page.author if author_page else AuthorFingerprint()

    recipient_page = next((p for p in pages if p.recipient.name), None)
    recipient = recipient_page.recipient if recipient_page else AuthorFingerprint()

    patient_page = next((p for p in pages if p.patient.name or p.patient.dob), None)
    patient_name = patient_page.patient.name if patient_page else None
    patient_dob = patient_page.patient.dob if patient_page else None
    patient_key = _patient_key(patient_page) if patient_page else ""

    # The patient is NEVER the author (golden rule). A letter or statement signed
    # by the claimant (record-correction request, complaint, personal statement)
    # must not be rendered as "clinical note by <patient>". Drop a self-authored
    # name so the summary opens as a claimant letter instead.
    claimant_authored = bool(
        author.name
        and patient_name
        and _normalize_name_tokens(author.name) == _normalize_name_tokens(patient_name)
    )
    if claimant_authored:
        author = AuthorFingerprint()

    has_evidence = any(p.evidence for p in pages)
    has_substantive_kind = any(
        p.page_kind not in {"admin", "empty", "signature_only"} for p in pages
    )
    include = (
        any(p.include_in_output for p in pages)
        and has_evidence
        and has_substantive_kind
    )

    return DocumentSegment(
        id=f"doc-{index}-{pages[0].page_number}",
        pages=pages,
        bucket=bucket,
        title=title,
        date=date,
        author=author,
        recipient=recipient,
        patient_key=patient_key or None,
        patient_name=patient_name,
        patient_dob=patient_dob,
        claimant_authored=claimant_authored,
        include_in_output=include,
    )


# A messy multi-hundred-page chart binder with noisy per-page dates could
# otherwise fragment into an unreadable wall of tiny sub-cards under one
# Document N - bound how many sub-sections one document can ever surface.
MAX_SUBSECTIONS_PER_DOCUMENT = 12


def split_subsections(doc: DocumentSegment) -> list[DocumentSubsection]:
    """Split one document's pages into contiguous per-encounter sub-sections.

    `group_documents()` already resolved the DOCUMENT-level boundary; some real
    documents are legitimately large multi-page chart binders that hold several
    distinct dated entries under one such boundary (same letterhead/title). This
    subdivides WITHIN one already-identified document by date/author changes, so
    each entry can get its own short summary later instead of a single
    per-document summary silently keeping only one entry and dropping the rest.

    A document with a single date/author (the common case) returns exactly one
    subsection wrapping all its pages - simple documents are unaffected.
    """
    pages = doc.pages
    if len(pages) <= 1:
        return [_subsection_from_pages(doc.id, pages, 0)]

    runs: list[list[ParsedPage]] = []
    current: list[ParsedPage] = []
    last_date: str | None = None
    last_author: str | None = None

    for page in pages:
        # Blank/signature-only pages carry no boundary signal at this finer
        # grain either - they always attach to the running sub-section, exactly
        # like group_documents()'s treatment of the same page kinds.
        if page.page_kind in {"empty", "signature_only"}:
            current.append(page)
            continue

        date = page.document.date
        author = page.author.name

        if not current:
            current = [page]
            last_date = date
            last_author = author
            continue

        # Looser than group_documents()'s combined date+title/date+author rule:
        # there is no title signal at this level, and a false split here only
        # costs a redundant sub-line under the same Document N card, never a
        # fabricated new document.
        date_changed = bool(date and last_date and _normalize_key(date) != _normalize_key(last_date))
        author_changed = bool(
            author and last_author and _normalize_key(author) != _normalize_key(last_author)
        )

        if date_changed or author_changed:
            runs.append(current)
            current = [page]
            last_date = date
            last_author = author
        else:
            current.append(page)
            if date:
                last_date = date
            if author:
                last_author = author

    if current:
        runs.append(current)

    runs = _merge_small_fragments(runs)
    runs = _cap_subsection_runs(runs)

    return [_subsection_from_pages(doc.id, run, index) for index, run in enumerate(runs)]


def _merge_small_fragments(runs: list[list[ParsedPage]]) -> list[list[ParsedPage]]:
    """Absorb a signature-only/zero-evidence 1-page fragment into the preceding
    run rather than emitting it as its own empty standalone sub-section."""
    if len(runs) <= 1:
        return runs
    merged: list[list[ParsedPage]] = [runs[0]]
    for run in runs[1:]:
        is_trivial_fragment = len(run) == 1 and (
            run[0].page_kind == "signature_only" or not run[0].evidence
        )
        if is_trivial_fragment:
            merged[-1].extend(run)
        else:
            merged.append(run)
    return merged


def _cap_subsection_runs(runs: list[list[ParsedPage]]) -> list[list[ParsedPage]]:
    """Bound the sub-section count to MAX_SUBSECTIONS_PER_DOCUMENT by repeatedly
    merging the smallest run into whichever neighbor is smaller."""
    runs = list(runs)
    while len(runs) > MAX_SUBSECTIONS_PER_DOCUMENT:
        smallest = min(range(len(runs)), key=lambda i: len(runs[i]))
        if smallest == 0:
            neighbor = 1
        elif smallest == len(runs) - 1:
            neighbor = smallest - 1
        else:
            left, right = smallest - 1, smallest + 1
            neighbor = left if len(runs[left]) <= len(runs[right]) else right
        first, second = sorted((smallest, neighbor))
        runs[first] = runs[first] + runs[second]
        del runs[second]
    return runs


def _subsection_from_pages(doc_id: str, pages: list[ParsedPage], index: int) -> DocumentSubsection:
    date = next((p.document.date for p in pages if p.document.date), None)
    author_page = next((p for p in pages if p.author.name), None)
    author = author_page.author if author_page else AuthorFingerprint()
    bucket = next(
        (p.document.bucket for p in pages if p.document.bucket and p.document.bucket != "unknown"),
        "unknown",
    )
    return DocumentSubsection(
        id=f"{doc_id}::{index}",
        pages=pages,
        date=date,
        author=author,
        bucket=bucket,
    )


def _bundle_canonical_dob(bundle: PatientBundle) -> str | None:
    if bundle.dob:
        return canonical_date_iso(bundle.dob)
    for doc in bundle.documents:
        if doc.patient_dob:
            iso = canonical_date_iso(doc.patient_dob)
            if iso:
                return iso
    return None


def _bundle_name_tokens(bundle: PatientBundle) -> set[str]:
    tokens: set[str] = set()
    sources = [bundle.name] + [doc.patient_name for doc in bundle.documents]
    for src in sources:
        if not src:
            continue
        for tok in _normalize_name_tokens(src).split("|"):
            if tok and len(tok) > 1:
                tokens.add(tok)
    return tokens


def _merge_into(parent: PatientBundle, child: PatientBundle) -> None:
    if not parent.name and child.name:
        parent.name = child.name
    elif child.name and len(child.name.split()) > len(parent.name.split() if parent.name else []):
        parent.name = child.name
    if not parent.dob and child.dob:
        parent.dob = child.dob
    parent.documents.extend(child.documents)
    parent.documents.sort(key=lambda d: d.page_start)


def _bundle_encloses(parent: PatientBundle, child: PatientBundle) -> bool:
    """True when parent has documents both before AND after child's page span -
    i.e. child is sandwiched inside parent's contiguous page run."""
    c_start, c_end = child.page_start, child.page_end
    if not c_start or not c_end:
        return False
    before = any(d.page_end and d.page_end < c_start for d in parent.documents)
    after = any(d.page_start and d.page_start > c_end for d in parent.documents)
    return before and after


def _dob_month_day(bundle: PatientBundle) -> str | None:
    iso = _bundle_canonical_dob(bundle)
    return iso[5:] if iso and len(iso) == 10 else None


def _name_ratio(a: PatientBundle, b: PatientBundle) -> float:
    a_name = "".join(sorted(_bundle_name_tokens(a)))
    b_name = "".join(sorted(_bundle_name_tokens(b)))
    if not a_name or not b_name:
        return 0.0
    return SequenceMatcher(None, a_name, b_name).ratio()


def _likely_same_person(a: PatientBundle, b: PatientBundle) -> bool:
    if _bundle_name_tokens(a) & _bundle_name_tokens(b):
        return True
    a_dob, b_dob = _bundle_canonical_dob(a), _bundle_canonical_dob(b)
    if a_dob and b_dob and a_dob == b_dob:
        return True
    # OCR commonly mangles a single name letter or DOB digit. When two bundles
    # share a birth month+day (only the year differs) or have closely similar
    # names, treat them as the same person - the enclosure test already confirmed
    # one is wedged inside the other's contiguous page run.
    a_md, b_md = _dob_month_day(a), _dob_month_day(b)
    if a_md and b_md and a_md == b_md:
        return True
    return _name_ratio(a, b) >= 0.6


def _merge_enclosed_bundles(bundles: list[PatientBundle]) -> list[PatientBundle]:
    """Merge a bundle whose pages are sandwiched inside another bundle's run.

    Each real patient occupies a contiguous block of pages, so an isolated
    document nested inside another patient's range is almost always an OCR
    identity variant (e.g. 'Russell' misread as 'Rossetti', DOB 1966 as 1960),
    not a new patient. Guarded by a shared name token or matching DOB so a
    genuinely different enclosed patient is left alone.
    """
    result = list(bundles)
    changed = True
    while changed and len(result) > 1:
        changed = False
        for child in result:
            parent = next(
                (
                    p
                    for p in result
                    if p is not child
                    and _bundle_encloses(p, child)
                    and _likely_same_person(p, child)
                ),
                None,
            )
            if parent is not None:
                _merge_into(parent, child)
                result = [b for b in result if b is not child]
                changed = True
                break
    return result


def _consolidate_bundles(bundles: list[PatientBundle]) -> list[PatientBundle]:
    """Two-pass merge:
    1. By canonical ISO DOB - same DOB across pages collapses regardless of name shape.
    2. By name-token overlap - merges single-token bundles ("Wanda") into a
       larger bundle whose tokens contain the same surname/given name when
       neither has a contradicting DOB.
    """
    if not bundles:
        return bundles

    by_dob: dict[str, PatientBundle] = {}
    leftover: list[PatientBundle] = []
    for bundle in bundles:
        dob = _bundle_canonical_dob(bundle)
        if dob and dob in by_dob:
            _merge_into(by_dob[dob], bundle)
            continue
        if dob:
            by_dob[dob] = bundle
        leftover.append(bundle)

    seen: list[PatientBundle] = []
    for bundle in leftover:
        bundle_dob = _bundle_canonical_dob(bundle)
        bundle_tokens = _bundle_name_tokens(bundle)
        merged = False
        if bundle_tokens:
            for parent in seen:
                parent_dob = _bundle_canonical_dob(parent)
                if bundle_dob and parent_dob and bundle_dob != parent_dob:
                    continue
                parent_tokens = _bundle_name_tokens(parent)
                if not parent_tokens:
                    continue
                overlap = bundle_tokens & parent_tokens
                if overlap and (bundle_tokens.issubset(parent_tokens) or parent_tokens.issubset(bundle_tokens)):
                    _merge_into(parent, bundle)
                    merged = True
                    break
        if not merged:
            seen.append(bundle)

    seen = _merge_enclosed_bundles(seen)
    for index, bundle in enumerate(seen, start=1):
        bundle.id = f"patient-{index}"
    return seen


def group_patients(documents: list[DocumentSegment]) -> list[PatientBundle]:
    """Multi-patient bundles per Constraints.md (8-10 patients per 500-page file)."""
    bundles: dict[str, PatientBundle] = {}
    order: list[str] = []
    fallback_idx = 0

    for doc in documents:
        if not doc.include_in_output:
            continue
        key = doc.patient_key or ""
        if not key:
            fallback_idx += 1
            key = f"__unknown_{fallback_idx}__"
        if key not in bundles:
            bundles[key] = PatientBundle(
                id=f"patient-{len(bundles) + 1}",
                key=key,
                name=doc.patient_name,
                dob=doc.patient_dob,
                documents=[],
            )
            order.append(key)
        else:
            if doc.patient_name and not bundles[key].name:
                bundles[key].name = doc.patient_name
            if doc.patient_dob and not bundles[key].dob:
                bundles[key].dob = doc.patient_dob
        bundles[key].documents.append(doc)

    ordered_bundles = [bundles[k] for k in order]
    return _consolidate_bundles(ordered_bundles)
