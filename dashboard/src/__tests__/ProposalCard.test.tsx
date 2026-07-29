/**
 * dashboard/src/__tests__/ProposalCard.test.tsx
 * ===============================================
 *
 * Vitest + React Testing Library tests for ``ProposalCard``.
 *
 * Tests match the spec in ``docs/workplan-v1/06-hitl-approval-dashboard.md``
 * (Frontend Tests section).
 *
 * Running
 * -------
 * ::
 *
 *     cd dashboard
 *     npm run test
 *
 * Mocking strategy
 * ----------------
 * - ``fetch`` is replaced with ``vi.fn()`` so no real HTTP calls are made.
 * - A factory function ``makeProposal()`` builds minimal valid
 *   ``ProposalRecord`` fixtures so each test is readable and self-contained.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { ProposalCard } from "../components/ProposalCard";
import type { ProposalRecord } from "../types";

// ── Fixture factory ──────────────────────────────────────────────────────────

function makeProposal(overrides: Partial<ProposalRecord> = {}): ProposalRecord {
  return {
    proposal_id: "test-pid-001",
    cycle_id: "test-cycle-001",
    company_code: "1000",
    status: "PENDING",
    action_type: "SURPLUS_ALLOCATION",
    description: "Move LKR 8,000,000 into a 14-day fixed deposit at 10%",
    rationale:
      "Surplus exceeds minimum buffer. Short term chosen to avoid payroll conflict.",
    alternatives_rejected: [],
    confidence_score: 0.87,
    flagged_ambiguities: [],
    parameter_bounds: { termDays: { min: 1, max: 14 } },
    proposed_at: new Date().toISOString(),
    ...overrides,
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("ProposalCard", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // Default fetch mock — returns APPROVED decision
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        proposal_id: "test-pid-001",
        decision: "APPROVED",
        recorded_at: new Date().toISOString(),
      }),
    });
  });

  it("renders confidence score with correct colour for high confidence", () => {
    const proposal = makeProposal({ confidence_score: 0.87 });
    render(<ProposalCard proposal={proposal} onDecision={vi.fn()} />);

    const ring = screen.getByLabelText(/Confidence score 87 percent/i);
    expect(ring).toBeInTheDocument();
    expect(ring).toHaveClass("confidence--high");
  });

  it("renders amber colour for medium confidence (0.65)", () => {
    const proposal = makeProposal({ confidence_score: 0.65 });
    render(<ProposalCard proposal={proposal} onDecision={vi.fn()} />);

    const ring = screen.getByLabelText(/Confidence score 65 percent/i);
    expect(ring).toBeInTheDocument();
    expect(ring).toHaveClass("confidence--medium");
  });

  it("renders flagged ambiguities as warning badges", () => {
    const proposal = makeProposal({
      flagged_ambiguities: ["LOW_FORECAST_CONFIDENCE", "STALE_DATA_PRESENT"],
    });
    render(<ProposalCard proposal={proposal} onDecision={vi.fn()} />);

    // Both ambiguity badges should be visible
    expect(screen.getByRole("alert", { name: /flagged ambiguities/i })).toBeInTheDocument();
    expect(screen.getByText(/LOW FORECAST CONFIDENCE/i)).toBeInTheDocument();
    expect(screen.getByText(/STALE DATA PRESENT/i)).toBeInTheDocument();
  });

  it("approve button calls decision API with APPROVED", async () => {
    const onDecision = vi.fn();
    const proposal = makeProposal();
    render(<ProposalCard proposal={proposal} onDecision={onDecision} />);

    const approveBtn = screen.getByRole("button", { name: /approve/i });
    await userEvent.click(approveBtn);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        `/api/proposals/${proposal.proposal_id}/decision`,
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining('"decision":"APPROVED"'),
        })
      );
    });
  });

  it("reject button calls decision API with REJECTED", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        proposal_id: "test-pid-001",
        decision: "REJECTED",
        recorded_at: new Date().toISOString(),
      }),
    });

    const onDecision = vi.fn();
    const proposal = makeProposal();
    render(<ProposalCard proposal={proposal} onDecision={onDecision} />);

    const rejectBtn = screen.getByRole("button", { name: /reject/i });
    await userEvent.click(rejectBtn);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        `/api/proposals/${proposal.proposal_id}/decision`,
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining('"decision":"REJECTED"'),
        })
      );
    });
  });
});
