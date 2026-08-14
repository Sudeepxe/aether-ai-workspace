"""Refresh-token cookie helpers (ADR-7.1).

``__Host-`` prefix requires the browser to see ``Secure``, no ``Domain``
attribute, and — the part that creates a real tension with the ADR text's
"path-scoped to /v1/auth/refresh" — ``Path=/``. A ``__Host-`` cookie with
any narrower path is silently *rejected by the browser*, which would
break auth in a way that's invisible until someone actually inspects
Set-Cookie headers. The ADR's ``__Host-`` requirement is stated twice
(ADR-7.1 and independently reconfirmed in the Ch.7 self-review, finding
F-1) and is the stronger, more load-bearing guarantee (no subdomain
cookie injection); this implementation keeps ``__Host-`` and uses
``Path=/`` rather than ship a narrower path that would silently not work.
"""

from __future__ import annotations

from fastapi import Response

REFRESH_COOKIE_NAME = "__Host-refresh_token"


def set_refresh_cookie(response: Response, *, value: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=value,
        max_age=max_age_seconds,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax"
    )
