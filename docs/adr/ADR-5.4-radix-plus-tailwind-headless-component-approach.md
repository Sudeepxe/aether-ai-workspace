# ADR-5.4: Radix plus Tailwind headless component approach

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The SPA needs a component and styling strategy that provides full accessibility behavior (focus management, ARIA, keyboard navigation) without taking on the maintenance burden of owning a complete design system.

## Decision

Headless primitives (Radix UI) for behavior and accessibility, plus Tailwind for styling.

## Alternatives considered

- **MUI/Chakra** — theme-fighting and bundle weight for a project that doesn't need a full opinionated design system.

- **Hand-rolled primitives** — accessibility is precisely where hand-rolling silently fails — focus traps and ARIA correctness are easy to get subtly wrong.

## Consequences

Easier: full accessibility behavior without owning a design system's ongoing maintenance; WCAG 2.1 AA contrast can be lint-gated. Harder: still requires deliberate styling work per component, since Radix provides no visual design out of the box.

## Revisit trigger

A design-system team exists (assessed as never, for a solo project).
