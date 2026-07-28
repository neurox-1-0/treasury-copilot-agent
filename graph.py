"""
agent/graph.py
==============

LangGraph orchestration — defines and compiles the full reasoning loop graph.

Architecture
------------
The graph is a ``StateGraph(AgentContext)`` with the following topology::

    perceive → reason → confidence_check →(conditional)→ decide → END
                                                 ↓
                                            disambiguate → decide → END

The conditional edge after ``confidence_check`` reads ``ctx.route``:
- ``"decide"``      → go directly to Decide
- ``"disambiguate"`` → go through Disambiguate first, then Decide

After Decide, the graph yields (``END``).  Execution resumes when the HITL
API calls ``run_report(ctx)`` with the human decision.

Entry points
------------
- ``build_graph()`` — returns the compiled ``CompiledGraph``.
- ``run_cycle(goal)`` — convenience wrapper that initialises a fresh
  ``AgentContext`` and runs the full perceive→decide loop.
- ``run_report(ctx)`` — called externally (by the HITL API) after the human
  decision is received.

Database initialisation
-----------------------
``build_graph()`` calls ``asyncio.run(init_db())`` on first call to ensure
the ``decision_log`` table exists.  This is idempotent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from langgraph.graph import StateGraph, END

from agent.db.audit_log import init_db
from agent.nodes.confidence_check import confidence_check_node, route_after_confidence_check
from agent.nodes.decide import decide_node
from agent.nodes.disambiguate import disambiguate_node
from agent.nodes.perceive import perceive_node
from agent.nodes.reason import reason_node
from agent.nodes.report import report_node
from agent.state import AgentContext, TreasuryGoal

logger = logging.getLogger(__name__)

_graph_instance = None
_db_initialised = False


def _ensure_db() -> None:
    """Ensure the audit log database is initialised (idempotent)."""
    global _db_initialised
    if not _db_initialised:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # In an async context, schedule it
                asyncio.ensure_future(init_db())
            else:
                loop.run_until_complete(init_db())
        except Exception as exc:  # noqa: BLE001
            logger.warning("DB init warning: %s", exc)
        _db_initialised = True


def build_graph():
    """
    Build and compile the full Treasury Copilot reasoning loop graph.

    Returns
    -------
    CompiledGraph
        A compiled LangGraph ``StateGraph`` ready for ``.invoke()`` or
        ``.ainvoke()``.

    Graph topology
    --------------
    ::

        perceive
           ↓
        reason
           ↓
        confidence_check
           ↓ (conditional on ctx.route)
        ┌──────────────────┐
        │ "decide"         │ "disambiguate"
        ↓                  ↓
        decide ←──── disambiguate
           ↓
          END

    Node descriptions
    -----------------
    - ``perceive``:         Sync node — queries ERP + bank, builds TreasuryState
    - ``reason``:           Async node — calls feedback loop, forecaster, optimizer
    - ``confidence_check``: Sync node — runs 4 quality checks, sets ctx.route
    - ``disambiguate``:     Sync node — stakes formula → ESCALATE or PROCEED_FLAGGED
    - ``decide``:           Async node — verifies constraints, builds ProposedAction
    - ``report``:           Async node — processes human decision (called externally)
    """
    _ensure_db()

    builder = StateGraph(AgentContext)

    # Register nodes
    builder.add_node("perceive", perceive_node)
    builder.add_node("reason", reason_node)
    builder.add_node("confidence_check", confidence_check_node)
    builder.add_node("disambiguate", disambiguate_node)
    builder.add_node("decide", decide_node)
    # Note: "report" is not in the main graph — it is invoked externally
    # after the HITL decision arrives.

    # Entry point
    builder.set_entry_point("perceive")

    # Fixed edges
    builder.add_edge("perceive", "reason")
    builder.add_edge("reason", "confidence_check")

    # Conditional edge: confidence_check → decide OR disambiguate
    builder.add_conditional_edges(
        "confidence_check",
        route_after_confidence_check,  # returns "decide" or "disambiguate"
        {"decide": "decide", "disambiguate": "disambiguate"},
    )

    # disambiguate always flows to decide
    builder.add_edge("disambiguate", "decide")

    # After decide, the graph yields — HITL gate is external
    builder.add_edge("decide", END)

    compiled = builder.compile()
    logger.info("[Graph] Reasoning loop compiled successfully.")
    return compiled


def get_graph():
    """Return the singleton compiled graph (builds once on first call)."""
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = build_graph()
    return _graph_instance


async def run_cycle(goal: TreasuryGoal | None = None) -> AgentContext:
    """
    Run a full perceive → reason → confidence_check → decide cycle.

    This is the main entry point for a fresh reasoning loop run.

    Parameters
    ----------
    goal:
        Treasury goal configuration.  If ``None``, uses the default
        ``TreasuryGoal()`` with company_code="1000".

    Returns
    -------
    AgentContext
        The final context after the Decide node.  Contains the
        ``proposed_action`` awaiting human approval (or ``NO_ACTION`` etc.).
    """
    if goal is None:
        goal = TreasuryGoal()

    initial_ctx = AgentContext(
        goal=goal,
        cycle_id=str(uuid.uuid4()),
    )

    graph = get_graph()
    logger.info("[Graph] Starting cycle %s", initial_ctx.cycle_id)

    result = await graph.ainvoke(initial_ctx)
    final_ctx = AgentContext(**result) if isinstance(result, dict) else result

    logger.info(
        "[Graph] Cycle %s complete — action_type=%s",
        final_ctx.cycle_id,
        final_ctx.proposed_action.action_type if final_ctx.proposed_action else "None",
    )
    return final_ctx


async def run_report(ctx: AgentContext) -> AgentContext:
    """
    Process the human decision after the HITL gate.

    Call this function from the HITL API (Component 6) after populating:
    - ``ctx.human_decision``  (``"APPROVED"`` | ``"REJECTED"`` | ``"MODIFIED"`` | ``"TIMEOUT"``)
    - ``ctx.human_modified_parameters`` (only for ``"MODIFIED"``)
    - ``ctx.human_note`` (optional)

    Parameters
    ----------
    ctx:
        ``AgentContext`` returned by ``run_cycle()``, updated with human decision.

    Returns
    -------
    AgentContext
        Updated context with ``payment_result`` and closed audit log entry.
    """
    logger.info(
        "[Graph] Running report for cycle %s decision=%s",
        ctx.cycle_id, ctx.human_decision,
    )
    return await report_node(ctx)
