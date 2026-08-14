# ADR-9.5: Apache-2.0 license; synthetic-only eval corpora for licensing and privacy

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Two separate licensing and legal-exposure decisions needed to be made together: the project's open-source license, and, more consequentially, the provenance of documents used in the eval corpora, since scraped or real-world documents inside the repository create licensing and PII exposure.

## Decision

Apache-2.0 license, with an explicit patent grant and a NOTICE file. All eval corpus documents are synthetic and project-authored — no scraped, copyrighted, or personal content, ever — with a provenance note in evals/corpora/ and a PII scan running in CI over the eval tree as a tripwire.

## Alternatives considered

- **MIT license** — acknowledged as acceptable, but Apache-2.0 is preferred for its explicit patent grant.

- **GPL license** — rejected as friction for a résumé-reader who wants to skim-fork the project.

- **Using real-world or scraped documents for more realistic eval fixtures** — rejected outright as a licensing and PII exposure risk living inside the repository itself.

## Consequences

Easier: the project can be forked and referenced freely without patent-grant ambiguity; eval corpora carry zero licensing or personal-data risk by construction. Harder: synthetic corpora may be less representative of real-world document messiness than scraped data would be — accepted as the correct trade given the legal exposure the alternative would create.

## Revisit trigger

None stated.
