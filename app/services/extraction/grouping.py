"""Document and patient grouping heuristics.

These run AFTER Gemini parsing so we can override `starts_new_document` based
on cross-page signals (patient changes, date+author changes, signature-only
continuations, etc.).
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

from app.services.extraction.header import canonical_date_iso
from app.services.extraction.models import (
    AuthorFingerprint,
    DocumentBucket,
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
    # Hyphens are a separator, not punctuation to drop in place: a hyphenated
    # surname is inconsistently OCR'd as "Haddad-Bowler", "Haddad - Bowler",
    # or "Haddad Bowler" across different pages/documents of the same file.
    # Splitting on the hyphen (rather than only stripping it) makes all of
    # those forms tokenize to the same {"haddad", "bowler"} set.
    text = re.sub(r"[,.;:-]", " ", text)
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


# A handwritten practitioner signature is re-OCR'd on EVERY page of a chart
# series, and cursive yields a different reading page to page ("Usuni",
# "Usmani", "Usmani, Hamza Gul" for the same signer). Exact token equality
# treats those as different clinicians and splits one provider's chart into
# fragments. Fuzzy equivalence absorbs one-or-two-letter OCR drift while
# genuinely different names ("Wilson" vs "Watson", "Meredith" vs "Bonin")
# stay distinct.
#
# SCOPE: boundary decisions ONLY (does this page continue the same document /
# the same visit series?). Fuzzy matching NEVER chooses or rewrites a
# displayed name - every author name shown in output is verbatim what the
# parser read from that entry's own pages (client requirement).
_AUTHOR_FUZZY_THRESHOLD = 0.7


def _author_token_ratio(a: str | None, b: str | None) -> float:
    ta = _normalize_name_tokens(a).replace("|", "")
    tb = _normalize_name_tokens(b).replace("|", "")
    if not ta or not tb:
        return 0.0
    return SequenceMatcher(None, ta, tb).ratio()


def _token_pair_matches(a: str, b: str) -> bool:
    if a == b:
        return True
    ratio = SequenceMatcher(None, a, b).ratio()
    if ratio >= _AUTHOR_FUZZY_THRESHOLD:
        return True
    # Very short tokens quantize coarsely ("gul" vs "gui" is 0.67 despite a
    # single-letter OCR slip) - allow a slightly lower bar for them. "john"
    # vs "jane" (different people) is 0.5 and stays below either bar.
    return len(a) <= 4 and len(b) <= 4 and ratio >= 0.6


def authors_equivalent(a: str | None, b: str | None) -> bool:
    """True when two author names are the same person, tolerating OCR drift.

    Matching is PER TOKEN, never on the blindly joined string: every token of
    the smaller name must fuzzily match a distinct token of the larger one.
    That lets "Usuni" ~ "Usmani, Hamza Gul" (a garbled surname-only reading of
    the same signature) while keeping "John Smith" vs "Jane Smith" apart -
    their shared surname matches but "john" vs "jane" does not."""
    na, nb = _normalize_name_tokens(a), _normalize_name_tokens(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    tokens_a, tokens_b = na.split("|"), nb.split("|")
    smaller, larger = (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    remaining = list(larger)
    for token in smaller:
        match = next((cand for cand in remaining if _token_pair_matches(token, cand)), None)
        if match is None:
            return False
        remaining.remove(match)
    return True


def _authors_conflict(a: str | None, b: str | None) -> bool:
    """True only when BOTH names are present and are genuinely different
    people (not an OCR variant of one signature)."""
    return bool(a and b) and not authors_equivalent(a, b)


# A real production chart showed the SAME clinician's signature read as
# "Hauza Suif Usuar" / "Hauza Lail Usmani" / "Hauza Jail Usmani" /
# "Hauza Sail Usuni" / "Hauza Saif Usuri" across five different visit pages -
# the given/middle name garbles differently on every read even though the
# surname is recognizably one word each time. authors_equivalent() requires
# EVERY token to fuzzily pair up, so a consistently-misread middle name alone
# blocks the match even when the surname itself is a clear variant (measured
# ratios for these five real readings: 0.545-0.8, all pairwise). The surname
# is what actually identifies the clinician; the given/middle name is also
# the part a signature scrawls fastest and least legibly, so it is the LEAST
# reliable token to require agreement on.
_SURNAME_FUZZY_THRESHOLD = 0.5


def _surname_token(value: str | None) -> str:
    """Best-effort surname, in PRINTED order (never alphabetically sorted
    like _normalize_name_tokens): the part before a comma in "Last, First"
    form, otherwise the final word in "First [Middle] Last" form."""
    if not value:
        return ""
    text = _strip_accents(value).lower()
    text = re.sub(r"\bdr\.?\b", "", text)
    part = text.split(",", 1)[0] if "," in text else text.split()[-1:] and text.split()[-1]
    if not part:
        return ""
    return re.sub(r"[^a-z0-9]", "", part)


def _recurring_series_name_match(a: str | None, b: str | None) -> bool:
    """Looser than authors_equivalent(): used ONLY to decide whether the next
    page continues the SAME recurring provider chart. Falls back to a
    surname-only fuzzy comparison when the full names don't already match,
    so a garbled given/middle name never blocks recognizing the same
    clinician's ongoing chart. Safe to be more lenient here specifically
    because this function is only ever called under strong extra context
    (same patient, same clinical bucket, adjacent pages already required by
    the caller) - a coincidental similar-surname collision between two
    genuinely different specialists on adjacent pages of the same patient's
    chart is far rarer than one clinician's own signature drifting across
    visits."""
    if authors_equivalent(a, b):
        return True
    surname_a, surname_b = _surname_token(a), _surname_token(b)
    if not surname_a or not surname_b:
        return False
    return SequenceMatcher(None, surname_a, surname_b).ratio() >= _SURNAME_FUZZY_THRESHOLD


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
    last_recipient: str | None = None
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
            last_recipient = page.recipient.name
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

        # A recurring chart/correspondence series - the SAME provider writing
        # again (e.g. a clinic auto-generating one note per appointment, often
        # back to the same referring physician) - is a continuation of one
        # ongoing document, not a new one, even when the page looks like "a
        # new document" (starts_new_document, a fresh title/date each visit).
        # Splitting these into a dozen top-level Document cards fragments one
        # chart into pieces; the per-visit breakdown belongs in
        # split_subsections() instead. Only the AUTHOR must positively match;
        # recipient merely must not actively conflict (rather than requiring
        # it to positively match too) because `recipient` is legitimately
        # blank on continuation pages and on any page sourced from
        # `extra_documents` (a second visit note stacked on the same physical
        # page), so requiring a positive recipient match on both sides
        # silently defeated this check for exactly the recurring-visit
        # pattern it exists to catch.
        recipient = page.recipient.name
        same_author = authors_equivalent(author, last_author)
        recipient_conflicts = _authors_conflict(recipient, last_recipient)
        same_provider_series = same_author and not recipient_conflicts

        force_new = False
        merge = False

        # Two ParsedPage entries with the same page_number are always split
        # (they were produced by expanding extra_documents from one physical page).
        last_page_number = current[-1].page_number if current else None
        if last_page_number is not None and page.page_number == last_page_number:
            force_new = True
        elif patient_key and last_patient and patient_key != last_patient:
            force_new = True
        elif same_provider_series:
            pass
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
            last_recipient = recipient
            last_patient = patient_key
        else:
            current.append(page)
            if title:
                last_title = title
            if date:
                last_date = date
            if author:
                last_author = author
            if recipient:
                last_recipient = recipient
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


def _refresh_segment_metadata(seg: DocumentSegment) -> None:
    """Re-derive title/date/bucket/author/recipient from `seg`'s current full
    page list. MUST run after any `seg.pages.extend(...)` merge - otherwise a
    merged segment keeps the handful of fields its small anchor sub-segment
    originally had even though its page list has grown, so a later matching
    pass (e.g. the recurring-provider-series heal below) compares against
    stale, incomplete metadata instead of what the merged document actually
    contains."""
    pages = seg.pages
    title = next((p.document.title for p in pages if p.document.title), None)
    if title:
        seg.title = title
    date = next((p.document.date for p in pages if p.document.date), None)
    if date:
        seg.date = date
    bucket = next(
        (p.document.bucket for p in pages if p.document.bucket and p.document.bucket != "unknown"), None
    )
    if bucket:
        seg.bucket = bucket
    author_page = next((p for p in pages if p.author.name), None)
    if author_page:
        seg.author = author_page.author
    recipient_page = next((p for p in pages if p.recipient.name), None)
    if recipient_page:
        seg.recipient = recipient_page.recipient


# How many pages of intervening admin/skipped content (fax cover sheets,
# invoices, a stray excluded page) can separate two segments and still count
# as "the same ongoing chart, briefly interrupted" rather than two unrelated
# documents. Wide enough to bridge a couple of admin pages, tight enough that
# a genuinely different, much-later section of a large merged PDF is not
# swept in just because the same clinician's name recurs somewhere in it.
_RECURRING_SERIES_MAX_GAP = 5

# A tighter gap for the weaker "headerless" signal below (neither author nor
# recipient to compare at all) - only bridges genuinely adjacent pages, not a
# same-patient section dozens of pages away that merely lacks headers too.
_HEADERLESS_MAX_GAP = 2


def _bucket_compatible(a: DocumentBucket, b: DocumentBucket) -> bool:
    return a == b or a == "unknown" or b == "unknown"


def _titles_similar(a: str | None, b: str | None) -> bool:
    """True when two document titles are the same repeating form/letterhead,
    tolerating partial captures - a chart's per-visit template is titled
    "TANDEM Health & Diagnostics" on one page and "TANDEM Health &
    Diagnostics clinical note" (or a lightly garbled variant) on the next."""
    ka, kb = _normalize_key(a), _normalize_key(b)
    if not ka or not kb:
        return False
    if ka == kb or ka in kb or kb in ka:
        return True
    return SequenceMatcher(None, ka, kb).ratio() >= 0.75


