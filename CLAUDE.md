# Engagement Rules

These rules apply to all work in this repository, for this and all future
sessions. See `docs/REMEDIATION_PLAN.md` for the full remediation plan they
govern.

## NON-NEGOTIABLE RULES

1. **Never fabricate a number.** Do not write a metric, benchmark, latency
   figure, or eval result into any file unless you actually ran the code and
   observed it. If a number is not yet measured, write `TBD — not yet measured`.
   A fabricated result in this repo is worse than no result. This is the single
   most important rule here.
2. **Do not delete or soften the limitations section in the README.** Honest
   stated limitations are an asset in this project, not a defect. You may update
   items that are genuinely fixed, but the section stays and stays honest.
3. **Do not rewrite git history.** No rebasing, squashing, amending, or
   force-pushing existing commits.
4. **Commit discipline.** One logical change per commit. Real messages that say
   what changed and why. No commit touching more than ~10 files unless the
   change is genuinely atomic. No `fix`, `update`, `wip`, `final` messages.
   The commit history is itself a reviewed artifact.
5. **Explain as you go.** After each phase, tell me in plain language what you
   changed and why, at a level where I can defend it verbally without looking at
   the code. If I cannot explain a change, it does not ship.
6. **No scope creep.** Do not refactor things outside the phase you are on, even
   if they look wrong. Note them at the end instead.
7. **Tests must actually pass.** Run them. Paste the real output.
