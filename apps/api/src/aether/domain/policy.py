"""RBAC policy map (Blueprint §7.3) — the single source of truth for what
each workspace role may do. Pure data; no I/O.

Capabilities beyond Sprint 1's actual routes are included now (not just
the ones wired to an endpoint today) because this *is* the source of
truth the matrix is generated from (ADR-7.4) — adding a capability here
when its route lands in a later sprint is the intended workflow, not
scope creep landing early. Sprint 1 itself has no workspace-role-gated
route (register/login/refresh/logout/me are all pre-workspace-context
operations — see http/authz.py), so no route references this table yet;
the generated authz-matrix test (tests/security/test_authz_matrix.py)
covers what Sprint 1 actually registers, and will grow to cover
capabilities from this table as their routes land.
"""

from __future__ import annotations

from aether.domain.entities import MembershipRole

Capability = str

READ_KNOWLEDGE_BASE: Capability = "read_knowledge_base"
CREATE_MESSAGES: Capability = "create_messages"
MANAGE_DOCUMENTS: Capability = "manage_documents"
MANAGE_MEMBERS: Capability = "manage_members"
MANAGE_BUDGETS: Capability = "manage_budgets"
READ_AUDIT_LOG: Capability = "read_audit_log"
MANAGE_WORKSPACE: Capability = "manage_workspace"
MANAGE_API_KEYS: Capability = "manage_api_keys"

# Blueprint §7.3 matrix, transcribed exactly.
_POLICY: dict[Capability, frozenset[MembershipRole]] = {
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
    # §7.3: "Budgets, model policy, API keys" — Admin/Owner.
    MANAGE_API_KEYS: frozenset({MembershipRole.ADMIN, MembershipRole.OWNER}),
}


def role_may(role: MembershipRole, capability: Capability) -> bool:
    return role in _POLICY[capability]


def capabilities() -> tuple[Capability, ...]:
    return tuple(_POLICY.keys())