def _is_headerless(seg: DocumentSegment) -> bool:
    """A segment with no title, author, or recipient of its own - e.g. a
    repeating clinic chart-note template ("Scale from 0 to 10 ... Today I
    have pain in my ...") whose signature line is inconsistently captured
    across visits. Carries no signal to positively confirm OR conflict with
    a neighboring segment."""
    return not seg.title and not seg.author.name and not seg.recipient.name


def _is_recurring_provider_continuation(prev: DocumentSegment, seg: DocumentSegment) -> bool:
    """True when `seg` is another visit in the SAME ongoing provider chart as
    `prev` (e.g. a nerve-block treatment series), not a genuinely new document.

    group_documents() already applies this same-provider-series signal WITHIN
    its single page-by-page pass, but two structural things force a fresh
    top-level segment regardless of that signal: hitting an admin/fax page
    (which resets the pass's running author/recipient state) and a same-page
    extra_documents boundary (two documents sharing one physical page are
    always split). Both are correct at that instant, but their after-effect -
    a real multi-visit chart chopped into many top-level Document cards - is
    exactly what this healing pass reverses, using each segment's already
    settled author/recipient/bucket/page-range rather than the transient
    per-page state.
    """
    if not _same_patient(prev.patient_key, seg.patient_key):
        return False
    if not _bucket_compatible(prev.bucket, seg.bucket):
        return False

    # Looser than authors_equivalent(): falls back to surname-only fuzzy
    # matching, since a repeating chart's handwritten signature garbles its
    # given/middle name differently on every single visit while the surname
    # itself stays a recognizable variant (see _recurring_series_name_match).
    prev_author, seg_author = prev.author.name, seg.author.name
    author_matches = _recurring_series_name_match(prev_author, seg_author)
    author_conflicts = bool(prev_author and seg_author) and not author_matches

    prev_recipient, seg_recipient = prev.recipient.name, seg.recipient.name
    recipient_matches = _recurring_series_name_match(prev_recipient, seg_recipient)
    recipient_conflicts = bool(prev_recipient and seg_recipient) and not recipient_matches

    if author_conflicts or recipient_conflicts:
        return False

    gap = seg.page_start - prev.page_end
    # A positive match on at least one of author/recipient - a page whose
    # author was never captured (common on a continuation page with no
    # letterhead of its own, e.g. "Dear Dr. Bonin..." with a blank signature
    # block) can still be recognized as the same ongoing chart by its
    # recipient alone, and vice versa.
    if author_matches or recipient_matches:
        return 0 <= gap <= _RECURRING_SERIES_MAX_GAP
    # Neither side has anything to compare (a repeating chart-note template
    # whose signature line is inconsistently captured, e.g. a chiropractic
    # clinic's daily visit form) - nothing conflicts, but the signal is much
    # weaker, so only bridge genuinely adjacent pages.
    if _is_headerless(prev) or _is_headerless(seg):
        return 0 <= gap <= _HEADERLESS_MAX_GAP
    # Same repeating form/letterhead title on adjacent pages of one clinical
    # chart: entries whose handwritten "Practitioner:" line was not captured
    # on their own page (blank, illegible, or spilling to the next page).
    # Reaching this point means NOTHING conflicts (a genuine author or
    # recipient mismatch already returned False above) - at least one side
    # simply has no signature to compare. The recurring form title plus same
    # patient on adjacent pages identifies the series, so merge and let
    # authorless entries inherit whatever name WAS parsed within the
    # document, instead of each page becoming its own card with no author.
    # Restricted to clinical-bucket segments: imaging/pathology reports carry
    # real signatures, and generic same-title merging there could chain two
    # different radiologists' reports if both signatures were missed.
    if (
        prev.bucket == "clinical"
        and seg.bucket == "clinical"
        and _titles_similar(prev.title, seg.title)
    ):
        return 0 <= gap <= _HEADERLESS_MAX_GAP
    return False


