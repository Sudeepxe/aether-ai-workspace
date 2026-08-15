"""Generated authz-matrix suite (Blueprint §7.3, §7.6): "authz-matrix
tests are generated from the policy map ... so the matrix cannot drift
from the code."

Two matrices exist at this point in the build:

1. Route x AuthRequirement — Sprint 1 registers only pre-workspace-context
   routes (register/login/refresh are PUBLIC; logout/me require a
   session), so this is the ADR-4.5 half of the stack: every route in the
   running app, compared against an independently-declared source of
   truth. A route added without updating this table fails here, not in
   production.
2. Capability x MembershipRole — domain.policy's RBAC table, checked
   against Blueprint §7.3's matrix transcribed independently in this file
   (not imported from policy.py) so the test can actually catch a typo'd
   policy edit rather than restating it. No route enforces these yet
   (Sprint 1 has no workspace-role-gated endpoint); this half activates
   route coverage as workspace routes land in later sprints.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI

from aether.domain.entities import MembershipRole
from aether.domain.policy import (
    CREATE_MESSAGES,
    MANAGE_BUDGETS,
    MANAGE_DOCUMENTS,
    MANAGE_MEMBERS,
    MANAGE_WORKSPACE,
    READ_AUDIT_LOG,
    READ_KNOWLEDGE_BASE,
    role_may,
)
from aether.http.app import create_app
from aether.http.authz import (
    _EXEMPT_PATHS,
    _EXTRA_KEY,
    AuthRequirement,
    MissingAuthDeclarationError,
    _iter_api_routes,
    assert_all_routes_declare_auth,
    route_auth,
)

pytestmark = pytest.mark.security

# path, method -> expected declared requirement (Sprint 1 + Sprint 2's
# full route set so far).
_EXPECTED_ROUTE_AUTH: dict[tuple[str, str], AuthRequirement] = {
    ("/v1/auth/register", "POST"): AuthRequirement.PUBLIC,
    ("/v1/auth/login", "POST"): AuthRequirement.PUBLIC,
    ("/v1/auth/refresh", "POST"): AuthRequirement.PUBLIC,
    ("/v1/auth/logout", "POST"): AuthRequirement.AUTHENTICATED,
    ("/v1/auth/password-reset:request", "POST"): AuthRequirement.PUBLIC,
    ("/v1/auth/password-reset:confirm", "POST"): AuthRequirement.PUBLIC,
    ("/v1/me", "GET"): AuthRequirement.AUTHENTICATED,
    ("/v1/workspaces", "POST"): AuthRequirement.AUTHENTICATED,
    ("/v1/workspaces/{workspace_id}", "GET"): AuthRequirement.WORKSPACE_MEMBER,
    ("/v1/workspaces/{workspace_id}", "PATCH"): AuthRequirement.WORKSPACE_MEMBER,
    ("/v1/workspaces/{workspace_id}", "DELETE"): AuthRequirement.WORKSPACE_MEMBER,
    ("/v1/workspaces/{workspace_id}/members", "GET"): AuthRequirement.WORKSPACE_MEMBER,
    ("/v1/workspaces/{workspace_id}/members/{user_id}", "PATCH"): AuthRequirement.WORKSPACE_MEMBER,
    ("/v1/workspaces/{workspace_id}/members/{user_id}", "DELETE"): AuthRequirement.WORKSPACE_MEMBER,
    ("/v1/workspaces/{workspace_id}/invitations", "POST"): AuthRequirement.WORKSPACE_MEMBER,
    (
        "/v1/workspaces/{workspace_id}/invitations/{invitation_id}",
        "DELETE",
    ): AuthRequirement.WORKSPACE_MEMBER,
    ("/v1/invitations/{token}:accept", "POST"): AuthRequirement.AUTHENTICATED,
    ("/v1/workspaces/{workspace_id}/threads", "POST"): AuthRequirement.WORKSPACE_MEMBER,
    ("/v1/workspaces/{workspace_id}/threads", "GET"): AuthRequirement.WORKSPACE_MEMBER,
    ("/v1/workspaces/{workspace_id}/threads/{thread_id}", "GET"): AuthRequirement.WORKSPACE_MEMBER,
    (
        "/v1/workspaces/{workspace_id}/threads/{thread_id}",
        "PATCH",
    ): AuthRequirement.WORKSPACE_MEMBER,
    (
        "/v1/workspaces/{workspace_id}/threads/{thread_id}",
        "DELETE",
    ): AuthRequirement.WORKSPACE_MEMBER,
    (
        "/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
        "GET",
    ): AuthRequirement.WORKSPACE_MEMBER,
    (
        "/v1/workspaces/{workspace_id}/threads/{thread_id}/messages",
        "POST",
    ): AuthRequirement.WORKSPACE_MEMBER,
    (
        "/v1/workspaces/{workspace_id}/generations/{generation_id}",
        "DELETE",
    ): AuthRequirement.WORKSPACE_MEMBER,
    (
        "/v1/workspaces/{workspace_id}/generations/{generation_id}",
        "GET",
    ): AuthRequirement.WORKSPACE_MEMBER,
}


def _registered_routes() -> dict[tuple[str, str], AuthRequirement]:
    app = create_app()
    found: dict[tuple[str, str], AuthRequirement] = {}
    for route in _iter_api_routes(app.routes):
        if route.path in _EXEMPT_PATHS:
            continue
        extra = route.openapi_extra or {}
        declared = AuthRequirement(extra[_EXTRA_KEY])
        for method in route.methods or set():
            found[(route.path, method)] = declared
    return found


def test_every_registered_route_matches_the_declared_auth_matrix() -> None:
    actual = _registered_routes()
    assert actual == _EXPECTED_ROUTE_AUTH, (
        "a route was added/changed without updating the authz matrix "
        "(ADR-4.5) — update _EXPECTED_ROUTE_AUTH in this test alongside "
        "the route's openapi_extra=route_auth(...) declaration"
    )


def test_boot_check_rejects_a_route_included_via_router_with_no_declaration() -> None:
    """Regression test: assert_all_routes_declare_auth must actually walk
    routes added via include_router() (the only way any real route is
    registered in this app), not just routes attached directly to the
    FastAPI instance — the two are represented differently in FastAPI's
    routing internals (see _iter_api_routes) and it's easy to fix the
    boot check against the wrong one without a test ever catching it."""
    app = FastAPI()
    router = APIRouter()

    @router.get("/undeclared")
    async def _undeclared() -> dict[str, str]:  # pragma: no cover - never invoked
        return {}

    app.include_router(router)

    with pytest.raises(MissingAuthDeclarationError):
        assert_all_routes_declare_auth(app)


def test_boot_check_accepts_a_router_where_every_route_declares_auth() -> None:
    app = FastAPI()
    router = APIRouter()

    @router.get("/declared", openapi_extra=route_auth(AuthRequirement.PUBLIC))
    async def _declared() -> dict[str, str]:  # pragma: no cover - never invoked
        return {}

    app.include_router(router)

    assert_all_routes_declare_auth(app)  # must not raise


# Blueprint §7.3, transcribed independently of domain/policy.py.
_EXPECTED_CAPABILITY_ROLES: dict[str, frozenset[MembershipRole]] = {
    READ_KNOWLEDGE_BASE: frozenset(
        {MembershipRole.VIEWER, MembershipRole.MEMBER, MembershipRole.ADMIN, MembershipRole.OWNER}
    ),
    CREATE_MESSAGES: frozenset({MembershipRole.MEMBER, MembershipRole.ADMIN, MembershipRole.OWNER}),
    MANAGE_DOCUMENTS: frozenset(
        {MembershipRole.MEMBER, MembershipRole.ADMIN, MembershipRole.OWNER}
    ),
    MANAGE_MEMBERS: frozenset({MembershipRole.ADMIN, MembershipRole.OWNER}),
    MANAGE_BUDGETS: frozenset({MembershipRole.ADMIN, MembershipRole.OWNER}),
    READ_AUDIT_LOG: frozenset({MembershipRole.ADMIN, MembershipRole.OWNER}),
    MANAGE_WORKSPACE: frozenset({MembershipRole.OWNER}),
}


@pytest.mark.parametrize("capability", sorted(_EXPECTED_CAPABILITY_ROLES))
@pytest.mark.parametrize("role", list(MembershipRole))
def test_policy_map_matches_blueprint_matrix(role: MembershipRole, capability: str) -> None:
    expected = role in _EXPECTED_CAPABILITY_ROLES[capability]
    assert role_may(role, capability) is expected


def test_blueprint_matrix_transcription_covers_every_declared_capability() -> None:
    """Guards against a capability being added to policy.py without a
    corresponding row here (the parametrized test above only proves the
    rows it has agree; this proves no row is silently missing)."""
    from aether.domain.policy import capabilities

    assert set(capabilities()) == set(_EXPECTED_CAPABILITY_ROLES)
