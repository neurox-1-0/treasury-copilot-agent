/**
 * dashboard/src/components/DecisionLog.tsx
 * ==========================================
 *
 * Sortable, filterable table of all past decisions for the Audit Trail page.
 *
 * Data source
 * -----------
 * Fetches from ``GET /api/audit-log`` with optional query params:
 * ``from_date``, ``to_date``, ``action_type``, ``decision``.
 *
 * Columns
 * -------
 * Date | Description | Decision (chip) | Modified Params | Human Note | Payment Status
 *
 * Features
 * --------
 * - Date range picker (from / to)
 * - Action type filter dropdown
 * - Decision filter dropdown
 * - Export button → ``GET /api/audit-log/export`` (downloads CSV)
 * - Pagination (offset / limit)
 * - Loading and empty states
 *
 * This component is self-contained — it manages its own filter and data state.
 */

import React, { useCallback, useEffect, useState } from "react";
import type { ProposalRecord } from "../types";

const PAGE_SIZE = 20;

function decisionChipClass(decision: string | null | undefined): string {
  switch (decision) {
    case "APPROVED": return "chip chip--approved";
    case "REJECTED": return "chip chip--rejected";
    case "MODIFIED": return "chip chip--modified";
    case "TIMEOUT":  return "chip chip--timeout";
    default:         return "chip chip--pending";
  }
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-GB", {
      dateStyle: "short",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

export const DecisionLog: React.FC = () => {
  const [records, setRecords] = useState<ProposalRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);

  // Filters
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [actionType, setActionType] = useState("");
  const [decision, setDecision] = useState("");

  const fetchRecords = useCallback(async (newOffset = 0) => {
    setLoading(true);
    setError(null);

    const params = new URLSearchParams();
    if (fromDate)   params.set("from_date", fromDate);
    if (toDate)     params.set("to_date", toDate);
    if (actionType) params.set("action_type", actionType);
    if (decision)   params.set("decision", decision);
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(newOffset));

    try {
      const resp = await fetch(`/api/audit-log?${params}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setRecords(data.records);
      setTotal(data.count + newOffset); // approximate
      setOffset(newOffset);
    } catch (err) {
      setError("Failed to load audit log. Is the HITL API running?");
    } finally {
      setLoading(false);
    }
  }, [fromDate, toDate, actionType, decision]);

  useEffect(() => {
    fetchRecords(0);
  }, [fetchRecords]);

  const handleExport = () => {
    window.open("/api/audit-log/export", "_blank");
  };

  return (
    <div className="decision-log">
      {/* ── Filters ──────────────────────────────────────────── */}
      <div className="decision-log__filters">
        <div className="filter-group">
          <label htmlFor="filter-from" className="filter-label">From</label>
          <input
            id="filter-from"
            type="date"
            className="filter-input"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
          />
        </div>
        <div className="filter-group">
          <label htmlFor="filter-to" className="filter-label">To</label>
          <input
            id="filter-to"
            type="date"
            className="filter-input"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
          />
        </div>
        <div className="filter-group">
          <label htmlFor="filter-type" className="filter-label">Action Type</label>
          <select
            id="filter-type"
            className="filter-select"
            value={actionType}
            onChange={(e) => setActionType(e.target.value)}
          >
            <option value="">All</option>
            <option value="SURPLUS_ALLOCATION">Surplus Allocation</option>
            <option value="PAYMENT_DEFERRAL">Payment Deferral</option>
            <option value="NO_ACTION">No Action</option>
            <option value="ESCALATE">Escalate</option>
          </select>
        </div>
        <div className="filter-group">
          <label htmlFor="filter-decision" className="filter-label">Decision</label>
          <select
            id="filter-decision"
            className="filter-select"
            value={decision}
            onChange={(e) => setDecision(e.target.value)}
          >
            <option value="">All</option>
            <option value="APPROVED">Approved</option>
            <option value="REJECTED">Rejected</option>
            <option value="MODIFIED">Modified</option>
            <option value="TIMEOUT">Timeout</option>
          </select>
        </div>

        <button
          className="btn btn--ghost decision-log__export"
          onClick={handleExport}
          title="Download audit log as CSV"
        >
          ↓ Export CSV
        </button>
      </div>

      {/* ── Error / Loading ───────────────────────────────────── */}
      {error && (
        <div className="decision-log__error" role="alert">{error}</div>
      )}

      {/* ── Table ─────────────────────────────────────────────── */}
      <div className="decision-log__table-wrap">
        <table className="audit-table" aria-label="Audit trail">
          <thead>
            <tr>
              <th>Date</th>
              <th>Description</th>
              <th>Decision</th>
              <th>Modified Params</th>
              <th>Human Note</th>
              <th>Payment</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6} className="audit-table__loading">
                  Loading…
                </td>
              </tr>
            ) : records.length === 0 ? (
              <tr>
                <td colSpan={6} className="audit-table__empty">
                  No records found.
                </td>
              </tr>
            ) : (
              records.map((r) => (
                <tr key={r.proposal_id} className="audit-table__row">
                  <td className="audit-table__date">
                    {formatDate(r.proposed_at)}
                  </td>
                  <td className="audit-table__description">{r.description}</td>
                  <td>
                    <span className={decisionChipClass(r.human_decision)}>
                      {r.human_decision ?? "PENDING"}
                    </span>
                  </td>
                  <td className="audit-table__params">
                    {r.modified_parameters
                      ? JSON.stringify(r.modified_parameters)
                      : "—"}
                  </td>
                  <td className="audit-table__note">{r.human_note ?? "—"}</td>
                  <td className="audit-table__payment">
                    {r.payment_status ?? "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* ── Pagination ────────────────────────────────────────── */}
      <div className="decision-log__pagination">
        <button
          className="btn btn--ghost"
          disabled={offset === 0 || loading}
          onClick={() => fetchRecords(Math.max(0, offset - PAGE_SIZE))}
        >
          ← Prev
        </button>
        <span className="pagination__info">
          Showing {offset + 1}–{offset + records.length}
        </span>
        <button
          className="btn btn--ghost"
          disabled={records.length < PAGE_SIZE || loading}
          onClick={() => fetchRecords(offset + PAGE_SIZE)}
        >
          Next →
        </button>
      </div>
    </div>
  );
};
