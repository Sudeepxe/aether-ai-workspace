# ADR-9.4: Solo pull-request discipline with required checks; PR history treated as portfolio evidence

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

A solo project has no natural code-review process, but the review discipline itself — checklist-driven self-review, required CI, clean history — is both a real defect-catching mechanism, demonstrated across the blueprint's own per-chapter self-review records, and a signal a hiring-manager reviewer can observe directly in the commit and PR history.

## Decision

Every change goes through a pull request using a template with an enforced self-review checklist, required CI green, and squash-merge with Conventional Commit titles generating the changelog. Branch protection prevents direct pushes to main, requires checks, and enforces linear history. CODEOWNERS names the solo owner explicitly for the highest-risk directories, docs/adr/ and .github/workflows/.

## Alternatives considered

- **Direct commits to main without a PR process, given there are no other collaborators to review** — implicitly rejected; the decision explicitly frames self-review as still being real review, citing the blueprint's own chapter self-review records as evidence the practice catches real defects even solo.

## Consequences

Easier: the PR history itself becomes a portfolio artifact — a reviewer who reads five PRs sees process, not just product; CODEOWNERS plus branch protection is configuration a future team could inherit unchanged. Harder: solo PR discipline is pure overhead in the narrow sense of having no second reviewer, justified entirely by its dual role as a defect-catching practice and a demonstrable signal.

## Revisit trigger

None stated.
