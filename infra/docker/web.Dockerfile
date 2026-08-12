# syntax=docker/dockerfile:1.7
# Aether web image — build stage only produces static assets; in the demo
# topology Caddy serves them (Blueprint §10.0). This image exists for CI
# artifact/scan purposes and local preview.

FROM node:20-slim AS builder
WORKDIR /build
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm npm ci || npm install
COPY apps/web .
RUN npm run build

FROM caddy:2-alpine AS runtime
COPY --from=builder /build/dist /usr/share/caddy
EXPOSE 80