def _merge_recurring_provider_series(segments: list[DocumentSegment]) -> list[DocumentSegment]:
    """Re-heal a single ongoing provider chart that group_documents() force-split
    into several top-level Documents (see `_is_recurring_provider_continuation`).
    The individual dated visits are not lost - split_subsections() still
    surfaces each one as its own dated sub-entry under the merged Document."""
    if len(segments) <= 1:
        return segments
    merged: list[DocumentSegment] = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        if _is_recurring_provider_continuation(prev, seg):
            prev.pages.extend(seg.pages)
            prev.include_in_output = prev.include_in_output or seg.include_in_output
            _refresh_segment_metadata(prev)
            continue
        merged.append(seg)
    return merged


def _coalesce_segments(segments: list[DocumentSegment]) -> list[DocumentSegment]:
    """Heal documents that messy scans split apart, and drop exact duplicates.

    Failure modes endemic to 500-page faxed bundles:
      1. A blank/photo/date-only page splits one report into a head fragment and
         an orphan continuation that carries no header of its own.
      2. The same report is faxed or scanned twice back-to-back, yielding two
         identical adjacent segments that look like two separate documents.
      3. The SAME letter is re-included NON-adjacently, elsewhere in the same
         merged file - e.g. a law firm merges several records-request
         responses into one PDF, and one clinic's response re-encloses a
         letter that another response already included earlier. These two
         copies can be dozens of pages apart with unrelated content between
         them, so an adjacent-only check never sees them together.
      4. A single ongoing provider chart (e.g. a nerve-block treatment series)
         force-splits into many top-level Documents because of intervening
         admin pages or same-physical-page (extra_documents) boundaries, even
         though every visit shares the same author and an ongoing recipient.
    (1), (2), and (4) are healed against the immediately preceding segment.
    (3) requires scanning every earlier segment for the same patient, not just
    the one right before it.
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
                _refresh_segment_metadata(prev)
                continue
        merged.append(seg)

    merged = _merge_recurring_provider_series(merged)

    deduped: list[DocumentSegment] = []
    for seg in merged:
        if any(
            _same_patient(earlier.patient_key, seg.patient_key) and _is_duplicate(earlier, seg)
            for earlier in deduped
        ):
            continue
        deduped.append(seg)

    for index, seg in enumerate(deduped, start=1):
        seg.id = f"doc-{index}-{seg.pages[0].page_number}"
    return deduped


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
        # Fuzzy, not exact: the same handwritten signature OCRs differently
        # page to page; only a genuinely different clinician splits an entry.
        author_changed = _authors_conflict(author, last_author)

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

    # Deliberately NO cap on how many sub-sections one document can surface: a
    # cumulative EMR chart export legitimately holds hundreds of dated entries,
    # and any fixed cap merges distinct dated entries together - the QA-visible
    # symptom is "entries combined" and dates silently missing from the index.
    # Completeness beats compactness for a consultant-facing chronology.
    runs = _merge_small_fragments(runs)

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


# Two same-person name-token sets scored via _name_ratio(): OCR variants of one
# surname ("Bowler" vs "Bouele", "Chesnari" vs "Chesnari") land at 0.88-0.95;
# two different people who happen to share a surname ("John Smith" vs "Jane
# Smith") land at 0.67-0.78. 0.85 sits cleanly between the two.
_FUZZY_NAME_MERGE_THRESHOLD = 0.85


def _consolidate_bundles(bundles: list[PatientBundle]) -> list[PatientBundle]:
    """Two-pass merge:
    1. By canonical ISO DOB - same DOB across pages collapses regardless of name shape.
    2. By name-token overlap - merges single-token bundles ("Wanda") into a
       larger bundle whose tokens contain the same surname/given name when
       neither has a contradicting DOB. Falls back to a fuzzy ratio when the
       tokens overlap but neither set is a subset of the other - this is the
       common shape of an OCR-mangled surname (e.g. "Peter Haddad-Bowler" vs
       "Peter-Haddad Bouele" for the same patient), which a strict subset
       check never catches and which otherwise splits one patient's file into
       two separate patient sections.
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
                if not overlap:
                    continue
                if (
                    bundle_tokens.issubset(parent_tokens)
                    or parent_tokens.issubset(bundle_tokens)
                    or _name_ratio(bundle, parent) >= _FUZZY_NAME_MERGE_THRESHOLD
                ):
                    _merge_into(parent, bundle)
                    merged = True
                    break
        if not merged:
            seen.append(bundle)

    seen = _merge_enclosed_bundles(seen)
    for bundle in seen:
        _apply_majority_patient_name(bundle)
    for index, bundle in enumerate(seen, start=1):
        bundle.id = f"patient-{index}"
    return seen


