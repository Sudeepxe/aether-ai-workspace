import { useBudget } from "../hooks/useBudget";

/** cost_microcents -> a "$X.XX" display string (1 microcent = 1e-8 USD). */
function formatUsd(microcents: number): string {
  return `$${(microcents / 1e8).toFixed(2)}`;
}

export function UsageIndicator({ workspaceId }: { workspaceId: string }): JSX.Element | null {
  const { data: budget } = useBudget(workspaceId);

  if (budget === undefined) {
    return null;
  }

  const pctUsed =
    budget.monthly_limit_microcents === 0
      ? 100
      : Math.min(100, (budget.settled_microcents / budget.monthly_limit_microcents) * 100);
  const softLimitCrossed = pctUsed >= budget.soft_pct;
  const exhausted = budget.settled_microcents >= budget.monthly_limit_microcents;

  return (
    <div
      className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${
        exhausted
          ? "border-red-300 bg-red-50 text-red-700"
          : softLimitCrossed
            ? "border-amber-300 bg-amber-50 text-amber-700"
            : "border-neutral-200 text-neutral-500"
      }`}
      title={`${formatUsd(budget.settled_microcents)} of ${formatUsd(budget.monthly_limit_microcents)} used this month`}
    >
      <span>
        {formatUsd(budget.settled_microcents)} / {formatUsd(budget.monthly_limit_microcents)}
      </span>
    </div>
  );
}
