from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from schemas import OptimizationRequest, OptimizationResult
from solver import optimize_allocation

app = FastAPI(title="Optimizer Service")
security = HTTPBearer(auto_error=False)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    # In a real app, verify the token.
    return True

@app.post("/optimize", response_model=OptimizationResult)
def optimize(request: OptimizationRequest, valid: bool = Depends(verify_token)):
    if not request.instruments:
        raise HTTPException(status_code=400, detail="NO_INSTRUMENTS_PROVIDED")
        
    return optimize_allocation(request)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