def _apply_majority_patient_name(bundle: PatientBundle) -> None:
    """Display the patient's MOST COMMONLY parsed name across this bundle's
    documents, not whichever page happened to be seen first.

    A referral cover page is often the very first page of the whole bundle,
    and one bad OCR read there ("Peter Fel Haddad- Bouler") used to lock in
    permanently as the header's displayed name even though a dozen later
    clinical notes all read the same patient's name correctly - first-seen
    is not the same as most-reliable. This is a plain majority vote over
    EXACT strings this file's own parsing actually produced (never a fuzzy
    rewrite into a new spelling), so the result is always something really
    printed in the document."""
    counter = Counter(doc.patient_name for doc in bundle.documents if doc.patient_name)
    if not counter:
        return
    top_count = counter.most_common(1)[0][1]
    # A tie is broken by the longest reading (more complete beats truncated),
    # not by first-seen order.
    tied = [name for name, count in counter.items() if count == top_count]
    bundle.name = max(tied, key=len)


def _split_range_by_patient(collected: list[ParsedPage]) -> list[list[ParsedPage]]:
    """Different patients NEVER share a document - hard-split a planned range
    wherever a page names a different patient, regardless of what the
    boundary plan said."""
    runs: list[list[ParsedPage]] = []
    current: list[ParsedPage] = []
    last_key = ""
    for page in collected:
        key = _patient_key(page)
        if current and key and last_key and key != last_key:
            runs.append(current)
            current = []
        current.append(page)
        if key:
            last_key = key
    if current:
        runs.append(current)
    return runs


