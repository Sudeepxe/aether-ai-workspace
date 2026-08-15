/**
 * S3 scope decision: there is no `GET /me/workspaces` endpoint yet (it's
 * in §4.3's resource catalog but was never wired — building it needs an
 * RLS-policy decision, since "list every workspace this user belongs
 * to" is structurally a cross-tenant read that the per-request
 * `SET LOCAL app.tenant_id` scoping can't express, unlike every other
 * query in this codebase so far). Rather than make that RLS-model
 * change under this sprint's time pressure, the SPA creates one
 * workspace + thread on first login and remembers the ids client-side
 * (a UUID pair, not a security-sensitive token — fine for localStorage
 * per ADR-5.5, which is about *tenant content*, not identifiers). A
 * real workspace switcher needs the RLS extension above and is
 * deferred to whichever later sprint first needs multi-workspace UX.
 */
import { useEffect, useState } from "react";

import { createThread, createWorkspace } from "../api/chat";

const STORAGE_KEY = "aether.defaultWorkspaceThread";

interface CachedIds {
  workspaceId: string;
  threadId: string;
}

interface BootstrapState {
  ids: CachedIds | null;
  error: string | null;
}

function readCache(): CachedIds | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw === null) {
    return null;
  }
  try {
    return JSON.parse(raw) as CachedIds;
  } catch {
    return null;
  }
}

export function useWorkspaceBootstrap(enabled: boolean): BootstrapState {
  const [state, setState] = useState<BootstrapState>({ ids: readCache(), error: null });

  useEffect(() => {
    if (!enabled || state.ids !== null) {
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const workspace = await createWorkspace("My Workspace");
        const thread = await createThread(workspace.id, "New chat");
        const ids: CachedIds = { workspaceId: workspace.id, threadId: thread.id };
        localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
        if (!cancelled) {
          setState({ ids, error: null });
        }
      } catch (err) {
        if (!cancelled) {
          setState({ ids: null, error: err instanceof Error ? err.message : "bootstrap failed" });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled, state.ids]);

  return state;
}
