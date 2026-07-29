/**
 * dashboard/src/components/ProposalCard.tsx
 * ===========================================
 *
 * The primary review widget for the Pending Approvals page.
 *
 * What it displays
 * ----------------
 * - Action description (large, prominent typography)
 * - Confidence score ring (green ≥ 0.8, amber 0.6–0.79, red < 0.6)
 * - Flagged ambiguities as warning badges (surfaced prominently)
 * - Rationale text (readable paragraph)
 * - Alternatives Rejected (collapsible list)
 * - Parameter bounds info (if SURPLUS_ALLOCATION + termDays bounds exist)
 * - Three action buttons: Approve, Reject, Modify
 * - Timestamp proposed
 *
 * Approve / Reject flow
 * ---------------------
 * Direct POST to ``/api/proposals/{id}/decision`` with ``APPROVED`` or
 * ``REJECTED``.  On success, ``onDecision()`` is called so the parent
 * removes the card from the list.
 *
 * Modify flow
 * -----------
 * Clicking Modify sets ``showModifyPanel=true``, rendering ``<ModifyPanel>``.
 * On modify success, ``onDecision()`` is called.
 *
 * Props
 * -----
 * proposal:   The ProposalRecord to render.
 * onDecision: Callback invoked after any completed decision.
 */

import React, { useState } from "react";
import type { ProposalRecord } from "../types";
import { ModifyPanel } from "./ModifyPanel";

interface ProposalCardProps {
  proposal: ProposalRecord;
  onDecision: (proposalId: string) => void;
}

/** Determine confidence colour class from a 0–1 score. */
function confidenceClass(score: number): string {
  if (score >= 0.8) return "confidence--high";
  if (score >= 0.6) return "confidence--medium";
  return "confidence--low";
}