def group_documents_with_plan(
    pages: list[ParsedPage], ranges: list[dict]
) -> list[DocumentSegment]:
    """Assemble document segments following an AI-produced boundary plan.

    The plan carries the cross-page judgment (where each source document
    starts and ends, seen over the WHOLE file); this function stays fully
    deterministic and enforces the invariants the plan is never allowed to
    break: page numbers come from parsing (never the plan), different
    patients never share a segment, excluded admin/blank pages never join a
    segment, and any content page the plan failed to cover falls back to the
    heuristic grouping so nothing is ever silently lost."""
    if not pages:
        return []
    _propagate_patient(pages)

    by_page: dict[int, list[ParsedPage]] = {}
    for page in pages:
        by_page.setdefault(page.page_number, []).append(page)

    def _collect(start: int, end: int) -> list[ParsedPage]:
        out: list[ParsedPage] = []
        for n in range(start, end + 1):
            for pg in by_page.get(n, []):
                # Same exclusion rule as the heuristic pass: separator pages
                # carry no content and never join a document (they surface
                # via coverage placeholders instead).
                if not pg.include_in_output and pg.page_kind in {"empty", "admin"}:
                    continue
                out.append(pg)
        return out

    max_page = max(by_page)
    planned: list[tuple[int, int]] = []
    prev_end = 0
    for entry in sorted(ranges, key=lambda r: int(r.get("start_page") or 0)):
        start = max(int(entry.get("start_page") or 0), prev_end + 1, 1)
        end = min(int(entry.get("end_page") or 0), max_page)
        if end < start:
            continue
        prev_end = max(prev_end, end)
        if (entry.get("kind") or "document") != "document":
            continue
        planned.append((start, end))

    segments: list[DocumentSegment] = []
    covered: set[int] = set()
    for start, end in planned:
        covered.update(range(start, end + 1))
        collected = _collect(start, end)
        if not collected:
            continue
        for run in _split_range_by_patient(collected):
            segments.append(_segment_from_pages(run, len(segments) + 1))

    # Content pages the plan never covered (a gap, or a clipped/invalid
    # range): group them with the heuristic pass so they still surface.
    uncovered = [
        n for n in sorted(by_page) if n not in covered and any(
            pg.include_in_output or pg.page_kind not in {"empty", "admin"}
            for pg in by_page[n]
        )
    ]
    if uncovered:
        runs: list[list[int]] = [[uncovered[0]]]
        for n in uncovered[1:]:
            if n == runs[-1][-1] + 1:
                runs[-1].append(n)
            else:
                runs.append([n])
        for run in runs:
            leftover_pages = [pg for n in run for pg in by_page[n]]
            segments.extend(group_documents(leftover_pages))

    # A whole-file range can only split BETWEEN physical pages, but one source
    # page can contain the end of the previous note and the beginning of the
    # next. The parser represents those as multiple ParsedPage entries sharing
    # one page number. If the boundary model starts a range too early, its first
    # entries are headerless continuations (starts_new_document=False) followed
    # by the real new entry. Move that leading continuation material back to the
    # preceding segment before subsection dates and page anchors are derived.
    #
    # This also heals a conventional continuation page that the boundary model
    # split merely because it contains the prior note's closing author/date.
    # Without this pass, the continuation is attached to the NEXT dated visit,
    # shifting that visit and every later card one page backward.
    segments.sort(key=lambda s: (s.page_start, s.page_end))
    healed: list[DocumentSegment] = []
    for seg in segments:
        if not healed:
            healed.append(seg)
            continue
        prev = healed[-1]
        prefix_len = 0
        for page in seg.pages:
            if page.starts_new_document:
                break
            prefix_len += 1
        can_reattach = (
            prefix_len > 0
            and _same_patient(prev.patient_key, seg.patient_key)
            and _bucket_compatible(prev.bucket, seg.bucket)
            and 0 <= seg.pages[0].page_number - prev.page_end <= 1
        )
        if not can_reattach:
            healed.append(seg)
            continue
        prev.pages.extend(seg.pages[:prefix_len])
        prev.include_in_output = prev.include_in_output or seg.include_in_output
        _refresh_segment_metadata(prev)
        seg.pages = seg.pages[prefix_len:]
        if seg.pages:
            _refresh_segment_metadata(seg)
            healed.append(seg)

    # Run the SAME healing pass group_documents() uses (recurring-provider-
    # series merge, duplicate drop, renumber) over the plan's own output, not
    # just over the fallback leftovers. The whole-file boundary plan judges
    # genuinely distinct documents well, but nothing stops it from still
    # splitting one ongoing visit chart into a range per visit; this safety
    # net re-merges those exactly as it would from the heuristic path, so
    # the golden rule (one recurring chart = one document with dated
    # sub-entries) holds no matter which path produced the raw segments.
    healed.sort(key=lambda s: (s.page_start, s.page_end))
    return _coalesce_segments(healed)


