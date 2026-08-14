# ADR-6.6: Tool calls are proposals through a policy engine; injection-flagged context escalates approval

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Phase 2 agents will execute tools based on model output, which is exactly the point where indirect prompt injection (malicious instructions hidden in retrieved documents) could escalate from a bad answer to an unauthorized action — this interface needed to be frozen at design time even though implementation is deferred per ADR-2.2.

## Decision

Tool calls proposed by the model are never executed directly. They pass through a policy engine that checks them against workspace policy and a declared side-effect class (read, write, or destructive). Destructive or external-write tool calls require human approval; a tool call proposed within a turn whose context contains flagged, injection-suspected retrieved content requires elevated approval, cutting the indirect-injection-to-tool-abuse chain at the policy layer rather than relying on model good behavior.

## Alternatives considered

- **Framework-provided agent loops (LangGraph, AutoGen, CrewAI)** — rejected for core with the same reasoning as ADR-6.1; LangGraph's explicit-state-machine model is noted as the design this converges to by hand anyway.

## Consequences

Easier: the highest-risk Phase 2 capability, arbitrary tool execution, has its security boundary designed before any implementation exists, rather than retrofitted. Harder: the policy engine and approval-gate machinery must exist before agents can ship at all — a real implementation dependency, deliberately accepted since Phase 2 depends on Phase 1 observability anyway.

## Revisit trigger

None stated.
