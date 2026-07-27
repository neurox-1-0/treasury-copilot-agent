# Treasury Copilot Agent — Governance & Security
> **Audience**: Security engineers, backend developers, and system administrators.
> This document details the Role-Based Access Control (RBAC), Authentication, and Data Integrity layers added to the HITL approval system.

---

## 1. Authentication & JWT

The Human-In-The-Loop (HITL) dashboard requires authentication before any proposals can be viewed or approved. The backend (`services/hitl-api/auth/`) implements this via **JSON Web Tokens (JWT)**.

- **Token Generation**: Uses pure Python `hmac` and `hashlib` to generate HMAC-SHA256 tokens (`auth/tokens.py`). This removes the need for native C-extensions like `cryptography`, ensuring the system remains lightweight and portable.
- **Login Endpoint**: `/auth/login` accepts a `LoginRequest` (username/password) and returns a standard `access_token` bearer response.
- **Dependency Injection**: FastAPI `Depends` handles extracting and verifying the token via the `get_current_user` dependency.

---

## 2. Role-Based Access Control (RBAC)

The system defines two strict roles at the approval layer, separating daily operational approval from system-wide policy definition.

### `ANALYST` (Treasury Analyst)
- **Permissions**: Can read `PENDING` proposals and submit decisions (`APPROVED`, `REJECTED`, `MODIFIED`) against them.
- **Restriction**: Cannot modify the standing `GoalParameters` (minimum liquidity buffer, target yield, etc.) or view sensitive admin logs.

### `ADMIN` (Treasury Admin / Manager)
- **Permissions**: Holds all `ANALYST` capabilities, plus the ability to modify standing `GoalParameters` (via `PUT /admin/goal-parameters`) and audit the full decision log (via `GET /admin/audit-log`).
- **Separation of Duties**: This separation ensures that changing what the agent optimizes *for* requires higher privileges than approving a single proposed action.

*Role enforcement is performed at the endpoint layer via the `require_analyst` and `require_admin` FastAPI dependencies.*

---

## 3. Data Integrity & Audit Chain (Hash Chains)

A core requirement for a financial system is proving that the audit log of decisions hasn't been tampered with post-execution.

The `decision_log` table in the database implements a **Cryptographic Hash Chain**:
1. When a proposal is generated, it hashes its own contents (`content_hash`).
2. The `insert_proposal()` function fetches the `previous_hash` of the last inserted row.
3. It creates a new composite hash: `SHA256(previous_hash + content_hash)`.
4. This ensures that any manual SQL `UPDATE` or `DELETE` to past decisions breaks the chain, rendering the audit log mathematically invalid.

The chain's integrity can be explicitly verified using the `GET /admin/audit/verify-chain` endpoint, which walks the database rows from start to finish and recalculates the hashes.

---

## 4. Sensitive Data Masking

The Agent reasoning loop processes real account numbers, but these must not be fully exposed in the audit logs or dashboard interfaces unless necessary.

The `agent.resilience` module provides a `mask_account()` utility that masks bank account numbers to display only the last 4 digits (e.g., `SAMP-****5678`). This applies to:
- Source account IDs in the UI
- Beneficiary account IDs in the UI
- Log outputs during reasoning

---

## 5. Background Approval Timeout Scanner

Proposals cannot stay `PENDING` forever. If left unapproved, liquidity models become stale.

A background `asyncio` task (`_background_timeout_checker_loop`) runs on the HITL API lifespan. Every 60 seconds, it calls `process_expired_approvals` to check for proposals that have been pending for >24 hours. Expired proposals are automatically transitioned to `TIMEOUT`, effectively cancelling them so the orchestrator can recalculate a fresh strategy with the next day's data.
