"""LLM-judge faithfulness scoring (§6.4, ADR-6.5) — the one eval metric
that genuinely needs a real model call. Reuses the same
``ProviderAdapterPort`` the real LLM Router uses (``adapters.openai``/
``adapters.anthropic``), not a separate judge SDK.

Cross-family enforcement (avoiding self-preference bias, ADR-6.5) is
real and code-enforced, not a comment: the judge is only ever a
*different* provider family than whichever generator answered. If no
provider key is configured at all (this environment's actual,
repeatedly-confirmed state — no OpenAI/Anthropic key), or only a
same-family provider is available, faithfulness is reported as
honestly ``not_measured``/``skipped_same_family`` — never a fabricated
or interpolated score. See metrics.py and adapters/echo/generator.py
for the same honest-fallback posture applied elsewhere in this project.

The ~30-case human-calibration slice with tracked judge/human agreement
(§6.4) needs real human labels this harness cannot manufacture —
``compute_agreement`` is real, reusable machinery for whenever a human
labeler actually produces that data; no synthetic/fabricated human
labels are invented here to pretend that data exists. See the S7
closing report for this tracked, explicit gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

import asyncpg

from aether.config import Settings
from aether.ports.llm import CompletionRequest, LlmMessage, LlmMessageRole, ProviderAdapterPort
from evals.harness.runner import CaseResult

_JUDGE_SYSTEM_PROMPT = (
    "You are a strict faithfulness judge for a retrieval-augmented chat "
    "system. You will be shown the retrieved context a generator was "
    "given, the user's question, the generator's reply, and a rubric. "
    "Judge ONLY whether the reply is faithful to the provided context — "
    "every factual claim in the reply must be supported by the context, "
    "with no invented facts, numbers, or claims not present in it. "
    "Respond in exactly this format, nothing else:\n"
    "VERDICT: faithful|unfaithful\n"
    "REASONING: <one or two sentences>"
)


class FaithfulnessStatus(StrEnum):
    MEASURED = "measured"
    NOT_MEASURED = "not_measured"
    """No LLM provider key configured at all in this environment."""
    SKIPPED_SAME_FAMILY = "skipped_same_family"
    """A provider key exists, but not one from a different family than
    the generator — cross-family judging is refused by design (ADR-6.5),
    not silently downgraded to same-family."""


@dataclass(frozen=True, slots=True)
class FaithfulnessVerdict:
    status: FaithfulnessStatus
    faithful: bool | None
    reasoning: str | None
    judge_model: str | None


def _generator_family(model: str) -> str | None:
    lowered = model.lower()
    if "gpt" in lowered or "openai" in lowered:
        return "openai"
    if "claude" in lowered or "anthropic" in lowered:
        return "anthropic"
    if lowered.startswith("echo"):
        return "echo"
    return None


def _select_judge(
    generator_model: str, settings: Settings
) -> tuple[ProviderAdapterPort, str] | None:
    """Returns (provider, judge_model_name) for a genuinely cross-family
    judge, or None if none is available — the caller turns None into the
    right ``FaithfulnessStatus`` (NOT_MEASURED vs SKIPPED_SAME_FAMILY)."""
    from aether.adapters.anthropic.completion import AnthropicCompletionAdapter
    from aether.adapters.openai.completion import OpenAiCompletionAdapter

    generator_family = _generator_family(generator_model)
    if generator_family != "openai" and settings.openai_api_key:
        return OpenAiCompletionAdapter(api_key=settings.openai_api_key), "gpt-4o-mini"
    if generator_family != "anthropic" and settings.anthropic_api_key:
        return (
            AnthropicCompletionAdapter(api_key=settings.anthropic_api_key),
            "claude-haiku-4-5",
        )
    return None


def _build_judge_prompt(
    *, query: str, context_chunks: list[str], reply: str, rubric: str | None
) -> str:
    context_block = "\n---\n".join(context_chunks) if context_chunks else "(no context retrieved)"
    rubric_block = rubric or "(no case-specific rubric — judge general faithfulness only)"
    return (
        f"RETRIEVED CONTEXT:\n{context_block}\n\n"
        f"QUESTION:\n{query}\n\n"
        f"GENERATOR REPLY:\n{reply}\n\n"
        f"RUBRIC:\n{rubric_block}"
    )


def _parse_verdict(raw: str) -> tuple[bool, str]:
    verdict_line = next((line for line in raw.splitlines() if line.startswith("VERDICT:")), "")
    reasoning_line = next((line for line in raw.splitlines() if line.startswith("REASONING:")), "")
    faithful = "faithful" in verdict_line.lower() and "unfaithful" not in verdict_line.lower()
    reasoning = reasoning_line.removeprefix("REASONING:").strip() or raw.strip()
    return faithful, reasoning


async def judge_faithfulness(
    *,
    query: str,
    context_chunks: list[str],
    reply: str,
    rubric: str | None,
    generator_model: str,
    settings: Settings,
) -> FaithfulnessVerdict:
    if not settings.openai_api_key and not settings.anthropic_api_key:
        return FaithfulnessVerdict(
            status=FaithfulnessStatus.NOT_MEASURED, faithful=None, reasoning=None, judge_model=None
        )

    selected = _select_judge(generator_model, settings)
    if selected is None:
        return FaithfulnessVerdict(
            status=FaithfulnessStatus.SKIPPED_SAME_FAMILY,
            faithful=None,
            reasoning=None,
            judge_model=None,
        )
    provider, judge_model = selected

    request = CompletionRequest(
        messages=[
            LlmMessage(role=LlmMessageRole.SYSTEM, content=_JUDGE_SYSTEM_PROMPT),
            LlmMessage(
                role=LlmMessageRole.USER,
                content=_build_judge_prompt(
                    query=query, context_chunks=context_chunks, reply=reply, rubric=rubric
                ),
            ),
        ],
        model=judge_model,
        max_tokens=200,
    )
    accumulated = ""
    async for chunk in provider.stream_completion(request):
        if isinstance(chunk, str):
            accumulated += chunk
    faithful, reasoning = _parse_verdict(accumulated)
    return FaithfulnessVerdict(
        status=FaithfulnessStatus.MEASURED,
        faithful=faithful,
        reasoning=reasoning,
        judge_model=judge_model,
    )


async def _fetch_chunk_contents(
    bootstrap_pool: asyncpg.Pool, chunk_ids: list[UUID]
) -> dict[UUID, str]:
    """A direct DB read (bootstrap/superuser role, bypassing RLS same as
    corpus.py's ingestion helper) purely to assemble judge input — the
    real client-facing citation payload (issue #59) deliberately doesn't
    carry chunk content, only provenance, so this is the one place the
    harness reaches past the HTTP surface, and only for judge context
    assembly, never to score the product's own retrieval/citation
    behavior (that's runner.py/metrics.py, entirely HTTP-driven)."""
    if not chunk_ids:
        return {}
    rows = await bootstrap_pool.fetch(
        "SELECT id, content FROM chunks WHERE id = ANY($1::uuid[])", chunk_ids
    )
    return {row["id"]: row["content"] for row in rows}


async def judge_case_faithfulness(
    result: CaseResult, *, bootstrap_pool: asyncpg.Pool, settings: Settings
) -> list[FaithfulnessVerdict]:
    """Judges every turn in an already-run case (runner.py's CaseResult)
    against the case's rubric — one verdict per turn, in turn order.
    Skips turns with no content (a refusal, or a failed case) since
    there's nothing to judge faithfulness of."""
    if result.error is not None:
        return []
    verdicts: list[FaithfulnessVerdict] = []
    for turn in result.turns:
        if not turn.grounded or not turn.content:
            continue
        chunk_ids = [UUID(c["chunk_id"]) for c in turn.citations if c.get("chunk_id") is not None]
        contents = await _fetch_chunk_contents(bootstrap_pool, chunk_ids)
        verdicts.append(
            await judge_faithfulness(
                query=turn.query,
                context_chunks=list(contents.values()),
                reply=turn.content,
                rubric=result.case.rubric,
                generator_model=turn.model,
                settings=settings,
            )
        )
    return verdicts


def compute_agreement(human_labels: list[bool], judge_labels: list[bool]) -> float:
    """Judge-vs-human agreement rate over a calibration slice (§6.4) —
    real, reusable machinery for whenever a real human-labeled slice
    exists. No synthetic human labels are fabricated to populate this
    with a number today; see this module's docstring."""
    if len(human_labels) != len(judge_labels):
        raise ValueError("human_labels and judge_labels must be the same length")
    if not human_labels:
        raise ValueError("cannot compute agreement over an empty calibration slice")
    matches = sum(1 for h, j in zip(human_labels, judge_labels, strict=True) if h == j)
    return matches / len(human_labels)