def build_coverage_placeholders(
    pages: list[ParsedPage], bundles: list[PatientBundle]
) -> list[DocumentSegment]:
    """Synthesize placeholder segments for every physical page that no included
    document claimed.

    Pages get dropped for legitimate reasons - admin/fax/billing pages, blank
    pages, and (rarely) a page the parser failed on. But a consultant-facing
    index must account for EVERY source page: an unexplained hole in the page
    sequence is indistinguishable from silently lost clinical content. Each
    contiguous uncovered run becomes one placeholder segment that renders as a
    deterministic one-line card (never summarized by the LLM)."""
    covered: set[int] = set()
    for bundle in bundles:
        for doc in bundle.documents:
            if not doc.include_in_output:
                continue
            for page in doc.pages:
                covered.add(page.page_number)

    by_number: dict[int, list[ParsedPage]] = {}
    for page in pages:
        by_number.setdefault(page.page_number, []).append(page)

    uncovered = [n for n in sorted(by_number) if n not in covered]
    if not uncovered:
        return []

    runs: list[list[int]] = []
    current: list[int] = [uncovered[0]]
    for n in uncovered[1:]:
        if n == current[-1] + 1:
            current.append(n)
        else:
            runs.append(current)
            current = [n]
    runs.append(current)

    placeholders: list[DocumentSegment] = []
    for index, run in enumerate(runs, start=1):
        run_pages = [pg for n in run for pg in by_number[n]]
        placeholders.append(
            DocumentSegment(
                id=f"placeholder-{index}-{run[0]}",
                pages=run_pages,
                bucket="administrative",
                title=next((p.document.title for p in run_pages if p.document.title), None),
                date=next((p.document.date for p in run_pages if p.document.date), None),
                include_in_output=True,
                is_placeholder=True,
            )
        )
    return placeholders


def attach_placeholders(
    bundles: list[PatientBundle], placeholders: list[DocumentSegment]
) -> None:
    """Insert placeholder segments into the patient bundle whose page range
    encloses them (or the nearest bundle), keeping documents page-ordered so
    each placeholder appears in its correct chronological position."""
    if not bundles or not placeholders:
        return
    for seg in placeholders:
        target: PatientBundle | None = None
        for bundle in bundles:
            if (
                bundle.page_start
                and bundle.page_end
                and bundle.page_start <= seg.page_start <= bundle.page_end
            ):
                target = bundle
                break
        if target is None:
            target = min(
                bundles,
                key=lambda b: min(
                    abs((b.page_start or 0) - seg.page_start),
                    abs((b.page_end or 0) - seg.page_start),
                ),
            )
        target.documents.append(seg)
        target.documents.sort(key=lambda d: d.page_start)


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
