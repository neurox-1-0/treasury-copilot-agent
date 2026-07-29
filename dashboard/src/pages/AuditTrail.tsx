/**
 * dashboard/src/pages/AuditTrail.tsx
 * =====================================
 *
 * Audit Trail page (``/audit``).
 *
 * Renders the ``DecisionLog`` component (filterable table) inside a page
 * layout with a descriptive header explaining the purpose of the log.
 *
 * The audit trail is the compliance backbone of the HITL system: every
 * decision, modification, and outcome is recorded here, exportable as CSV
 * for board reporting.
 */

import React from "react";
import { DecisionLog } from "../components/DecisionLog";

export const AuditTrail: React.FC = () => {
  return (
    <div className="audit-trail-page">
      <div className="audit-trail-page__header">
        <h1 className="audit-trail-page__title">Audit Trail</h1>
        <p className="audit-trail-page__subtitle">
          Complete immutable log of every agent proposal and human decision.
          Filter by date, action type, or outcome. Export as CSV for compliance
          and board reporting.
        </p>
      </div>

      <DecisionLog />
    </div>
  );
};
