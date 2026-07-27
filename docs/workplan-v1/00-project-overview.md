# Treasury Copilot Agent — Project Overview

> **Audience**: Any developer, agent, or reviewer picking up this project.
> This document is the single source of truth for what the system is, why it
> exists, and how every component fits together. Read this before any other doc.

---

## Problem Statement (B2B Framing)

Mid-market South Asian companies (LKR 1B–20B revenue) typically run SAP S/4HANA or
Business One but have **no dedicated treasury analyst**. As a result:

- Idle cash sits uninvested because no one is monitoring balances daily.
- Regulatory payments (EPF, ETF, WHT, corporate tax) are missed or late because
  ERP data and payment decisions live in separate silos.
- Vendor payment timing is managed by instinct, not liquidity modelling.
- A Bloomberg Terminal or enterprise TMS (Treasury Management System) costs
  USD 25,000+/year — out of reach for this segment.

**This system is a TMS-grade autonomous treasury agent delivered as a SaaS layer
on top of an existing SAP installation, at a fraction of enterprise TMS cost.**

---

## What the System Does

Given a treasury goal (e.g., *"maintain a minimum liquidity buffer of LKR 20M,
maximise yield above 10% on idle cash, keep payment risk under 10 days"*), an
autonomous multi-agent system:

1. **Perceives** current cash position, payables, payroll, taxes, and loan
   obligations from an ERP and a bank API.
2. **Reasons** using a forecasting tool and an optimisation tool to evaluate
   scenarios against the goal.
3. **Decides** by resolving conflicting signals, checking hard constraints, and
   producing a concrete `ProposedAction` with full rationale and rejected
   alternatives — **not a fixed script**.
4. Routes every proposal through a **human approval gate** before any money moves.
5. **Reports** a full audit trail and feeds every human decision back into future
   proposals via an adaptive feedback loop.

The differentiator is **judgment under ambiguity**: conflicting data, low-confidence
forecasts, competing payment priorities. A pipeline prints outputs; this system
decides *what to do* and *why*, and explains both.

---

## Agency Architecture

This is a **hierarchical multi-agent system**, not a single LLM loop. Each
specialist agent is a LangGraph `StateGraph` subgraph. The Orchestrator
coordinates them and owns the feedback loop.

```
┌──────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR AGENT                         │
│  Holds treasury goals, dispatches sub-tasks, owns HITL gate, │
│  manages feedback loop, handles approval timeout escalation   │
└────────┬────────────┬──────────────┬────────────┬────────────┘
         │            │              │            │
         ▼            ▼              ▼            ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
   │ PERCEIVE │ │  REASON  │ │  DECIDE  │ │    AUDIT     │
   │  AGENT   │ │  AGENT   │ │  AGENT   │ │    AGENT     │
   │          │ │          │ │          │ │              │
   │ Queries  │ │ Runs fore-│ │ Verifies │ │ Logs all     │
   │ ERP +    │ │ cast +   │ │ constraints│ │ steps,      │
   │ Bank API,│ │ optimizer,│ │ generates │ │ detects     │
   │ returns  │ │ evaluates │ │ Proposed- │ │ anomalies   │
   │ Treasury-│ │ confidence│ │ Action   │ │ in trail    │
   │ State    │ │           │ │          │ │              │
   └──────────┘ └──────────┘ └──────────┘ └──────────────┘
```

Each agent has a well-defined input/output contract (Pydantic models). This makes
every step independently testable, replayable, and replaceable.

---

## High-Level Data Flow (Full Loop)

```
Treasury Goal (config)
        │
        ▼
┌───────────────────────────────────────────────────────────────────┐
│                        ORCHESTRATOR                                │
│                                                                    │
│  ┌──────────┐    ┌─────────────────┐    ┌──────────────────────┐  │
│  │ ERP Mock │◄──►│  PERCEIVE AGENT │    │   BANK MOCK API      │  │
│  │ (OData)  │    │  → TreasuryState│◄──►│  (OAuth2 + HMAC)     │  │
│  └──────────┘    └────────┬────────┘    └──────────────────────┘  │
│                           │                                        │
│                           ▼                                        │
│                  ┌────────────────┐                                │
│                  │  REASON AGENT  │                                │
│                  │ calls tools:   │                                │
│                  │ → Forecaster   │                                │
│                  │ → Optimizer    │                                │
│                  └────────┬───────┘                                │
│                           │                                        │
│              ┌────────────▼────────────┐                          │
│              │  Confidence & Conflict  │                           │
│              │       Check             │                           │
│              └──────┬──────────┬───────┘                          │
│              high   │          │  low confidence /                 │
│              conf.  │          │  conflicting signals              │
│                     │          ▼                                   │
│                     │   ┌────────────────┐                        │
│                     │   │  DISAMBIGUATE  │                        │
│                     │   │  (rules-first, │                        │
│                     │   │  LLM rationale)│                        │
│                     │   └──────┬─────────┘                        │
│                     │          │                                   │
│                     ▼          ▼                                   │
│              ┌──────────────────────────────┐                     │
│              │        DECIDE AGENT          │                     │
│              │ Verify constraints → produce │                     │
│              │ ProposedAction + contentHash │                     │
│              └──────────────┬───────────────┘                     │
│                             │                                      │
│                             ▼                                      │
│              ┌──────────────────────────────┐                     │
│              │   HUMAN APPROVAL GATE (HITL) │                     │
│              │   approve / reject / modify  │                     │
│              │   (timeout → escalate)       │                     │
│              └──────────┬───────────────────┘                     │
│                         │                                          │
│           ┌─────────────┴─────────────┐                          │
│           ▼                           ▼                           │
│    approved → execute           rejected/modified                  │
│    via Bank API                        │                           │
│           │                           │                           │
│           ▼                           ▼                           │
│    ┌─────────────┐         ┌───────────────────────┐             │
│    │ AUDIT AGENT │         │ Feedback logged to    │             │
│    │ close trail │         │ decision_log table →  │             │
│    └─────────────┘         │ shapes future Reason  │             │
│                            │ cycle constraints     │             │
│                            └───────────────────────┘             │
└───────────────────────────────────────────────────────────────────┘
```

---

## System Components

| # | Component | Status | Location | Doc |
|---|---|---|---|---|
| 1 | Mock SAP ERP | ✅ Built | `services/erp-mock/` | `01-mock-sap-erp.md` |
| 2 | Mock Bank API | ✅ Built | `services/bank-mock/` | `02-mock-sampath-bank-api.md` |
| 3 | LSTM Forecasting Tool | ✅ Built | `services/forecaster/` | `03-forecasting-tool-lstm.md` |
| 4 | SciPy Optimizer Tool | ✅ Built | `services/optimizer/` | `04-optimizer-tool-scipy.md` |
| 5 | Agent Core (multi-agent graph) | ✅ Built | `agent/` | `05-agent-core-reasoning-loop.md` |
| 6 | HITL Approval Backend API | ✅ Built | `services/hitl-api/` | `06-hitl-approval-dashboard.md` |
| 7 | Failure Handling & Resilience | ✅ Built | `agent/resilience.py` | `07-failure-handling-resilience.md` |
| 8 | Governance & Security | ✅ Built | `services/hitl-api/auth/` | `10-governance-and-security.md` |
| 9 | Market Data Tool | ✅ Built | `services/market-data/` | `09-market-data-tool.md` |

---

## Project Directory Structure

```
cashflow-copilot-agent/
│
├── services/
│   ├── erp-mock/               # Component 1 (move from docs/sap/)
│   │   ├── main.py
│   │   ├── schemas/entities.py
│   │   ├── odata/core.py
│   │   ├── odata/metadata.py
│   │   ├── data/seed.py
│   │   ├── tests/test_erp_mock.py
│   │   └── requirements.txt
│   │
│   ├── bank-mock/              # Component 2
   │   ├── main.py
   │   ├── schemas/entities.py
   │   ├── data/seed.py
   │   ├── state/account_state.py
   │   ├── state/loan_state.py
   │   ├── tests/test_bank_mock.py
   │   └── requirements.txt
   │
   ├── market-data/            # Component 9
   │   ├── main.py
   │   ├── scrapers/
   │   │   ├── sampath_scraper.py
   │   │   ├── hnb_scraper.py
   │   │   ├── combank_scraper.py
   │   │   └── cbsl_scraper.py     # Selenium
   │   ├── scheduler.py
   │   ├── cache/rates_cache.json
   │   ├── tests/test_market_data.py
   │   └── requirements.txt
   │
│   ├── forecaster/             # Component 3
│   │   ├── main.py
│   │   ├── model/lstm.py
│   │   ├── model/stub.py
│   │   ├── model/weights/      # Saved LSTM weights
│   │   ├── data/series_generator.py
│   │   ├── tests/test_forecaster.py
│   │   └── requirements.txt
│   │
│   ├── optimizer/              # Component 4
│   │   ├── main.py
│   │   ├── solver.py
│   │   ├── greedy_fallback.py
│   │   ├── tests/test_optimizer.py
│   │   └── requirements.txt
│   │
│   └── hitl-api/               # Component 6 backend & 8 (Governance)
│       ├── main.py
│       ├── auth/               # RBAC & JWT logic
│       ├── schemas/proposals.py
│       ├── db/models.py
│       ├── tests/test_hitl_api.py
│       └── requirements.txt
│
├── agent/                      # Component 5 (the core)
│   ├── graph.py                # LangGraph orchestration
│   ├── state.py                # TreasuryState, ProposedAction Pydantic models
│   ├── timeout_checker.py      # Background approval scanner
│   ├── nodes/
│   │   ├── perceive.py
│   │   ├── reason.py
│   │   ├── confidence_check.py
│   │   ├── disambiguate.py
│   │   ├── decide.py
│   │   └── report.py
│   ├── tools/
   │   ├── erp_client.py
   │   ├── bank_client.py
   │   ├── market_data_client.py   # Component 9 client
   │   ├── forecast_client.py
   │   └── optimizer_client.py
│   ├── memory/
│   │   ├── cache.py            # DataCache stale-data tracking
│   │   └── feedback.py         # decision_log query layer
│   ├── db/audit_log.py
│   ├── resilience.py           # Fallback, masking, & retry mechanisms
│   ├── prompts/rationale.py
│   ├── tests/
│   │   ├── test_graph.py
│   │   └── test_nodes/
│   └── requirements.txt
│
├── dashboard/                  # Component 6 frontend (React + Vite)
│   ├── src/
│   │   ├── components/
│   │   │   ├── ProposalCard.tsx
│   │   │   ├── DecisionLog.tsx
│   │   │   ├── FeedbackInsightPanel.tsx
│   │   │   └── ChaosPanel.tsx
│   │   ├── pages/
│   │   │   ├── Pending.tsx
│   │   │   └── AuditTrail.tsx
│   │   ├── hooks/useProposals.ts
│   │   └── App.tsx
│   ├── package.json
│   └── vite.config.ts
│
├── docs/
│   ├── workplan-v1/            # This folder
│   └── adr/                    # Architecture Decision Records
│       ├── 001-langgraph-chosen.md
│       ├── 002-rules-first-disambiguation.md
│       └── 003-react-vite-chosen.md
│
├── infra/
│   ├── docker-compose.yml      # All services + postgres
│   └── .env.example
│
├── tests/
│   └── integration/
│       └── test_full_loop.py   # End-to-end: ERP → Agent → Bank → HITL
│
└── README.md
```

---

## Tech Stack (Committed Choices)

| Layer | Choice | Rationale |
|---|---|---|
| Mock ERP / Bank API | Python + FastAPI + Pydantic | Consistent stack, OData v2 shapes |
| Agent orchestration | **LangGraph** (Python) | Stateful graphs, subgraph support, traceable nodes |
| LLM | **Gemini (Configurable)** | Rationale generation (`GEMINI_MODEL` defaults to `gemini-2.0-flash`) |
| Forecasting | TensorFlow/Keras (LSTM) + rule-based stub | Stub de-risks build; LSTM is a drop-in upgrade |
| Optimisation | `scipy.optimize.linprog` + greedy fallback | Small LP, explainable, greedy fallback if scipy unavailable |
| Market data scraping | `httpx` + `BeautifulSoup` (banks) + Selenium (CBSL) | HTTP-first; Selenium only where JavaScript rendering required |
| Dashboard frontend | **React + Vite + TypeScript** | Enterprise-grade UI; Streamlit is prototype-only |
| Dashboard backend | FastAPI + Pure Python JWT | Pure Python auth without native crypto bloat |
| Data storage (decision log) | **SQLite** (dev) / **PostgreSQL** (prod) | Seamless switching via `asyncpg` / `aiosqlite` & auto DB init |
| Retry / resilience | `tenacity` | Clean retry-then-cache-then-flag pattern |
| State management | Pydantic models at every node boundary | Traceable, testable, LLM hallucination-resistant |

**Not chosen**: LlamaIndex Agent Workflows (less graph control), Streamlit (prototype aesthetic), any auto-scaling/service-mesh resilience (out of scope for this demo).

---

## Core Design Principles

### 1. `PaymentPriority` is the Heart of the System

`FIXED` vs `FLEXIBLE` is threaded through every payable-like entity. The agent can
delay a `FLEXIBLE` vendor payment to preserve the buffer; it **cannot** delay
payroll, tax, or loan covenant payments. Every component must respect this.

### 2. Rules Make Decisions, LLM Generates Rationale

The Disambiguate node uses **deterministic rules** (stakes score formula) to make
the proceed/escalate branching decision. The LLM is then called to generate a
human-readable rationale *for* that decision. This keeps control-flow auditable
and prevents LLM hallucination from causing the wrong money to move.

### 3. Transparency Over Automation

Every proposal shows: what was decided, why, what alternatives were considered,
and what was rejected and why. The Human Approval gate is placed *after reasoning*
and *before execution* — not gating every forecast (too slow) and not allowing
fully autonomous execution (too risky for real capital).

### 4. Failure Is a First-Class Output, Not an Exception

No component should silently fail. Every error state (stale data, low confidence,
infeasible optimisation, payment write failure) surfaces explicitly in the agent's
output, with a named failure mode and a defined fallback behaviour.

---

## Mock Company (Demo Context)

The mock company is a **fictional FMCG/beverage conglomerate** — not a real listed
company. This avoids any implication that synthetic financial data represents a
real entity's actual position. Settings: company code `1000`, currency `LKR`,
fiscal year April–March.

---

## Component Status Tracker

Update this table as components are completed:

| Component | Code Done | Tests Written | Tests Passing | Integrated |
|---|---|---|---|---|
| Mock SAP ERP | ✅ | ✅ | ✅ | ✅ |
| Mock Bank API | ✅ | ✅ | ✅ | ✅ |
| Market Data Tool | ✅ | ✅ | ✅ | ✅ |
| Forecaster (stub) | ✅ | ✅ | ✅ | ✅ |
| Forecaster (LSTM) | ✅ | ✅ | ✅ | ✅ |
| Optimizer | ✅ | ✅ | ✅ | ✅ |
| Agent Core | ✅ | ✅ | ✅ | ✅ |
| HITL Dashboard | ✅ | ✅ | ✅ | ✅ |
| Governance Module | ✅ | ✅ | ✅ | ✅ |
| Integration (full loop) | ✅ | ✅ | ✅ | ✅ |
