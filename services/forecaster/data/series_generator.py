import numpy as np

def generate_feature_matrix(
    cash_flow_series: list[float],
    awplr_series: list[float],
    repo_rate_series: list[float],
    best_fd_90d_series: list[float],
    usd_lkr_series: list[float],
) -> np.ndarray:
    """Shape: (n_days, 5). All series must be same length."""
    return np.column_stack([
        cash_flow_series,
        awplr_series,
        repo_rate_series,
        best_fd_90d_series,
        usd_lkr_series,
    ])

def generate_synthetic_exogenous(lookback_days: int = 90) -> dict[str, list[float]]:
    """Generate synthetic exogenous features for training/testing when market data is unavailable."""
    # AWPLR: 12.5% -> 12.0%
    awplr = np.linspace(0.125, 0.120, lookback_days).tolist()
    
    # Repo Rate: 9.0% -> 8.5% at day 45
    repo = [0.09 if i < 45 else 0.085 for i in range(lookback_days)]
    
    # Best FD 90d: lag repo by 7 days
    fd = [0.09 if i < 45 + 7 else 0.085 for i in range(lookback_days)]
    
    # USD/LKR mid: random walk starting at 305
    rng = np.random.RandomState(42)
    usd = [305.0]
    for _ in range(1, lookback_days):
        usd.append(usd[-1] + rng.normal(0, 1.5))
        
    return {
        "awplr": awplr,
        "repo_rate": repo,
        "best_fd_rate_90d": fd,
        "usd_lkr_mid": usd
    }
