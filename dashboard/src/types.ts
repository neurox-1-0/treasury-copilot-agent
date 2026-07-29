/**
 * dashboard/src/types.ts
 * =======================
 *
 * Shared TypeScript types for the HITL Approval Dashboard.
 *
 * These mirror the Pydantic models in ``services/hitl-api/schemas/proposals.py``
 * and the ``decision_log`` schema from ``agent/db/audit_log.py``.
 *
 * Naming convention: camelCase for TypeScript, snake_case for API JSON.
 * The API returns snake_case; types below match the wire format so responses
 * can be assigned directly without transformation.
 */

/** A rejected alternative considered but ruled out by the agent. */
export interface RejectedAlternative {
  option: string;
  reason_rejected: string;
  expected_yield?: string | null;
}

/**
 * A single proposal record from ``decision_log``.
 * Returned by GET /proposals and GET /audit-log.
 */
export interface ProposalRecord {
  proposal_id: string;
  cycle_id: string;
  company_code: string;
  /** PENDING | APPROVED | REJECTED | MODIFIED | TIMEOUT */
  status: string;
  action_type: string;
  description: string;
  rationale: string;
  alternatives_rejected: RejectedAlternative[];
  confidence_score: number;
  flagged_ambiguities: string[];
  /** e.g. { termDays: { min: 1, max: 14 } } */
  parameter_bounds: Record<string, { min: number; max: number }>;
  proposed_at: string;
  decided_at?: string | null;
  human_decision?: string | null;
  modified_parameters?: Record<string, unknown> | null;
  human_note?: string | null;
  payment_status?: string | null;
}

/** Body for POST /proposals/{id}/decision */
export interface DecisionRequest {
  decision: "APPROVED" | "REJECTED" | "MODIFIED";
  modified_parameters?: Record<string, unknown>;
  human_note?: string;
}

/** Success response from POST /proposals/{id}/decision */
export interface DecisionResponse {
  proposal_id: string;
  decision: string;
  verification_result?: {
    constraints_satisfied: boolean;
    buffer_after_modification?: string;
  };
  recorded_at: string;
}

/** 400 error body when MODIFIED parameters violate bounds */
export interface ConstraintViolationError {
  error: "CONSTRAINT_VIOLATION";
  message: string;
  parameter_bounds: Record<string, { min: number; max: number }>;
}

/** Response from GET /feedback/insights */
export interface FeedbackInsights {
  last_30_days: {
    total_proposals: number;
    approved: number;
    rejected: number;
    modified: number;
    approval_rate: number;
  };
  rejection_patterns: {
    pattern: string;
    agent_adaptation: string;
  }[];
}

/** Chaos panel request */
export type ChaosService = "bank-mock" | "erp-mock";
export type ChaosMode = "timeout" | "auth_failure" | "write_failure" | "none";
