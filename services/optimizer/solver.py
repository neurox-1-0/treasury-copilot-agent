from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
import logging
from schemas import OptimizationRequest, OptimizationResult, Allocation, RejectedAlternative
from greedy_fallback import optimize_greedy

try:
    from scipy.optimize import linprog
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

logger = logging.getLogger(__name__)

def optimize_allocation(req: OptimizationRequest) -> OptimizationResult:
    # 1. Feasibility Pre-check
    if req.availableSurplus <= 0:
        return OptimizationResult(
            recommendedAllocation=[],
            alternativesConsidered=[],
            constraintsSatisfied=False,
            infeasibilityReason="Current balance already at or below minimum buffer. No surplus available to deploy.",
            costOfDebtHurdleBreached=False,
            hurdleNote=None,
            solverUsed="SCIPY_LINPROG" if SCIPY_AVAILABLE else "GREEDY_FALLBACK",
            bufferAfterDeployment=req.currentTotalBalance
        )
        
    if req.availableSurplus < Decimal("100000"):
        return OptimizationResult(
            recommendedAllocation=[],
            alternativesConsidered=[],
            constraintsSatisfied=False,
            infeasibilityReason="Surplus too small for any instrument (minimum LKR 100,000).",
            costOfDebtHurdleBreached=False,
            hurdleNote=None,
            solverUsed="SCIPY_LINPROG" if SCIPY_AVAILABLE else "GREEDY_FALLBACK",
            bufferAfterDeployment=req.currentTotalBalance
        )

    # 2. Instrument Pre-filtering
    safe_instruments = []
    rejected_alternatives = []
    
    for inst in req.instruments:
        maturity_date = req.asOfDate + timedelta(days=inst.termDays)
        exp_yield = (req.availableSurplus * Decimal(str(inst.rate)) * Decimal(inst.termDays) / Decimal("365")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        
        is_safe = True
        reject_reason = ""
        
        if req.nextFixedObligationDate is not None:
            if maturity_date > req.nextFixedObligationDate:
                is_safe = False
                reject_reason = f"Maturity ({maturity_date}) falls after next fixed obligation date ({req.nextFixedObligationDate})."
                
        if is_safe:
            safe_instruments.append(inst)
        else:
            rejected_alternatives.append(RejectedAlternative(
                bank=inst.bank,
                instrument=inst.type,
                termDays=inst.termDays,
                amount=req.availableSurplus,
                maturityDate=maturity_date,
                expectedYield=exp_yield,
                yieldRate=inst.rate,
                rejectedReason=reject_reason
            ))

    if not safe_instruments:
        return OptimizationResult(
            recommendedAllocation=[],
            alternativesConsidered=rejected_alternatives,
            constraintsSatisfied=False,
            infeasibilityReason="No instrument matures before next fixed obligation date. Consider using call deposit only.",
            costOfDebtHurdleBreached=False,
            hurdleNote=None,
            solverUsed="SCIPY_LINPROG" if SCIPY_AVAILABLE else "GREEDY_FALLBACK",
            bufferAfterDeployment=req.currentTotalBalance
        )

    if not SCIPY_AVAILABLE:
        return optimize_greedy(req, safe_instruments, rejected_alternatives)

    # 3. LP Formulation (scipy linprog)
    c = []
    for i, inst in enumerate(safe_instruments):
        yield_coeff = -(inst.rate * inst.termDays / 365.0)
        # Perturb by a tiny amount based on index to favor earlier items in a tie
        yield_coeff -= (1e-9 / (i + 1))
        c.append(yield_coeff)
        
    A_ub = [[1.0] * len(safe_instruments)]
    b_ub = [float(req.availableSurplus)]
    bounds = [(0, None) for _ in safe_instruments]

    res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method='highs')
    
    if not res.success:
        logger.warning(f"LP solver failed: {res.message}. Falling back to greedy.")
        return optimize_greedy(req, safe_instruments, rejected_alternatives)
        
    # Find best instrument chosen by LP
    best_inst = None
    max_amount = 0
    for i, amount in enumerate(res.x):
        if amount > 1.0:
            inst = safe_instruments[i]
            if amount > max_amount:
                max_amount = amount
                best_inst = inst
    
    if not best_inst:
         return optimize_greedy(req, safe_instruments, rejected_alternatives)
         
    return _build_result(req, best_inst, safe_instruments, rejected_alternatives, "SCIPY_LINPROG")

def _build_result(req, best_inst, safe_instruments, rejected_alternatives, solver_used):
    maturity_date = req.asOfDate + timedelta(days=best_inst.termDays)
    exp_yield = (req.availableSurplus * Decimal(str(best_inst.rate)) * Decimal(best_inst.termDays) / Decimal("365")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    
    rec_alloc = Allocation(
        bank=best_inst.bank,
        instrument=best_inst.type,
        termDays=best_inst.termDays,
        amount=req.availableSurplus,
        maturityDate=maturity_date,
        expectedYield=exp_yield,
        yieldRate=best_inst.rate
    )
    
    best_yield_rate = best_inst.rate
    for inst in safe_instruments:
        if inst == best_inst:
            continue
            
        inst_maturity_date = req.asOfDate + timedelta(days=inst.termDays)
        inst_exp_yield = (req.availableSurplus * Decimal(str(inst.rate)) * Decimal(inst.termDays) / Decimal("365")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        
        reason = f"Feasible but sub-optimal. Yields {inst.rate*100:.2f}% compared to recommended {best_yield_rate*100:.2f}%."
        
        rejected_alternatives.append(RejectedAlternative(
            bank=inst.bank,
            instrument=inst.type,
            termDays=inst.termDays,
            amount=req.availableSurplus,
            maturityDate=inst_maturity_date,
            expectedYield=inst_exp_yield,
            yieldRate=inst.rate,
            rejectedReason=reason
        ))

    breached = False
    hurdle_note = None
    if req.costOfDebt is not None:
        if best_inst.rate < req.costOfDebt:
            breached = True
            hurdle_note = f"All available instruments yield below the effective OD rate of {req.costOfDebt*100:.2f}%. Recommend considering partial OD repayment instead of FD placement. Awaiting human approval."
            
    buffer_after = req.currentTotalBalance - req.availableSurplus
    rejected_alternatives.sort(key=lambda x: (-x.yieldRate, x.termDays, x.bank))

    return OptimizationResult(
        recommendedAllocation=[rec_alloc],
        alternativesConsidered=rejected_alternatives,
        constraintsSatisfied=True,
        costOfDebtHurdleBreached=breached,
        hurdleNote=hurdle_note,
        solverUsed=solver_used,
        bufferAfterDeployment=buffer_after
    )
