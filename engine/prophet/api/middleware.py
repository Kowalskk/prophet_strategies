"""
Middleware for the Prophet REST API.

BearerTokenMiddleware
---------------------
Validates ``Authorization: Bearer <token>`` on every request except ``/health``
and ``/api/v1/health``.

- If ``settings.api_secret`` is an empty string, authentication is skipped
  (dev/local mode).
- Returns HTTP 401 JSON on missing or invalid token.

CORSMiddleware configuration is also exported from this module so it can be
applied consistently in ``app.py``.
"""

from __future__ import annotations

import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from prophet.config import settings

logger = logging.getLogger(__name__)

# Paths that never require authentication
_PUBLIC_PATHS = {"/health", "/api/v1/health"}


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that checks a static Bearer token.

    Skipped entirely when ``settings.api_secret`` is empty (dev mode).
    """

    async def dispatch(self, request: Request, call_next: object) -> Response:
        # Dev mode: no secret configured → allow all requests
        if not settings.api_secret:
            return await call_next(request)

        # Public paths are always allowed
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        # Extract Bearer token from Authorization header
        auth_header: str = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.warning(
                "Unauthorized request — missing Bearer token: %s %s",
                request.method,
                request.url.path,
            )
            return _unauthorized("Missing or malformed Authorization header.")

        token = auth_header[len("Bearer "):]
        if token != settings.api_secret:
            logger.warning(
                "Unauthorized request — invalid token: %s %s",
                request.method,
                request.url.path,
            )
            return _unauthorized("Invalid Bearer token.")

        return await call_next(request)


def _unauthorized(detail: str) -> Response:
    """Return a 401 JSON response."""
    body = json.dumps({"error": "Unauthorized", "detail": detail})
    return Response(
        content=body,
        status_code=401,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# CORS configuration dict — imported by app.py
# ---------------------------------------------------------------------------

CORS_KWARGS: dict = {
    "allow_origins": settings.cors_origins,
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*", "Authorization"],
}
