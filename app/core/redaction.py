"""Strip credentials out of anything that gets stored or displayed.

A provider rejecting a key answers with the key in the message. That message
became `job.error`, was written to the database, and rendered in the portal:

    Incorrect API key provided: sk-proj-abc123...XYZ

So a credential leaked into stored records and onto a screen, where it survives
long after the key is rotated and is visible to anyone who can see the job. Any
text that came from an exception or a provider response goes through here first.

Deliberately pattern-based rather than a list of known keys: the point is to
catch the shape of a secret, including one from a provider added later.
"""
from __future__ import annotations

import re

REDACTED = "[redacted]"

# Ordered longest-prefix first so a more specific pattern wins.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # OpenAI: sk-, sk-proj-, sk-svcacct-, and organisation ids.
    re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{16,}"),
    re.compile(r"\borg-[A-Za-z0-9]{16,}"),
    # Google / Gemini.
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}"),
    # Anthropic.
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"),
    # AWS access key ids, and long secret access keys in an obvious context.
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}"),
    re.compile(
        r"(?i)\b(?:aws_secret_access_key|secret[_-]?access[_-]?key)\s*[=:]\s*\S+"
    ),
    # LlamaCloud and similar vendor-prefixed keys.
    re.compile(r"\bllx-[A-Za-z0-9_-]{16,}"),
    # Bearer tokens and JWTs.
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # A key named in a query string or an assignment, whatever its shape.
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password)\s*[=:]\s*\S+"),
    # Credentials embedded in a URL.
    re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"),
)


def redact_secrets(text: str | None) -> str:
    """Return ``text`` with anything shaped like a credential removed.

    Never raises: this runs on the failure path, where a second exception would
    lose the original error entirely.
    """
    if not text:
        return ""
    try:
        cleaned = text
        for pattern in _SECRET_PATTERNS:
            if pattern.groups:
                cleaned = pattern.sub(rf"\g<1>{REDACTED}@", cleaned)
            else:
                cleaned = pattern.sub(REDACTED, cleaned)
        return cleaned
    except Exception:  # pragma: no cover - redaction must never mask a failure
        return REDACTED
