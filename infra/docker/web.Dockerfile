# syntax=docker/dockerfile:1.7
# Aether web image — build stage only produces static assets; in the demo
# topology Caddy serves them (Blueprint §10.0). This image exists for CI
# artifact/scan purposes and local preview.
# Base images pinned by digest, in `image:tag@digest` form so the tag
# stays human-readable next to the immutable pin (Ch. 10 F-1 discipline:
# no `latest`, no mutable tags). Digests captured 2026-08-14, node image
# bumped to 22-slim 2026-08-15 (Sprint 3): jsdom/testing-library's latest
# majors require Node >=22, and Node 20 is past its LTS window by this
# date anyway — this is a currency fix, not a scope change.

FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS builder
WORKDIR /build
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY apps/web .
RUN npm run build

FROM caddy:2-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648 AS runtime
COPY --from=builder /build/dist /usr/share/caddy
EXPOSE 80
