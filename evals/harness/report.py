"""Renders a human-readable Markdown report from a real harness run
(§6.4, issue #74) — the honest published artifact this project's North
Star claim rests on. No number here is ever interpolated or assumed:
every metric comes straight from evals/harness/metrics.py's real
computation over a real run's output, and anything not genuinely
measured in this environment says so explicitly rather than being
omitted (an omission reads as "not applicable"; a gap that's silently
missing is worse than one that's stated).
"""

from __future__ import annotations

from evals.harness.judge import FaithfulnessStatus, FaithfulnessVerdict
from evals.harness.metrics import AggregateMetrics, CaseMetrics

_NORTH_STAR_TARGET = 0.90


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _faithfulness_rate(verdicts: list[FaithfulnessVerdict]) -> float | None:
    measured = [v for v in verdicts if v.status == FaithfulnessStatus.MEASURED]
    if not measured:
        return None
    return sum(1 for v in measured if v.faithful) / len(measured)


def render_report(
    *,
    agg: AggregateMetrics,
    case_metrics: list[CaseMetrics],
    faithfulness: list[FaithfulnessVerdict],
    generated_at: str,
    golden_set_version: str = "v1",
) -> str:
    faithfulness_rate = _faithfulness_rate(faithfulness)
    measured_count = sum(1 for v in faithfulness if v.status == FaithfulnessStatus.MEASURED)
    not_measured_count = sum(1 for v in faithfulness if v.status == FaithfulnessStatus.NOT_MEASURED)
    skipped_count = sum(
        1 for v in faithfulness if v.status == FaithfulnessStatus.SKIPPED_SAME_FAMILY
    )

    north_star_met = (
        faithfulness_rate is not None
        and faithfulness_rate >= _NORTH_STAR_TARGET
        and agg.refusal_correctness_rate >= _NORTH_STAR_TARGET
    )
    if faithfulness_rate is None:
        north_star_line = (
            "**Not yet determinable.** Blueprint §1.7's North Star requires "
            f"faithfulness ≥ {_NORTH_STAR_TARGET:.0%} ∧ correct-refusal ≥ "
            f"{_NORTH_STAR_TARGET:.0%}, both against a real generator judged by a real "
            "cross-family LLM. This environment has no LLM provider key configured, so "
            'faithfulness is honestly unmeasured — see "Known gaps" below. This is a '
            "genuinely open item, not a passed-but-unreported result."
        )
    else:
        status_word = "met" if north_star_met else "NOT met"
        north_star_line = (
            f"**{status_word}** — faithfulness {_fmt(faithfulness_rate)}, refusal "
            f"correctness {_fmt(agg.refusal_correctness_rate)} "
            f"(target: both ≥ {_NORTH_STAR_TARGET:.0%})."
        )

    class_counts: dict[str, int] = {}
    for c in case_metrics:
        class_counts[c.case_class.value] = class_counts.get(c.case_class.value, 0) + 1
    class_summary = ", ".join(f"{n} {cls}" for cls, n in sorted(class_counts.items()))

    lines = [
        "# Aether Eval Report",
        "",
        f"**Generated:** {generated_at}",
        f"**Golden set:** {golden_set_version} ({len(case_metrics)} cases — {class_summary})",
        "**Environment:** LocalHashEmbeddingAdapter (embedding_version=1) unless a real "
        "OpenAI/Anthropic key is configured; EchoGenerator unless a real provider key is "
        "configured.",
        "",
        "## North Star (§1.7)",
        "",
        north_star_line,
        "",
        "## Mechanical metrics (real, measured — no LLM judge needed)",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Cases ran successfully | {agg.cases_ran_successfully}/{agg.total_cases} |",
        f"| Refusal correctness | {_fmt(agg.refusal_correctness_rate)} |",
        f"| Retrieval hit rate | {_fmt(agg.retrieval_hit_rate)} |",
        f"| Citation precision | {_fmt(agg.citation_precision_mean)} |",
        f"| Citation recall | {_fmt(agg.citation_recall_mean)} |",
        "",
        "## Faithfulness (§6.4, ADR-6.5 — needs a real cross-family LLM judge)",
        "",
    ]
    if faithfulness_rate is not None:
        lines.append(
            f"**{_fmt(faithfulness_rate)}** faithful, over {measured_count} judged turn(s)."
        )
    elif skipped_count:
        lines.append(
            f"Not measured — {skipped_count} turn(s) had only a same-family judge available "
            "and cross-family judging was refused by design (ADR-6.5)."
        )
    elif not_measured_count:
        lines.append(
            "Not measured — no LLM provider key configured in this environment "
            "(OPENAI_API_KEY / ANTHROPIC_API_KEY both unset)."
        )
    else:
        lines.append("Not measured — no grounded turns to judge in this run.")
    lines += [
        "",
        "## Adversarial safety",
        "",
    ]
    if agg.adversarial_safety_rate is not None:
        lines.append(
            f"**{_fmt(agg.adversarial_safety_rate)}** — measured against a real generator."
        )
    else:
        lines.append(
            "Not applicable under EchoGenerator: it embeds retrieved chunk content "
            "verbatim to prove grounding, injection payload included, regardless of what "
            "the payload says — a trigger phrase's presence reflects correct echo "
            "behavior, not a compromised generator. Meaningful only with a real provider "
            "key configured (see evals/harness/metrics.py's docstring)."
        )
    lines += [
        "",
        "## Known gaps (tracked, not hidden)",
        "",
        "- **No real LLM provider key** in this environment — faithfulness, adversarial "
        "safety, and the North Star target are all honestly unmeasured rather than faked.",
        "- **Golden set is v1** (20 cases), not yet at the blueprint's ~150-case steady "
        "state (evals/golden/v1/README.md).",
        "- **No populated-KB negative cases** (an off-topic query against a non-empty "
        "knowledge base) — the threshold calibration (issue #73) derives a real floor "
        "from positive samples only, not a full precision/recall sweep.",
        "- **No human-labeled calibration slice** for judge/human agreement tracking "
        "(§6.4) — needs a human labeler this harness cannot substitute for "
        "(evals/harness/judge.py's compute_agreement is ready for that data once it exists).",
        "",
        "## Per-case results",
        "",
        "| Case | Class | Ran | Refusal correct | Retrieval hit | Citation precision | Citation recall |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in case_metrics:
        if not c.ran_successfully:
            lines.append(f"| {c.case_id} | {c.case_class.value} | ERROR: {c.error} | | | | |")
            continue
        refusal = "yes" if all(t.refusal_correct for t in c.turns) else "no"
        hits = [t.retrieval_hit for t in c.turns if t.retrieval_hit is not None]
        precisions = [t.citation_precision for t in c.turns if t.citation_precision is not None]
        recalls = [t.citation_recall for t in c.turns if t.citation_recall is not None]
        hit_str = "yes" if hits and all(hits) else ("n/a" if not hits else "no")
        precision_str = _fmt(sum(precisions) / len(precisions)) if precisions else "n/a"
        recall_str = _fmt(sum(recalls) / len(recalls)) if recalls else "n/a"
        lines.append(
            f"| {c.case_id} | {c.case_class.value} | yes | {refusal} | {hit_str} "
            f"| {precision_str} | {recall_str} |"
        )
    lines.append("")
    return "\n".join(lines)
