"""Deny-by-default route registration (ADR-4.5).

Every route must declare an explicit auth requirement or the app refuses
to boot — "a missing authorization declaration is a boot error, not a
runtime vulnerability waiting to be found." ``assert_all_routes_declare_auth``
runs at the end of ``create_app()``, not just in tests, so this is
actually true of the running process, not just an assertion CI happens
to check.

Sprint 1 had two requirement levels because it had no workspace-scoped
route yet (register/login/refresh are pre-session; logout/me need a
session but not a specific role). Sprint 2 adds WORKSPACE_MEMBER for
routes that need the caller to be *a* member of the workspace in the
path — the specific-capability check (Admin vs Owner vs any member)
still happens in the route body against domain.policy's role_may(), via
the caller_membership already resolved by http/deps.py's
get_workspace_scope; WORKSPACE_MEMBER only declares "this route is
workspace-scoped", which is what the boot-time check and the generated
authz-matrix both actually need to know.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from enum import StrEnum

from fastapi import FastAPI, HTTPException, status
from fastapi.routing import APIRoute, _IncludedRouter
from starlette.routing import BaseRoute

from aether.domain.entities import MembershipRole
from aether.domain.policy import Capability, role_may

_EXEMPT_PATHS = frozenset({"/healthz", "/readyz", "/openapi.json", "/docs", "/redoc"})
_EXTRA_KEY = "x-auth-requirement"


def _iter_api_routes(routes: Iterable[BaseRoute]) -> Iterator[APIRoute]:
    """Recursively flatten ``include_router()``'d routes.

    FastAPI's lazy-router internals leave ``app.routes`` holding an opaque
    ``_IncludedRouter`` wrapper per ``include_router()`` call instead of
    the included router's actual ``APIRoute`` objects, so a naive
    ``isinstance(route, APIRoute)`` walk of ``app.routes`` silently skips
    every route added that way — which, for this app, is all of them.
    Walking ``_IncludedRouter.original_router.routes`` recurses through
    the wrapper to reach the real routes ADR-4.5 actually needs checked.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif isinstance(route, _IncludedRouter):
            yield from _iter_api_routes(route.original_router.routes)


class AuthRequirement(StrEnum):
    PUBLIC = "public"
    AUTHENTICATED = "authenticated"
    WORKSPACE_MEMBER = "workspace_member"


def route_auth(requirement: AuthRequirement) -> dict[str, str]:
    """Pass as ``openapi_extra=route_auth(...)`` on a route decorator."""
    return {_EXTRA_KEY: requirement.value}


def require_capability(role: MembershipRole, capability: Capability) -> None:
    """The capability half of role-scoped enforcement (WORKSPACE_MEMBER
    only proves the caller is *a* member — this proves they're the right
    *kind* of member). Raises 403, not 404: by the time a route calls
    this, get_workspace_scope has already 404'd a caller with no
    membership at all, so a caller reaching here is a known member of a
    known workspace — 403 correctly says "you can't", not "this doesn't
    exist" (§3.6.1 taxonomy)."""
    if not role_may(role, capability):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")


class MissingAuthDeclarationError(RuntimeError):
    pass


def assert_all_routes_declare_auth(app: FastAPI) -> None:
    for route in _iter_api_routes(app.routes):
        if route.path in _EXEMPT_PATHS:
            continue
        extra = route.openapi_extra or {}
        if _EXTRA_KEY not in extra:
            raise MissingAuthDeclarationError(
                f"route {route.path!r} has no declared auth requirement (ADR-4.5); "
                f"add route_auth(...) to its decorator"
            )
