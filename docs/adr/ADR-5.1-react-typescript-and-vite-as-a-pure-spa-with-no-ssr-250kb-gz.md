# ADR-5.1: React, TypeScript, and Vite as a pure SPA with no SSR; 250KB gzipped initial budget (D5-1)

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

The entire product lives behind authentication — there is no SEO surface or public content that would benefit from server-side rendering. A frontend architecture decision was needed that keeps the API-first posture honest: the frontend must hold no privilege or logic the public API lacks.

## Decision

React 18 with TypeScript and Vite, as a pure client-rendered SPA with no server-side rendering, subject to a performance budget of at most 250KB gzipped initial JavaScript, enforced via route-level code splitting and a CI bundle-size gate.

## Alternatives considered

- **Next.js (SSR/RSC)** — the industry default and the right answer for anything with a public marketing or SEO surface; rejected here because it adds a Node server runtime and RSC's client/server complexity to render pages that require a JWT to see — cost without benefit for this product shape.

- **SvelteKit** — rejected on ecosystem thinness for this project's needs (mature virtualization, headless accessibility primitives) and hiring-signal legibility; acknowledged as a defensible personal-taste choice elsewhere.

- **HTMX / server-rendered** — the core surface is a streaming, optimistic, stateful chat client that outgrows hypermedia patterns.

- **Angular** — full-framework weight and opinionation exceed a single-product SPA's needs, with no differentiated benefit.

## Consequences

Easier: the smallest system that fully serves the product; static files deploy from a CDN or reverse proxy with zero server runtime. Harder: the SPA pays a first-load cost, bounded by the enforced bundle budget and lazy-loaded heavy bundles (markdown, highlighting, admin); no-JS accessibility is knowingly sacrificed given the inherently interactive product.

## Revisit trigger

A public/SEO surface appears.
