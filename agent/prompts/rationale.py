"""
agent/prompts/rationale.py
==========================

LLM prompt templates and rationale generation for the Treasury Copilot Agent.

Design constraint
-----------------
The LLM is called **only** to generate human-readable rationale text.
**All routing, branching, and constraint verification is deterministic.**
This prevents LLM hallucination from affecting control flow or causing the
wrong money to move.

Graceful degradation (no API key)
----------------------------------
If ``GEMINI_API_KEY`` is not set in the environment, ``llm_generate_rationale``
falls back to a deterministic template-based string.  This ensures:

- All agent nodes remain fully testable without any API key.
- The system degrades gracefully in environments without LLM access.
- The fallback output is clearly labelled as ``[TEMPLATE]`` so reviewers
  know an LLM was not used.

Usage
-----
Called from ``agent/nodes/disambiguate.py`` and ``agent/nodes/decide.py``::

    rationale = llm_generate_rationale(ctx, "PROCEED_FLAGGED")
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.state import AgentContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt Templates
# ---------------------------------------------------------------------------

RATIONALE_PROMPT = """You are a treasury analyst generating a concise explanation for a treasury decision.

Decision made: {decision_path}
Proposed action: {action_description}
Key numbers:
  - Available surplus: {surplus} LKR
  - Minimum buffer required: {buffer} LKR
  - Next fixed obligation: {obligation_amount} LKR on {obligation_date}
  - Recommended instrument: {instrument} at {rate}% for {term_days} days
  - Expected yield: {yield_amount} LKR

Rejected alternatives:
{alternatives}

Confidence flags: {flags}

Write a 2-4 sentence explanation of why this recommendation was chosen, in plain \
language suitable for a CFO. Reference the specific numbers above. Do not use \
bullet points. Do not hedge excessively."""

DISAMBIGUATION_RATIONALE_PROMPT = """You are a treasury analyst explaining a disambiguation decision.

Disambiguation outcome: {disambiguation_path}
Conflict flags raised: {flags}
Stakes score: {stakes_score:.2f} (threshold: {threshold:.2f})
Available surplus: {surplus} LKR
Affects fixed obligations: {affects_fixed}

