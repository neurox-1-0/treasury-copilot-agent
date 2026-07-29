/**
 * dashboard/src/hooks/useProposals.ts
 * =====================================
 *
 * React hook that opens a Server-Sent Events connection to
 * ``GET /api/proposals/stream`` and maintains the list of pending proposals
 * in local state.
 *
 * Design rationale
 * ----------------
 * - The agent pushes proposals via SSE so the dashboard never needs to poll.
 *   The hook is the single owner of the SSE connection — it opens on mount
 *   and closes on unmount.
 * - On SSE disconnect, the hook retries with exponential backoff (up to 30 s)
 *   so brief HITL API restarts do not require a page reload.
 * - The ``proposals`` state is a Map keyed by ``proposal_id`` so that an
 *   updated status from a subsequent SSE event replaces the old entry rather
 *   than duplicating it.
 *
 * Usage
 * -----
 * ```tsx
 * const { proposals, connected } = useProposals();
 * ```
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ProposalRecord } from "../types";

interface UseProposalsReturn {
  /** Current list of PENDING proposals in arrival order (newest first). */
  proposals: ProposalRecord[];
  /** Whether the SSE connection is currently open. */
  connected: boolean;
  /** Remove a proposal from the local list (e.g. after approve/reject). */
  removeProposal: (proposalId: string) => void;
}

const SSE_URL = "/api/proposals/stream";
const MAX_BACKOFF_MS = 30_000;

export function useProposals(): UseProposalsReturn {
  const [proposalMap, setProposalMap] = useState<Map<string, ProposalRecord>>(
    new Map()
  );
  const [connected, setConnected] = useState(false);
  const retryDelay = useRef(1_000);
  const esRef = useRef<EventSource | null>(null);

  const connect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
    }

    const es = new EventSource(SSE_URL);
    esRef.current = es;

    es.addEventListener("open", () => {
      setConnected(true);
      retryDelay.current = 1_000; // reset backoff on success
    });

    es.addEventListener("proposal", (event: MessageEvent) => {
      try {
        const incoming: ProposalRecord[] = JSON.parse(event.data);
        setProposalMap((prev) => {
          const next = new Map(prev);
          for (const p of incoming) {
            next.set(p.proposal_id, p);
          }
          return next;
        });
      } catch {
        console.error("[useProposals] Failed to parse SSE data", event.data);
      }
    });

    es.addEventListener("error", () => {
      setConnected(false);
      es.close();
      esRef.current = null;
      // Exponential backoff reconnect
      const delay = retryDelay.current;
      retryDelay.current = Math.min(delay * 2, MAX_BACKOFF_MS);
      setTimeout(connect, delay);
    });
  }, []);

  useEffect(() => {
    connect();
    return () => {
      esRef.current?.close();
    };
  }, [connect]);

  const proposals = Array.from(proposalMap.values()).sort(
    (a, b) =>
      new Date(b.proposed_at).getTime() - new Date(a.proposed_at).getTime()
  );

  const removeProposal = useCallback((proposalId: string) => {
    setProposalMap((prev) => {
      const next = new Map(prev);
      next.delete(proposalId);
      return next;
    });
  }, []);

  return { proposals, connected, removeProposal };
}
