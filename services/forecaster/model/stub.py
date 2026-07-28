import numpy as np
from datetime import date, timedelta

class StubForecaster:
    def predict(self, company_code: str, horizon_days: int, historical_cash_flows: list[float], start_date: date) -> dict:
        if not historical_cash_flows:
            raise ValueError("historical_cash_flows cannot be empty")
            
        mean_cf = np.mean(historical_cash_flows)
        std_cf = np.std(historical_cash_flows)
        
        forecast = []
        flags = set()
        day_scores = []
        
        for i in range(1, horizon_days + 1):
            pred_date = start_date + timedelta(days=i)
            # Confidence interval: +- 15% of trailing std
            ci_margin = 0.15 * std_cf
            conf_low = mean_cf - ci_margin
            conf_high = mean_cf + ci_margin
            
            day_score = max(0.4, 1.0 - (i / horizon_days) * 0.5)
            day_scores.append(day_score)
            
            if i > 10 and day_score < 0.6:
                flags.add("LOW_CONFIDENCE_BEYOND_DAY_10")
                
            forecast.append({
                "date": pred_date.isoformat(),
                "predictedNetCashFlow": f"{mean_cf:.2f}",
                "confidenceLow": f"{conf_low:.2f}",
                "confidenceHigh": f"{conf_high:.2f}",
                "dayConfidenceScore": round(float(day_score), 2)
            })
            
        overall_score = float(np.mean(day_scores))
        
        return {
            "companyCode": company_code,
            "forecastHorizonDays": horizon_days,
            "modelType": "STUB_TRAILING_AVERAGE",
            "forecast": forecast,
            "overallConfidenceScore": round(overall_score, 2),
            "flags": list(flags),
            "fallbackUsed": True,
            "fallbackReason": "LSTM weights not found"
        }
