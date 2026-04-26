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


def surname(name: str) -> str | None:
    """Return the surname token from a printed name string."""
    if not name:
        return None
    cleaned = name.strip().rstrip(",.")
    if "," in cleaned:
        last = cleaned.split(",", 1)[0].strip()
        if last:
            return last.split()[0].rstrip(",.")
    tokens = _name_tokens(cleaned)
    if not tokens:
        return None
    return tokens[-1]


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
