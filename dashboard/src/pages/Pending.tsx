/**
 * dashboard/src/pages/Pending.tsx
 * =================================
 *
 * Landing page (``/``) — the main HITL approval screen.
 *
 * Layout
 * ------
 * - Left: stacked ``ProposalCard`` list fed by the ``useProposals`` SSE hook.
 * - Right: collapsible ``FeedbackInsightPanel`` sidebar.
 *
 * States
 * ------
 * - **Loading / no proposals**: Animated waiting UI with clear status copy.
 * - **SSE connected**: Live indicator dot in the header.
 * - **Proposals present**: One card per pending proposal.
 *
 * The ``FeedbackInsightPanel`` is always accessible via a sidebar toggle
 * button, making the adaptive learning loop visible without cluttering the
 * main review workflow.
 */

import React, { useState } from "react";
import { FeedbackInsightPanel } from "../components/FeedbackInsightPanel";
import { ProposalCard } from "../components/ProposalCard";
import { useProposals } from "../hooks/useProposals";

export const Pending: React.FC = () => {
  const { proposals, connected, removeProposal } = useProposals();
  const [showFeedback, setShowFeedback] = useState(false);

  return (
    <div className="pending-page">
      {/* ── Page header ──────────────────────────────────────── */}
      <div className="pending-page__header">
        <div>
          <h1 className="pending-page__title">Pending Approvals</h1>
          <p className="pending-page__subtitle">
            Review and approve every agent proposal before funds move.
          </p>
        </div>
        <div className="pending-page__header-actions">
          <div
            className={`sse-indicator ${connected ? "sse-indicator--connected" : "sse-indicator--disconnected"}`}
            title={connected ? "Live — receiving proposals" : "Reconnecting…"}
          >
            <span className="sse-indicator__dot" />
            {connected ? "Live" : "Reconnecting"}
          </div>
          <button
            className="btn btn--ghost"
            onClick={() => setShowFeedback((v) => !v)}
            aria-expanded={showFeedback}
            aria-label="Toggle feedback insights panel"
          >
            📊 Insights
          </button>
        </div>
      </div>

      {/* ── Content area ─────────────────────────────────────── */}
      <div className="pending-page__body">
        {/* Proposals column */}
        <div className="pending-page__proposals">
          {proposals.length === 0 ? (
            <div className="pending-page__empty">
              <div className="empty-state__icon">🏦</div>
              <h2 className="empty-state__title">No pending proposals</h2>
              <p className="empty-state__body">
                The agent is monitoring your treasury position. When a
                recommended action is ready, it will appear here in real time.
              </p>
              <div className="empty-state__indicator">
                <span className={`sse-indicator__dot ${connected ? "sse-indicator--connected" : ""}`} />
                <span>{connected ? "Waiting for new proposals…" : "Reconnecting to agent…"}</span>
              </div>
            </div>
          ) : (
            <div className="pending-page__cards" role="list">
              {proposals.map((p) => (
                <div key={p.proposal_id} role="listitem">
                  <ProposalCard
                    proposal={p}
                    onDecision={removeProposal}
                  />
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Feedback sidebar */}
        {showFeedback && (
          <FeedbackInsightPanel onClose={() => setShowFeedback(false)} />
        )}
      </div>

      {/* ── Design rationale footer ────────────────────────────── */}
      <div className="pending-page__rationale">
        <p>
          <strong>Informed oversight by design.</strong> The approval gate sits
          after the agent reasons and before any money moves. It does not gate
          every forecast (too much friction) and does not allow fully autonomous
          execution (unacceptable risk for real capital).
        </p>
      </div>
    </div>
  );
};
