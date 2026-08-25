# Postmortem: [short incident title]

**Status:** draft / final
**Date of incident:** YYYY-MM-DD
**Author(s):**
**Severity:** page / ticket
**Alert(s) that fired:** [alert name(s), link to the runbook(s) followed]

> Blameless (§10.4): this document names systems, decisions, and failure modes — never individuals as the cause. "The deploy pipeline lacked a check for X" is useful; "so-and-so forgot to check X" is not, and isn't how failures actually happen in a system with real gaps.

## Summary

One paragraph: what broke, for how long, who/what was affected, and the user-visible impact (if any). Write this section last, after the timeline below is complete.

## Timeline (UTC)

All times from real evidence (logs, dashboards, alert history), not memory.

| Time | Event |
|---|---|
| HH:MM | First user/system-visible symptom |
| HH:MM | Alert fired: `<alert name>` |
| HH:MM | On-call acknowledged / began investigating |
| HH:MM | Root cause identified |
| HH:MM | Mitigation applied |
| HH:MM | Confirmed resolved |
| HH:MM | Alert cleared |

## Root cause

What actually happened, mechanistically — not "the API was down" (a symptom) but the real chain: *why* it was down. Link the exact commit/config/deploy if one is implicated.

## Contributing factors

Everything that made this worse than it needed to be, or that let it happen at all — a missing test, a gap in the alert coverage, a runbook step that didn't work as written, a monitoring blind spot. Each of these is a candidate action item below.

## Impact

- User-visible? (which routes/features, for how long)
- Data impact? (none / degraded / lost — be specific; "none" is a claim that needs the evidence for why, not just an assertion)
- Cost impact? (if relevant — e.g. a budget-cap near-miss)

## What went well

Real detection/mitigation that worked as designed deserves the same honest documentation as what failed — this is calibration data for what to keep, not just what to fix.

## Action items

| Action | Owner | Due | Tracking issue |
|---|---|---|---|
| | | | |

Every contributing factor above should map to at least one action item, or an explicit note on why no action is needed (e.g. "already covered by X, verified").

## Runbook feedback

Did the runbook followed (if any) work as written? If a step was wrong, missing, or unclear, fix the runbook itself as part of this postmortem's action items — a runbook that drifts from reality is worse than no runbook (§10.4's own testable-docs rule).
