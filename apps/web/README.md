# aether-web

React 18 + TypeScript + Vite SPA (Blueprint Ch. 5, ADR-5.1..5.5).

**Sprint 3 state:** the streaming spine's frontend half — login/register,
silent re-auth from the HttpOnly refresh cookie with cross-tab-coordinated
refresh (Web Locks + BroadcastChannel, Ch.5 self-review F-1), and a chat UI
consuming the §4.4 SSE contract via a hand-rolled `fetch()`+`ReadableStream`
parser (ADR-5.3 — native `EventSource` can't carry the POST body/auth header
this contract needs). TanStack Query owns server state (threads/messages);
Zustand owns the one deliberate exception, the in-flight token buffer
(rAF-batched, ADR-5.2).

**Known gap:** there is no workspace switcher yet — `GET /me/workspaces`
was never wired on the backend, because "list every workspace this user
belongs to" is a cross-tenant read that the per-request RLS tenant-scoping
can't express without a policy extension. The SPA creates one workspace +
default thread on first login instead (see `useWorkspaceBootstrap`'s
docstring). A real switcher waits on that RLS decision.

## Commands

```
npm run dev            # Vite dev server, proxies /v1 to :8000
npm run build           # tsc --noEmit && vite build
npm test                 # Vitest unit/component tests
npm run e2e              # Playwright — needs the full stack running (see below)
```

The e2e suite assumes Postgres, Redis, and a migrated API are already
reachable on `:8000` — `make dev && make migrate` from the repo root, then
`cd apps/api && uv run python -m aether.http.main` in another terminal (or
the CI job's equivalent, `.github/workflows/ci.yml`'s `e2e` job).
