"""Deny-by-default route registration (ADR-4.5).

Every route must declare an explicit auth requirement or the app refuses
to boot — "a missing authorization declaration is a boot error, not a
runtime vulnerability waiting to be found." ``assert_all_routes_declare_auth``
runs at the end of ``create_app()``, not just in tests, so this is
actually true of the running process, not just an assertion CI happens
to check.

Sprint 1 has two requirement levels because it has no workspace-scoped
route yet (register/login/refresh are pre-session; logout/me need a
session but not a specific role). Role-scoped requirements, checked
against the domain.policy capability table, are added the same way when
the first workspace-scoped route lands (S2+) — this module is where that
extension happens, not a redesign.
"""

from __future__ import annotations

from enum import StrEnum

from fastapi import FastAPI
from fastapi.routing import APIRoute

_EXEMPT_PATHS = frozenset({"/healthz", "/readyz", "/openapi.json", "/docs", "/redoc"})
_EXTRA_KEY = "x-auth-requirement"


class AuthRequirement(StrEnum):
    PUBLIC = "public"
    AUTHENTICATED = "authenticated"


def route_auth(requirement: AuthRequirement) -> dict[str, str]:
    """Pass as ``openapi_extra=route_auth(...)`` on a route decorator."""
    return {_EXTRA_KEY: requirement.value}


class MissingAuthDeclarationError(RuntimeError):
    pass


def assert_all_routes_declare_auth(app: FastAPI) -> None:
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in _EXEMPT_PATHS:
            continue
        extra = route.openapi_extra or {}
        if _EXTRA_KEY not in extra:
            raise MissingAuthDeclarationError(
                f"route {route.path!r} has no declared auth requirement (ADR-4.5); "
                f"add route_auth(...) to its decorator"
            )
