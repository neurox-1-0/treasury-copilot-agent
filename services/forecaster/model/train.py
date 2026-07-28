import os
import sys
import numpy as np
import tensorflow as tf
from pathlib import Path
from sklearn.preprocessing import MinMaxScaler
import joblib

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from services.erp_mock.data.seed import generate_historical_series
from services.forecaster.data.series_generator import generate_feature_matrix, generate_synthetic_exogenous

def build_model(horizon_days: int = 30):
    model = tf.keras.models.Sequential([
        tf.keras.layers.LSTM(64, return_sequences=True, input_shape=(60, 5)),
        tf.keras.layers.Dropout(0.2),
        tf.keras.layers.LSTM(32),
        tf.keras.layers.Dropout(0.2),
        tf.keras.layers.Dense(horizon_days)
    ])
    model.compile(optimizer='adam', loss='mse')
    return model

def main():
    print("Generating synthetic historical data...")
    lookback = 150
    cash_flow_data = generate_historical_series(lookback_days=lookback)
    cf_series = [float(d["net_cash_flow"]) for d in cash_flow_data]
    
    exo_data = generate_synthetic_exogenous(lookback_days=lookback)
    
    feature_matrix = generate_feature_matrix(
        cash_flow_series=cf_series,
        awplr_series=exo_data["awplr"],
        repo_rate_series=exo_data["repo_rate"],
        best_fd_90d_series=exo_data["best_fd_rate_90d"],
        usd_lkr_series=exo_data["usd_lkr_mid"],
    )
    
    scaler = MinMaxScaler()
    scaled_features = scaler.fit_transform(feature_matrix)
    
    target_scaler = MinMaxScaler()
    scaled_targets = target_scaler.fit_transform(np.array(cf_series).reshape(-1, 1))
    
    X, y = [], []
    horizon_days = 30
    window_size = 60
    
    for i in range(len(scaled_features) - window_size - horizon_days):
        X.append(scaled_features[i:i+window_size])
        y.append(scaled_targets[i+window_size:i+window_size+horizon_days].flatten())
        
    X = np.array(X)
    y = np.array(y)
    
    print(f"Training data shape: X={X.shape}, y={y.shape}")
    
    model = build_model(horizon_days=horizon_days)
    
    print("Training model...")
    model.fit(X, y, epochs=10, batch_size=4, verbose=1)
    
    weights_dir = Path(__file__).parent / "weights"
    weights_dir.mkdir(exist_ok=True)
    
    model_path = weights_dir / "lstm_model.keras" # Using .keras extension to suppress warnings
    model.save(model_path)
    print(f"Model saved to {model_path}")
    
    joblib.dump(scaler, weights_dir / "feature_scaler.pkl")
    joblib.dump(target_scaler, weights_dir / "target_scaler.pkl")
    print("Scalers saved.")

if __name__ == "__main__":
    main()
