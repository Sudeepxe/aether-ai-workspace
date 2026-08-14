# ADR-7.5: SOPS and age for in-repo secrets, with envelope encryption for stored provider keys

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Runtime secrets (provider API keys, JWT signing keys) need a management approach that avoids plaintext secrets anywhere in git while remaining auditable and versioned, without requiring a company-scale secret-manager subscription for the demo tier.

## Decision

Runtime secrets are injected via environment from the deploy layer; the configuration bundle itself is encrypted in-repo using SOPS plus age, auditable, versioned, and never plaintext at rest in git. The cloud profile swaps to a platform secret manager using the same injection contract. Provider API keys are additionally envelope-encrypted at rest in Postgres — a data key wrapped by a master key held only in the secret manager — so database backup theft alone yields no usable provider credentials.

## Alternatives considered

- **A dedicated cloud secret manager as the only mechanism** — not chosen as the sole v1 mechanism because it doesn't serve the local-first, zero-cloud-dependency demo profile; SOPS plus age works identically locally and in the compose demo profile while remaining swappable for a real secret manager in the cloud profile.

## Consequences

Easier: no secret manager subscription is required to run the full system locally; gitleaks in CI provides a tripwire against accidental plaintext commits. Harder: database backup theft alone doesn't yield provider keys, but the master key's own custody becomes the new critical secret to protect, held only in the secret manager.

## Revisit trigger

Cloud secret manager adoption in the cloud profile.