Write 2-3 sentences explaining why the agent chose to {action_verb} this proposal \
rather than the alternative path. Be direct and specific."""


# ---------------------------------------------------------------------------
# Template Fallback (used when GEMINI_API_KEY is absent)
# ---------------------------------------------------------------------------


def _template_rationale(ctx: "AgentContext", decision_path: str) -> str:
    """
    Generate a deterministic rationale string without calling any LLM.

    Used when ``GEMINI_API_KEY`` is not set.  Output is prefixed with
    ``[TEMPLATE]`` to signal that LLM was not used.

    Parameters
    ----------
    ctx:
        The current ``AgentContext``.
    decision_path:
        e.g. ``"PROCEED_FLAGGED"``, ``"ESCALATE"``, ``"SURPLUS_ALLOCATION"``.

    Returns
    -------
    str
        A formatted rationale string.
    """
    ts = ctx.treasury_state
    surplus = f"LKR {ts.available_surplus:,.2f}" if ts else "N/A"
    buffer = f"LKR {ctx.goal.minimum_liquidity_buffer:,.2f}"
    flags = ", ".join(ctx.conflict_flags) if ctx.conflict_flags else "none"

    if decision_path in ("PROCEED_FLAGGED", "ESCALATE"):
        action_verb = "proceed with" if decision_path == "PROCEED_FLAGGED" else "escalate"
        return (
            f"[TEMPLATE] The agent chose to {action_verb} this proposal based on a "
            f"deterministic stakes assessment. Available surplus is {surplus} against "
            f"a minimum buffer of {buffer}. Active confidence flags: {flags}. "
            f"All routing decisions are rule-based; this text is template-generated "
            f"(no LLM API key configured)."
        )

    # Generic fallback for action rationale
    opt_result = ctx.optimizer_result or {}
    allocations = opt_result.get("recommendedAllocation", [])
    if allocations:
        first = allocations[0]
        instrument_desc = (
            f"{first.get('bank', 'Bank')} {first.get('instrument', 'FD')} "
            f"at {first.get('yieldRate', 0):.1%} for {first.get('termDays', 0)} days"
        )
        yield_amt = f"LKR {float(first.get('expectedYield', 0)):,.2f}"
    else:
        instrument_desc = "no instrument recommended"
        yield_amt = "N/A"

    return (
        f"[TEMPLATE] With a surplus of {surplus} available above the {buffer} liquidity "
        f"buffer, the optimizer recommended {instrument_desc} yielding approximately "
        f"{yield_amt}. The decision path was '{decision_path}' with flags [{flags}]. "
        f"This rationale is template-generated (no LLM API key configured)."
    )


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------


def llm_generate_rationale(ctx: "AgentContext", decision_path: str) -> str:
    """
    Generate a human-readable rationale for the given decision path.

    Attempts to call the Gemini Flash API if ``GEMINI_API_KEY`` is set.
    Falls back to a deterministic template string if:

    - ``GEMINI_API_KEY`` is not in the environment.
    - The LLM call raises any exception (network failure, quota exceeded, etc.).

    The LLM **never** makes routing decisions — it only generates the
    ``rationale`` string that appears in ``ProposedAction`` and audit logs.

    Parameters
    ----------
    ctx:
        The current ``AgentContext`` (read-only).
    decision_path:
        One of ``"PROCEED_FLAGGED"``, ``"ESCALATE"``, ``"SURPLUS_ALLOCATION"``,
        ``"NO_ACTION"``, ``"ESCALATE"``, or any action_type string.

    Returns
    -------
    str
        2–4 sentence rationale suitable for a CFO audience.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        logger.debug("GEMINI_API_KEY not set — using template rationale.")
        return _template_rationale(ctx, decision_path)

    try:
        # Lazy import so the module is importable without langchain-google-genai installed
        from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore
        from langchain_core.messages import HumanMessage  # type: ignore

        ts = ctx.treasury_state
        opt = ctx.optimizer_result or {}
        allocations = opt.get("recommendedAllocation", [])
        first_alloc = allocations[0] if allocations else {}

        alts = []
        for alt in opt.get("alternativesConsidered", [])[:3]:
            alts.append(
                f"- {alt.get('bank', '')} {alt.get('instrument', '')} "
                f"{alt.get('termDays', '')}d: {alt.get('rejectedReason', '')}"
            )

        prompt = RATIONALE_PROMPT.format(
            decision_path=decision_path,
            action_description=f"{first_alloc.get('bank', 'N/A')} {first_alloc.get('instrument', '')}",
            surplus=f"{ts.available_surplus:,.2f}" if ts else "N/A",
            buffer=f"{ctx.goal.minimum_liquidity_buffer:,.2f}",
            obligation_amount=f"{ts.next_fixed_obligation_amount:,.2f}" if ts and ts.next_fixed_obligation_amount else "N/A",
            obligation_date=str(ts.next_fixed_obligation_date) if ts and ts.next_fixed_obligation_date else "N/A",
            instrument=f"{first_alloc.get('bank', 'N/A')} {first_alloc.get('instrument', '')}",
            rate=f"{first_alloc.get('yieldRate', 0) * 100:.1f}" if first_alloc else "N/A",
            term_days=first_alloc.get("termDays", "N/A"),
            yield_amount=f"{float(first_alloc.get('expectedYield', 0)):,.2f}" if first_alloc else "N/A",
            alternatives="\n".join(alts) if alts else "No alternatives evaluated.",
            flags=", ".join(ctx.conflict_flags) if ctx.conflict_flags else "none",
        )

        model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        llm = ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key)
        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content.strip()


    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM rationale generation failed (%s) — using template fallback.", exc)
        return _template_rationale(ctx, decision_path)
