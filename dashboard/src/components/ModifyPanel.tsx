/**
 * dashboard/src/components/ModifyPanel.tsx
 * ==========================================
 *
 * Slide-in drawer that lets the treasury analyst modify agent proposal
 * parameters before submitting a MODIFIED decision.
 *
 * Behaviour
 * ---------
 * 1. Renders a bounded slider for ``termDays`` (limits from
 *    ``proposal.parameter_bounds.termDays``).
 * 2. Optionally renders a free-text note field.
 * 3. On submit: POST ``/api/proposals/{id}/decision`` with
 *    ``decision: "MODIFIED"``.
 * 4. If the API returns ``CONSTRAINT_VIOLATION`` (400): displays the error
 *    inline — the human must correct or reject instead.
 * 5. On success: calls ``onSuccess()`` so the parent can remove the card.
 *
 * Props
 * -----
 * proposal:   The full ProposalRecord being modified.
 * onClose:    Called when the drawer is dismissed without a decision.
 * onSuccess:  Called after a successful MODIFIED decision is recorded.
 */

import React, { useState } from "react";
import type {
  ConstraintViolationError,
  DecisionResponse,
  ProposalRecord,
} from "../types";

interface ModifyPanelProps {
  proposal: ProposalRecord;
  onClose: () => void;
  onSuccess: (proposalId: string) => void;
}

export const ModifyPanel: React.FC<ModifyPanelProps> = ({
  proposal,
  onClose,
  onSuccess,
}) => {
  const bounds = proposal.parameter_bounds?.termDays;
  const defaultTerm = bounds
    ? Math.round((bounds.min + bounds.max) / 2)
    : 7;

  const [termDays, setTermDays] = useState<number>(defaultTerm);
  const [humanNote, setHumanNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const resp = await fetch(
        `/api/proposals/${proposal.proposal_id}/decision`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            decision: "MODIFIED",
            modified_parameters: { termDays },
            human_note: humanNote || undefined,
          }),
        }
      );

      if (resp.status === 400) {
        const err: ConstraintViolationError = await resp.json();
        setError(err.message);
        return;
      }

      if (!resp.ok) {
        setError(`Unexpected error: ${resp.status}`);
        return;
      }

      const _data: DecisionResponse = await resp.json();
      onSuccess(proposal.proposal_id);
    } catch (err) {
      setError("Network error — please retry.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modify-panel-overlay" onClick={onClose}>
      <div
        className="modify-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Modify proposal parameters"
      >
        {/* Header */}
        <div className="modify-panel__header">
          <h3 className="modify-panel__title">Modify Proposal</h3>
          <button
            className="modify-panel__close"
            onClick={onClose}
            aria-label="Close modify panel"
          >
            ✕
          </button>
        </div>

        {/* Description */}
        <p className="modify-panel__description">{proposal.description}</p>

        <form onSubmit={handleSubmit}>
          {/* Term days slider */}
          {bounds && (
            <div className="modify-panel__field">
              <label htmlFor="term-slider" className="modify-panel__label">
                Term (days)
                <span className="modify-panel__label-value">{termDays}</span>
              </label>
              <div className="modify-panel__slider-row">
                <span className="modify-panel__slider-bound">{bounds.min}</span>
                <input
                  id="term-slider"
                  type="range"
                  min={bounds.min}
                  max={bounds.max}
                  value={termDays}
                  onChange={(e) => setTermDays(Number(e.target.value))}
                  className="modify-panel__slider"
                />
                <span className="modify-panel__slider-bound">{bounds.max}</span>
              </div>
              <p className="modify-panel__hint">
                Safe range: {bounds.min}–{bounds.max} days (bound by next fixed
                obligation date)
              </p>
            </div>
          )}

          {/* Human note */}
          <div className="modify-panel__field">
            <label htmlFor="human-note" className="modify-panel__label">
              Note (optional)
            </label>
            <textarea
              id="human-note"
              className="modify-panel__textarea"
              placeholder="e.g. Board meeting Friday — prefer shorter lock-up"
              value={humanNote}
              onChange={(e) => setHumanNote(e.target.value)}
              rows={3}
            />
          </div>

          {/* Constraint violation error */}
          {error && (
            <div className="modify-panel__error" role="alert">
              <span className="modify-panel__error-icon">⚠</span>
              {error}
            </div>
          )}

          {/* Actions */}
          <div className="modify-panel__actions">
            <button
              type="button"
              className="btn btn--ghost"
              onClick={onClose}
              disabled={loading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn--primary"
              disabled={loading}
            >
              {loading ? "Submitting…" : "Submit Modification"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
