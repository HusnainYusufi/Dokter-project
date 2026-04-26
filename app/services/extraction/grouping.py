"""Document and patient grouping heuristics.

These run AFTER Gemini parsing so we can override `starts_new_document` based
on cross-page signals (patient changes, date+author changes, signature-only
continuations, etc.).
"""
from __future__ import annotations

import re
import unicodedata

from app.services.extraction.header import canonical_date_iso
from app.services.extraction.models import (
    AuthorFingerprint,
    DocumentSegment,
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


def group_documents(pages: list[ParsedPage]) -> list[DocumentSegment]:
    """Apply boundary heuristics on top of Gemini's `starts_new_document` hints."""
    if not pages:
        return []
    _propagate_patient(pages)

    segments: list[list[ParsedPage]] = []
    current: list[ParsedPage] = []
    last_kind: str | None = None
    last_date: str | None = None
    last_author: str | None = None
    last_patient: str = ""

    for page in pages:
        if not page.include_in_output and page.page_kind in {"admin", "empty"}:
            if current:
                if page.page_kind == "signature_only":
                    current.append(page)
                else:
                    segments.append(current)
                    current = []
                    last_kind = None
                    last_date = None
                    last_author = None
                    last_patient = ""
            continue

        patient_key = _patient_key(page)
        date = page.document.date
        author = page.author.name
        kind = page.page_kind

        if not current:
            current = [page]
            last_kind = kind
            last_date = date
            last_author = author
            last_patient = patient_key
            continue

        force_new = False
        merge = False

        if patient_key and last_patient and patient_key != last_patient:
            force_new = True
        elif page.starts_new_document and page.document.title:
            force_new = True
        elif date and last_date and _normalize_key(date) != _normalize_key(last_date) and author and last_author and _normalize_key(author) != _normalize_key(last_author):
            force_new = True

        if kind == "signature_only":
            merge = True
        elif not page.document.title and not author and not date:
            merge = True

        if force_new and not merge:
            segments.append(current)
            current = [page]
            last_kind = kind
            last_date = date
            last_author = author
            last_patient = patient_key
        else:
            current.append(page)
            if kind not in {"signature_only", "empty"}:
                last_kind = kind
            if date:
                last_date = date
            if author:
                last_author = author
            if patient_key:
                last_patient = patient_key

    if current:
        segments.append(current)

    return [_segment_from_pages(seg, idx) for idx, seg in enumerate(segments, start=1)]


def _segment_from_pages(pages: list[ParsedPage], index: int) -> DocumentSegment:
    title = next((p.document.title for p in pages if p.document.title), None)
    date = next((p.document.date for p in pages if p.document.date), None)
    bucket = next(
        (p.document.bucket for p in pages if p.document.bucket and p.document.bucket != "unknown"),
        "unknown",
    )
    author_page = next((p for p in pages if p.author.name), None)
    author = author_page.author if author_page else AuthorFingerprint()

    patient_page = next((p for p in pages if p.patient.name or p.patient.dob), None)
    patient_name = patient_page.patient.name if patient_page else None
    patient_dob = patient_page.patient.dob if patient_page else None
    patient_key = _patient_key(patient_page) if patient_page else ""

    include = any(p.include_in_output for p in pages) and any(p.evidence for p in pages)

    return DocumentSegment(
        id=f"doc-{index}-{pages[0].page_number}",
        pages=pages,
        bucket=bucket,
        title=title,
        date=date,
        author=author,
        patient_key=patient_key or None,
        patient_name=patient_name,
        patient_dob=patient_dob,
        include_in_output=include,
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
