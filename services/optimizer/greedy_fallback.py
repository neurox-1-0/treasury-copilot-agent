from schemas import OptimizationRequest, OptimizationResult

def optimize_greedy(req: OptimizationRequest, safe_instruments: list, rejected_alternatives: list) -> OptimizationResult:
    from solver import _build_result
    
    if not safe_instruments:
        return None
        
    # Sort safe instruments by yield rate descending. To break ties, use termDays (ascending).
    # Python's stable sort preserves the original list order for remaining ties.
    best_inst = sorted(safe_instruments, key=lambda x: (-x.rate, x.termDays))[0]
    
    return _build_result(req, best_inst, safe_instruments, rejected_alternatives, "GREEDY_FALLBACK")