function confidenceLabel(score: number): string {
  if (score >= 0.8) return "High";
  if (score >= 0.6) return "Medium";
  return "Low";
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("en-GB", {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

export const ProposalCard: React.FC<ProposalCardProps> = ({
  proposal,
  onDecision,
}) => {
  const [showAlternatives, setShowAlternatives] = useState(false);
  const [showModifyPanel, setShowModifyPanel] = useState(false);
  const [deciding, setDeciding] = useState<"APPROVED" | "REJECTED" | null>(null);
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const [decided, setDecided] = useState(false);

  const submitDecision = async (decision: "APPROVED" | "REJECTED") => {
    setDeciding(decision);
    setDecisionError(null);
    try {
      const resp = await fetch(
        `/api/proposals/${proposal.proposal_id}/decision`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision }),
        }
      );
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        setDecisionError(err?.detail || `Error ${resp.status}`);
        return;
      }
      setDecided(true);
      setTimeout(() => onDecision(proposal.proposal_id), 600);
    } catch {
      setDecisionError("Network error — please retry.");
    } finally {
      setDeciding(null);
    }
  };

  const scoreClass = confidenceClass(proposal.confidence_score);
  const scoreLabel = confidenceLabel(proposal.confidence_score);
  const scorePct = Math.round(proposal.confidence_score * 100);

  if (decided) {
    return (
      <div className="proposal-card proposal-card--decided" aria-live="polite">
        <span className="proposal-card__decided-icon">✓</span>
        <p>Decision recorded.</p>
      </div>
    );
  }

  return (
    <>
      <article className="proposal-card" aria-label={`Proposal: ${proposal.description}`}>
        {/* ── Header ─────────────────────────────────────────────── */}
        <div className="proposal-card__header">
          <div className="proposal-card__meta">
            <span className="proposal-card__action-type">
              {proposal.action_type.replace(/_/g, " ")}
            </span>
            <span className="proposal-card__timestamp">
              {formatDate(proposal.proposed_at)}
            </span>
          </div>

          {/* Confidence ring */}
          <div
            className={`confidence-ring ${scoreClass}`}
            title={`Confidence: ${scorePct}% (${scoreLabel})`}
            aria-label={`Confidence score ${scorePct} percent`}
          >
            <span className="confidence-ring__pct">{scorePct}%</span>
            <span className="confidence-ring__label">{scoreLabel}</span>
          </div>
        </div>

        {/* ── Description ────────────────────────────────────────── */}
        <h2 className="proposal-card__description">{proposal.description}</h2>

        {/* ── Flagged ambiguities ─────────────────────────────────── */}
        {proposal.flagged_ambiguities.length > 0 && (
          <div
            className="proposal-card__ambiguities"
            role="alert"
            aria-label="Flagged ambiguities"
          >
            {proposal.flagged_ambiguities.map((flag) => (
              <span key={flag} className="ambiguity-badge">
                ⚠ {flag.replace(/_/g, " ")}
              </span>
            ))}
          </div>
        )}

        {/* ── Rationale ──────────────────────────────────────────── */}
        <section className="proposal-card__rationale">
          <h3 className="proposal-card__section-title">Agent Rationale</h3>
          <p className="proposal-card__rationale-text">{proposal.rationale}</p>
        </section>

        {/* ── Alternatives rejected ──────────────────────────────── */}
        {proposal.alternatives_rejected.length > 0 && (
          <section className="proposal-card__alternatives">
            <button
              className="proposal-card__alternatives-toggle"
              onClick={() => setShowAlternatives((v) => !v)}
              aria-expanded={showAlternatives}
            >
              {showAlternatives ? "▾" : "▸"} Alternatives Considered (
              {proposal.alternatives_rejected.length})
            </button>
            {showAlternatives && (
              <ul className="proposal-card__alternatives-list">
                {proposal.alternatives_rejected.map((alt, i) => (
                  <li key={i} className="proposal-card__alternative-item">
                    <span className="alternative__option">{alt.option}</span>
                    <span className="alternative__reason">
                      ✗ {alt.reason_rejected}
                    </span>
                    {alt.expected_yield && (
                      <span className="alternative__yield">
                        Expected yield: LKR {alt.expected_yield}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {/* ── Parameter bounds hint ──────────────────────────────── */}
        {proposal.parameter_bounds?.termDays && (
          <div className="proposal-card__bounds-hint">
            <span className="bounds-hint__icon">🔒</span>
            Modifiable term: {proposal.parameter_bounds.termDays.min}–
            {proposal.parameter_bounds.termDays.max} days (safe range)
          </div>
        )}

        {/* ── Decision error ─────────────────────────────────────── */}
        {decisionError && (
          <div className="proposal-card__error" role="alert">
            ⚠ {decisionError}
          </div>
        )}

        {/* ── Action buttons ─────────────────────────────────────── */}
        <div className="proposal-card__actions">
          <button
            id={`approve-${proposal.proposal_id}`}
            className="btn btn--approve"
            onClick={() => submitDecision("APPROVED")}
            disabled={!!deciding}
            aria-label="Approve this proposal"
          >
            {deciding === "APPROVED" ? "…" : "✓ Approve"}
          </button>

          <button
            id={`reject-${proposal.proposal_id}`}
            className="btn btn--reject"
            onClick={() => submitDecision("REJECTED")}
            disabled={!!deciding}
            aria-label="Reject this proposal"
          >
            {deciding === "REJECTED" ? "…" : "✕ Reject"}
          </button>

          <button
            id={`modify-${proposal.proposal_id}`}
            className="btn btn--modify"
            onClick={() => setShowModifyPanel(true)}
            disabled={!!deciding}
            aria-label="Modify this proposal"
          >
            ✎ Modify
          </button>
        </div>
      </article>

      {/* ── Modify drawer ──────────────────────────────────────────── */}
      {showModifyPanel && (
        <ModifyPanel
          proposal={proposal}
          onClose={() => setShowModifyPanel(false)}
          onSuccess={(pid) => {
            setShowModifyPanel(false);
            setDecided(true);
            setTimeout(() => onDecision(pid), 600);
          }}
        />
      )}
    </>
  );
};
