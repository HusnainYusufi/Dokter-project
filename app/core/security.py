"""Optional shared-secret authentication for the API.

The portal authenticates its own users through NextAuth; the FastAPI service
sits behind it and historically accepted any caller that could reach it. When
`API_AUTH_TOKEN` is configured, every `/api/v1` request must present it as a
bearer token, which closes that gap for deployments where the API is exposed.

Leaving `API_AUTH_TOKEN` unset keeps the previous open behavior, so existing
local and docker-compose setups are unaffected.
"""
from __future__ import annotations

import hmac

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings


def _extract_bearer(header_value: str | None) -> str | None:
    if not header_value:
        return None
    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


class ApiTokenMiddleware(BaseHTTPMiddleware):
    """Reject unauthenticated API calls when a shared secret is configured."""

    def __init__(self, app, *, protected_prefix: str) -> None:  # noqa: ANN001
        super().__init__(app)
        self.protected_prefix = protected_prefix

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        expected = settings.API_AUTH_TOKEN
        if not expected or not request.url.path.startswith(self.protected_prefix):
            return await call_next(request)

        # Browsers send an unauthenticated preflight before the real request;
        # the CORS middleware answers it and the real request still needs a token.
        if request.method == "OPTIONS":
            return await call_next(request)

        presented = _extract_bearer(request.headers.get("Authorization"))
        if presented and hmac.compare_digest(presented, expected):
            return await call_next(request)

        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Missing or invalid API credentials."},
            headers={"WWW-Authenticate": "Bearer"},
        )
