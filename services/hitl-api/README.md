# HITL Approval API — `services/hitl-api/`

FastAPI service for the Human-in-the-Loop Approval Dashboard (Component 6).

## What this service does

Bridges the agent's `decision_log` SQLite table and the React dashboard. It is
a **separate process** from the agent so the dashboard stays responsive even
when the agent is restarted. Every proposal produced by the Decide node
requires explicit human approval before any money moves.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe |
| `GET` | `/proposals?status=PENDING` | List proposals filtered by status |
| `GET` | `/proposals/stream` | SSE stream of pending proposals |
| `POST` | `/proposals/{id}/decision` | Submit approve / reject / modify |
| `GET` | `/audit-log` | Full history with date / type / decision filters |
| `GET` | `/audit-log/export` | Download as CSV |
| `GET` | `/feedback/insights` | Aggregated patterns for Feedback Panel |
| `POST` | `/chaos` | Toggle failure modes on mock services |

## Running locally

```bash
# From the project root (so DATABASE_URL resolves to agent_audit.db)
cd cashflow-copilot-agent
pip install -r services/hitl-api/requirements.txt
uvicorn services.hitl-api.main:app --port 8006 --reload
```

Or from the service directory:

```bash
cd services/hitl-api
pip install -r requirements.txt
uvicorn main:app --port 8006 --reload
```

API docs: http://localhost:8006/docs

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///agent_audit.db` | Shared with the agent |
| `BANK_MOCK_URL` | `http://localhost:8002` | Bank mock base URL |
| `ERP_MOCK_URL` | `http://localhost:8001` | ERP mock base URL |
| `NOTIFICATION_WEBHOOK_URL` | _(empty)_ | Optional Slack / email webhook |

> **Shared DB note**: Both this service and the agent must use the **same
> `DATABASE_URL`** pointing to the same file / PostgreSQL instance. When
> running both locally from the project root, the default `agent_audit.db`
> is in the working directory for both — they will share it automatically.

## Running tests

```bash
cd services/hitl-api
pytest tests/ -v
```

Tests use an in-memory SQLite DB; the real `agent_audit.db` is never touched.

## Architecture

```
decision_log (SQLite / PostgreSQL)
       ▲                ▲
       │ write          │ read + write
       │                │
  agent/db/        services/hitl-api/
  audit_log.py     db/models.py
                        │
                   main.py (FastAPI)
                        │
                   dashboard/ (React + Vite)
```

## Key design decisions

- **No second schema**: The HITL API reads/writes the same `decision_log` table
  the agent creates. No ORM models — raw `text()` queries keep coupling minimal.
- **SSE over WebSocket**: Proposals flow one-way (server → browser). SSE is
  simpler and fully supported by `EventSource` in all modern browsers.
- **Constraint re-verification**: `MODIFIED` decisions are validated against the
  stored `parameter_bounds` before being written. This is intentionally
  lightweight — a full optimizer re-run is out of scope for the HITL service.
