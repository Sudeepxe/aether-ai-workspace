# syntax=docker/dockerfile:1.7
# Aether web image — build stage only produces static assets; in the demo
# topology Caddy serves them (Blueprint §10.0). This image exists for CI
# artifact/scan purposes and local preview.
# Base images pinned by digest, in `image:tag@digest` form so the tag
# stays human-readable next to the immutable pin (Ch. 10 F-1 discipline:
# no `latest`, no mutable tags). Digests captured 2026-08-14.

FROM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS builder
WORKDIR /build
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY apps/web .
RUN npm run build

FROM caddy:2-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648 AS runtime
COPY --from=builder /build/dist /usr/share/caddy
EXPOSE 80
