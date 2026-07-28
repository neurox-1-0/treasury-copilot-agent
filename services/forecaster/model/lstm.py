import numpy as np
import tensorflow as tf
from datetime import date, timedelta
from pathlib import Path
import joblib

class LSTMForecaster:
    def __init__(self, weights_dir: str = "model/weights"):
        self.weights_dir = Path(weights_dir)
        self.model_path = self.weights_dir / "lstm_model.keras"
        self.feature_scaler_path = self.weights_dir / "feature_scaler.pkl"
        self.target_scaler_path = self.weights_dir / "target_scaler.pkl"
        
        self.model = None
        self.feature_scaler = None
        self.target_scaler = None
        
        if self.model_path.exists() and self.feature_scaler_path.exists() and self.target_scaler_path.exists():
            self.model = tf.keras.models.load_model(self.model_path)
            self.feature_scaler = joblib.load(self.feature_scaler_path)
            self.target_scaler = joblib.load(self.target_scaler_path)
            
    def predict(self, company_code: str, horizon_days: int, feature_matrix: np.ndarray, start_date: date) -> dict:
        if self.model is None or self.feature_scaler is None or self.target_scaler is None:
            raise RuntimeError("Model weights or scalers not loaded")
            
        if len(feature_matrix.shape) != 2 or feature_matrix.shape[1] != 5:
            raise ValueError("Feature matrix must be of shape (n_days, 5)")
            
        if feature_matrix.shape[0] < 30:
            raise ValueError("Insufficient history (minimum 30 days)")
            
        X = feature_matrix[-60:]
        if len(X) < 60:
            pad = np.zeros((60 - len(X), 5))
            X = np.vstack([pad, X])
            
        X = self.feature_scaler.transform(X)
        X = np.expand_dims(X, axis=0) # shape (1, 60, 5)
        
        # Monte Carlo Dropout inference
        predictions = []
        for _ in range(50):
            pred = self.model(X, training=True).numpy()
            predictions.append(pred[0])
            
        predictions = np.array(predictions) # shape (50, max_horizon)
        
        # Inverse transform predictions
        # reshape to (50 * max_horizon, 1) to inverse transform
        max_horizon = predictions.shape[1]
        predictions = self.target_scaler.inverse_transform(predictions.reshape(-1, 1)).reshape(50, max_horizon)
        
        # Slicing to horizon_days
        predictions = predictions[:, :horizon_days]
        
        mean_pred = predictions.mean(axis=0)
        std_pred = predictions.std(axis=0)
        
        confidence_low = mean_pred - 1.96 * std_pred
        confidence_high = mean_pred + 1.96 * std_pred
        
        forecast = []
        flags = set()
        day_scores = []
        
        for i in range(horizon_days):
            pred_date = start_date + timedelta(days=i+1)
            
            mean_val = abs(mean_pred[i]) if abs(mean_pred[i]) > 1e-6 else 1e-6
            std_val = std_pred[i]
            
            day_score = 1.0 - float(np.clip(std_val / mean_val, 0.0, 1.0))
            day_scores.append(day_score)
            
            if i >= 10 and day_score < 0.6:
                flags.add("LOW_CONFIDENCE_BEYOND_DAY_10")
                
            forecast.append({
                "date": pred_date.isoformat(),
                "predictedNetCashFlow": f"{mean_pred[i]:.2f}",
                "confidenceLow": f"{confidence_low[i]:.2f}",
                "confidenceHigh": f"{confidence_high[i]:.2f}",
                "dayConfidenceScore": round(float(day_score), 2)
            })
            
        if np.all(std_pred == 0):
            overall_score = 0.5
            flags.add("DEGENERATE_MODEL_OUTPUT")
        else:
            overall_score = float(np.mean(day_scores))
            
        return {
            "companyCode": company_code,
            "forecastHorizonDays": horizon_days,
            "modelType": "LSTM_MC_DROPOUT",
            "forecast": forecast,
            "overallConfidenceScore": round(overall_score, 2),
            "flags": list(flags),
            "fallbackUsed": False,
            "fallbackReason": None
        }
