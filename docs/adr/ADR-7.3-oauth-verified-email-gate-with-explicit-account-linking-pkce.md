# ADR-7.3: OAuth verified-email gate with explicit account linking; PKCE mandatory

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

OAuth and social login account-linking is a well-known real-world account-takeover vector — auto-linking an OAuth identity to an existing email account is dangerous if the OAuth provider hasn't verified that email.

## Decision

Authorization-code flow with mandatory PKCE, state for CSRF protection, exact-match registered redirect URIs, and nonce validation on ID tokens. An OAuth identity auto-links to an existing email account only if the identity provider asserts the email is verified; otherwise the flow requires logging into the existing account first, then explicit linking.

## Alternatives considered

- **Auto-linking OAuth identities purely by matching email address regardless of provider-asserted verification status** — explicitly rejected as the vulnerability class that has hit real products historically — an attacker registers at the identity provider with the victim's email, unverified, and auto-link becomes takeover.

## Consequences

Easier: the v1 identity model already separates an identities table from users, shaping the system for future SSO as an additive change rather than a redesign. Harder: legitimate users whose OAuth provider doesn't verify email face an extra explicit-linking step rather than seamless auto-link — an accepted security-over-convenience trade.

## Revisit trigger

None stated.
