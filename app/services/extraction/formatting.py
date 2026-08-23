"""Shared formatting helpers used by header, summary, visits, and opinion.

Centralizes name/author normalization so every output applies the same
golden-rule conventions (Dr. LastName for physicians; preserve printed form
otherwise; never emit a bare "Dr." or credential string).
"""
from __future__ import annotations

import re

from app.services.extraction.models import AuthorFingerprint


_DOCTOR_CREDENTIAL_TOKENS = {
    "md",
    "do",
    "frcpc",
    "frcsc",
    "frcp",
    "frcs",
    "facp",
    "phd",
    "dds",
    "dpm",
    "mbbs",
    "mbchb",
    "md.",
}


_NON_NAME_TOKENS = {
    "dr",
    "dr.",
    "doctor",
    "mr",
    "mr.",
    "mrs",
    "mrs.",
    "ms",
    "ms.",
    "miss",
    "the",
    "patient",
    "claimant",
    "from",
    "to",
    "attn",
    "attention",
    "by",
}


def _strip_punct(token: str) -> str:
    return re.sub(r"^[^\w]+|[^\w]+$", "", token)


def _is_credential(token: str) -> bool:
    bare = _strip_punct(token).lower()
    return bare in _DOCTOR_CREDENTIAL_TOKENS


def _name_tokens(value: str) -> list[str]:
    text = re.sub(r"[,;]", " ", value)
    out: list[str] = []
    for tok in text.split():
        bare = _strip_punct(tok)
        if not bare:
            continue
        if bare.lower() in _NON_NAME_TOKENS:
            continue
        if _is_credential(tok):
            continue
        if len(bare) <= 1:
            continue
        out.append(bare)
    return out


def has_doctor_credentials(author: AuthorFingerprint) -> bool:
    if author.is_doctor:
        return True
    if author.credentials:
        for tok in re.split(r"[\s,;.]+", author.credentials):
            if tok.lower() in _DOCTOR_CREDENTIAL_TOKENS:
                return True
    return False


def reorder_lastname_first(name: str) -> str:
    """Convert 'Last, First M' -> 'First M Last' (preserves capitalization).

    Used for non-doctor names. For doctors we only need the surname so we use
    `surname()` instead.
    """
    if "," not in name:
        return name.strip()
    last, _, rest = name.partition(",")
    last = last.strip()
    rest = rest.strip()
    if not last or not rest:
        return name.strip()
    return f"{rest} {last}".strip()


# Surname particles that belong to the LAST name and must not be dropped:
# "Tom du Rand" -> "du Rand", "Jan van der Berg" -> "van der Berg". Stored lower
# case for matching; the printed casing of the token is preserved on output.
_SURNAME_PARTICLES = {
    "du", "de", "da", "di", "del", "della", "dei", "do", "dos", "das",
    "la", "le", "van", "von", "der", "den", "ter", "ten", "bin", "al", "el",
}


def _surname_from_tokens(tokens: list[str]) -> str | None:
    """Join the trailing name tokens, pulling in any leading particles.

    Walks back from the final token and keeps absorbing preceding particle
    tokens ("du", "van", "der", ...) so multi-word surnames stay intact.
    """
    if not tokens:
        return None
    parts = [tokens[-1]]
    index = len(tokens) - 2
    while index >= 0 and tokens[index].lower() in _SURNAME_PARTICLES:
        parts.insert(0, tokens[index])
        index -= 1
    return " ".join(parts)


def surname(name: str) -> str | None:
    """Return the surname (with particles) from a printed name string."""
    if not name:
        return None
    cleaned = name.strip().rstrip(",.")
    if "," in cleaned:
        # "Last, First M" — the part before the comma is the full surname.
        last = cleaned.split(",", 1)[0].strip()
        last_tokens = _name_tokens(last)
        if last_tokens:
            return _surname_from_tokens(last_tokens)
    tokens = _name_tokens(cleaned)
    if not tokens:
        return None
    return _surname_from_tokens(tokens)


_TITLE_TRAILING_NOISE_RE = re.compile(
    r"[\s,;:\-]+(?:Dr|Dr\.|Doctor|MD|RN|To|From|By|Re|Attn|Attention)\.?$",
    re.IGNORECASE,
)


def clean_title(title: str | None) -> str | None:
    """Strip salutation/credential noise that leaks into document titles.

    The parser sometimes captures the first token of a signature/letterhead line
    (e.g. "...Cardiac Catheterization ... Dr.") into ``document.title``. A bare
    trailing "Dr."/"MD"/"To" is never part of a real title and must be removed so
    summaries and visit indices do not render a dangling salutation.

    Title casing is left to the summarizer LLM (see SUMMARY_SYSTEM_PROMPT), which
    renders the document type in normal sentence case.
    """
    if not title:
        return None
    cleaned = title.strip()
    # Repeatedly peel trailing noise tokens ("... Letter to Dr." -> "... Letter to").
    for _ in range(4):
        stripped = _TITLE_TRAILING_NOISE_RE.sub("", cleaned).strip()
        if stripped == cleaned:
            break
        cleaned = stripped
    cleaned = cleaned.rstrip(" ,;:-")
    return cleaned or None


_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def spell_number(value: int) -> str:
    """Spell an integer in words, as the reference reviews write page counts
    ("Three hundred and thirty-eight pages have been provided.").

    Falls back to digits above 9999, where words stop aiding readability.
    """
    if value < 0 or value > 9999:
        return str(value)
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return _TENS[tens] if not ones else f"{_TENS[tens]}-{_ONES[ones]}"
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        head = f"{_ONES[hundreds]} hundred"
        return head if not rest else f"{head} and {spell_number(rest)}"
    thousands, rest = divmod(value, 1000)
    head = f"{spell_number(thousands)} thousand"
    if not rest:
        return head
    joiner = " and " if rest < 100 else " "
    return f"{head}{joiner}{spell_number(rest)}"


def format_author(author: AuthorFingerprint | None) -> str | None:
    """Return the public-facing author label, or None if unusable.

    Rules:
    - Drop empty / credential-only / "Dr." alone authors.
    - Doctor (`is_doctor` or MD-style credentials) -> "Dr. Surname".
    - Otherwise return the printed name with "Last, First" reordered.
    """
    if author is None or not author.name:
        return None
    raw = author.name.strip().rstrip(",.")
    if not raw:
        return None
    tokens = _name_tokens(raw)
    if not tokens:
        return None

    if has_doctor_credentials(author):
        last = surname(raw)
        if not last or len(last) < 2:
            return None
        return f"Dr. {last}"

    if "," in raw:
        return reorder_lastname_first(raw)

    cleaned = " ".join(tokens)
    return cleaned or None
