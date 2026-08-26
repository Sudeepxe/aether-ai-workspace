# contracts — GENERATED FILES ONLY (ADR-9.2)

`openapi.json`: the real running app's OpenAPI 3.1 spec, generated from
FastAPI/Pydantic route+model declarations (`make openapi`). Committed
so CI can diff for drift (`make openapi-check`, the `contract` job) —
**never hand-edited**. First generated in S10 #107.

TS client types generated from this spec are a Phase 2 item (`docs/api`
gains SDK examples per §9.6's roadmap) — not yet built; nothing here
depends on them existing.
