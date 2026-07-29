# Treasury Copilot HITL Dashboard — `dashboard/`

React + Vite + TypeScript frontend for the Human-in-the-Loop Approval System (Component 6).

## What this dashboard does

The treasury analyst uses this dashboard to review every agent proposal before
any money moves.  The design principle: **informed oversight by design** — not
a rubber-stamp click.

## Screens

| Route | Component | Purpose |
|-------|-----------|---------|
| `/` | `Pending.tsx` | Live list of pending proposals fed by SSE |
| `/audit` | `AuditTrail.tsx` | Filterable audit log with CSV export |
| `/chaos` | `ChaosPanel.tsx` | Developer/demo failure simulation tool |

## Running

```bash
cd dashboard
npm install
npm run dev
```

Dashboard: http://localhost:5173

The Vite dev server proxies `/api/*` to `http://localhost:8006` (HITL API).
Start the HITL API first:

```bash
cd services/hitl-api
uvicorn main:app --port 8006 --reload
```

## Running tests

```bash
npm run test       # Single run (CI)
npm run test:watch # Watch mode
```

## Architecture

```
src/
├── types.ts                      # All TypeScript types (mirrors Pydantic models)
├── hooks/
│   └── useProposals.ts           # SSE hook with exponential backoff reconnect
├── components/
│   ├── ProposalCard.tsx          # Main review card (approve/reject/modify)
│   ├── ModifyPanel.tsx           # Slide-in drawer for parameter modification
│   ├── DecisionLog.tsx           # Audit trail table with filters + CSV export
│   ├── FeedbackInsightPanel.tsx  # Bar chart + rejection pattern chips
│   └── ChaosPanel.tsx            # Developer failure mode toggles
├── pages/
│   ├── Pending.tsx               # Route /
│   └── AuditTrail.tsx            # Route /audit
├── App.tsx                       # Router setup + nav bar
└── main.tsx                      # Vite entry point
```

## Key design decisions

- **SSE over WebSocket**: Proposals flow one-way (server to browser). EventSource
  is natively supported in all browsers; no library needed.
- **Exponential backoff**: useProposals reconnects with doubling delays up to 30s
  on SSE disconnect. Brief HITL API restarts do not require a page reload.
- **No Redux/Zustand**: State is local to each component or the useProposals hook.
  The dashboard is not complex enough to warrant global state management.
- **recharts**: Lightweight, React-native charting for the feedback bar chart.
