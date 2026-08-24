import { useState } from "react";

import { useExportJob, useRequestExport } from "../hooks/useExport";

/**
 * FR-AD-5's request -> poll -> download flow (issue #85). Owner-only on
 * the backend (§7.3's "workspace delete/export/transfer" row) — shown
 * to every member here, same posture as the rest of this app's
 * capability-gated actions (the server enforces; the client doesn't
 * pre-filter based on a role the frontend doesn't otherwise track).
 */
export function ExportDataButton({ workspaceId }: { workspaceId: string }): JSX.Element {
  const [jobId, setJobId] = useState<string | null>(null);
  const requestExport = useRequestExport(workspaceId);
  const { data: job } = useExportJob(workspaceId, jobId);

  if (jobId === null) {
    return (
      <button
        type="button"
        onClick={() => {
          requestExport.mutate(undefined, {
            onSuccess: (created) => setJobId(created.id),
          });
        }}
        disabled={requestExport.isPending}
        className="hover:text-neutral-900 disabled:opacity-50"
      >
        {requestExport.isPending ? "Requesting export…" : "Export data"}
      </button>
    );
  }

  if (job === undefined || job.status === "queued" || job.status === "running") {
    return <span className="text-neutral-400">Preparing export…</span>;
  }
  if (job.status === "failed") {
    return (
      <span role="alert" className="text-red-600">
        Export failed
      </span>
    );
  }
  return (
    <a href={job.download_url ?? undefined} className="hover:text-neutral-900">
      Download export
    </a>
  );
}
