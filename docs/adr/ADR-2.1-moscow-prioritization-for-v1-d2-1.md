# ADR-2.1: MoSCoW prioritization for v1 (D2-1)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Aether is a single-engineer product with zero usage data. Chapter 2 needed a prioritization framework for functional and non-functional requirements before scope could be cut into an MVP.

## Decision

Use MoSCoW (Must/Should/Could/Won't) as the prioritization framework. "Won't" is a first-class answer, producing an unambiguous scope contract. MoSCoW's weakness (no intra-bucket ranking) is covered by dependency-ordered phases in Chapter 11.

## Alternatives considered

- **RICE (Reach x Impact x Confidence / Effort)** — Reach and Impact require real user data; with none, the numbers would be invented, and invented precision is worse than honest coarseness.

- **Kano model** — requires user surveys to classify delighters vs. basics; no users exist.

- **Weighted scoring matrix** — weights are as subjective as the ranking they produce; adds a spreadsheet without adding information.

## Consequences

Easier: produces an unambiguous scope contract and demonstrates scoping discipline to a reviewer. Harder: can hide effort differences inside "Must" (a 2-day Must and a 3-week Must look identical) — mitigated by the phase table's explicit exit criteria.

## Revisit trigger

Migrate to RICE if real usage data appears.
