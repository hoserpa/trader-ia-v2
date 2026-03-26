"""Entrenamiento del modelo LightGBM.

Usage:
    python train_model.py --data output/features/features_with_labels.parquet --output output/model
"""
import argparse
from datetime import datetime
from pathlib import Path
import json
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import RobustScaler
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    classification_report, confusion_matrix
)
from sklearn.metrics import roc_auc_score
import joblib
from loguru import logger

Path("logs").mkdir(exist_ok=True)


FEATURE_COLS = [
    "price_change_1", "price_change_3", "price_change_6",
    "rsi_14", "rsi_slope",
    "macd", "macd_signal", "macd_hist", "macd_cross",
    "bb_pct_b", "bb_bandwidth",
    "atr_pct",
    "price_vs_ema9", "price_vs_ema21", "price_vs_sma50", "ema9_vs_ema21",
    "volume_change", "volume_ratio", "obv_slope",
    "stoch_k", "stoch_d", "williams_r", "cci_20",
    "hour_sin", "hour_cos", "day_of_week", "is_weekend",
    "volatility_regime"
]

LABEL_MAP = {0: "SELL", 1: "HOLD", 2: "BUY"}
LABEL_COL = "label"


def load_and_split_data(df: pd.DataFrame, test_size: float = 0.15, val_size: float = 0.15):
    """Split temporal: 70% train, 15% val, 15% test (sin shuffle)."""
    df = df.sort_values("timestamp").reset_index(drop=True)
    
    n = len(df)
    train_end = int(n * 0.70)
    val_end = int(n * (0.70 + val_size))
    
    train_df = df.iloc[:train_end].copy()
    val_df = df.iloc[train_end:val_end].copy()
    test_df = df.iloc[val_end:].copy()
    
    logger.info(f"Train: {len(train_df)}, Val: {len(val_df)}, Test: {len(test_df)}")
    return train_df, val_df, test_df


def prepare_features(train_df, val_df, test_df):
    """Normaliza features con RobustScaler."""
    X_train = train_df[FEATURE_COLS].values
    X_val = val_df[FEATURE_COLS].values
    X_test = test_df[FEATURE_COLS].values
    
    scaler = RobustScaler()
    X_train = scaler.fit_transform(X_train)
    X_val = scaler.transform(X_val)
    X_test = scaler.transform(X_test)
    
    y_train = train_df[LABEL_COL].astype(int).values
    y_val = val_df[LABEL_COL].astype(int).values
    y_test = test_df[LABEL_COL].astype(int).values
    
    return X_train, X_val, X_test, y_train, y_val, y_test, scaler


def optimize_threshold(model, X_val, y_val, confidence_thresholds: list):
    """Optimiza el umbral de confianza para maximizar precision."""
    best_threshold = 0.70
    best_precision = 0
    
    proba = model.predict_proba(X_val)
    
    for threshold in confidence_thresholds:
        predictions = []
        for probs in proba:
            max_idx = np.argmax(probs)
            max_prob = probs[max_idx]
            if max_prob >= threshold:
                predictions.append(max_idx)
            else:
                predictions.append(1)
        
        predictions = np.array(predictions)
        
        prec_buy = precision_score(y_val, predictions, labels=[2], average=None, zero_division=0)
        prec_sell = precision_score(y_val, predictions, labels=[0], average=None, zero_division=0)
        
        avg_precision = (prec_buy[0] + prec_sell[0]) / 2 if len(prec_buy) > 0 else 0
        
        if avg_precision > best_precision:
            best_precision = avg_precision
            best_threshold = threshold
    
    logger.info(f"Mejor umbral: {best_threshold} (precision: {best_precision:.3f})")
    return best_threshold


def train_lightgbm(X_train, y_train, X_val, y_val):
    """Entrena modelo LightGBM multiclase con sklearn wrapper para predict_proba."""
    from lightgbm import LGBMClassifier
    
    class_counts = np.bincount(y_train)
    class_weights = len(y_train) / (len(class_counts) * class_counts)
    
    model = LGBMClassifier(
        objective="multiclass",
        num_class=3,
        n_estimators=500,
        max_depth=6,
        num_leaves=31,
        learning_rate=0.05,
        n_jobs=2,
        verbose=-1,
        random_state=42,
        class_weight="balanced",
    )
    
    logger.info("Entrenando LightGBM (sklearn wrapper)...")
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(stopping_rounds=50), lgb.log_evaluation(period=50)]
    )
    
    return model


def evaluate_model(model, X, y, threshold: float):
    """Evalúa modelo con threshold personalizado."""
    proba = model.predict_proba(X)
    predictions = []
    
    for probs in proba:
        max_idx = np.argmax(probs)
        max_prob = probs[max_idx]
        if max_prob >= threshold:
            predictions.append(max_idx)
        else:
            predictions.append(1)
    
    predictions = np.array(predictions)
    
    metrics = {
        "accuracy": accuracy_score(y, predictions),
        "precision_buy": precision_score(y, predictions, labels=[2], average=None, zero_division=0)[0] if 2 in y else 0,
        "precision_sell": precision_score(y, predictions, labels=[0], average=None, zero_division=0)[0] if 0 in y else 0,
        "recall_buy": recall_score(y, predictions, labels=[2], average=None, zero_division=0)[0] if 2 in y else 0,
        "recall_sell": recall_score(y, predictions, labels=[0], average=None, zero_division=0)[0] if 0 in y else 0,
        "f1_macro": f1_score(y, predictions, average="macro", zero_division=0),
    }
    
    try:
        metrics["auc_ovr"] = roc_auc_score(y, proba, multi_class="ovr")
    except:
        metrics["auc_ovr"] = 0.0
    
    return metrics, predictions


def main():
    parser = argparse.ArgumentParser(description="Entrena modelo LightGBM")
    parser.add_argument("--data", type=str, default="output/features/features_with_labels.parquet")
    parser.add_argument("--output", type=str, default="output/model")
    parser.add_argument("--test-size", type=float, default=0.15)
    parser.add_argument("--val-size", type=float, default=0.15)
    args = parser.parse_args()
    
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Cargando datos de {args.data}")
    df = pd.read_parquet(args.data)
    logger.info(f"Total muestras: {len(df)}")
    
    train_df, val_df, test_df = load_and_split_data(df, args.test_size, args.val_size)
    
    X_train, X_val, X_test, y_train, y_val, y_test, scaler = prepare_features(train_df, val_df, test_df)
    
    model = train_lightgbm(X_train, y_train, X_val, y_val)
    
    thresholds = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
    best_threshold = optimize_threshold(model, X_val, y_val, thresholds)
    
    logger.info("Evaluando en test set...")
    test_metrics, _ = evaluate_model(model, X_test, y_test, best_threshold)
    
    logger.info("Métricas en test set:")
    for k, v in test_metrics.items():
        logger.info(f"  {k}: {v:.4f}")
    
    logger.info("\nClassification Report (test):")
    proba_test = model.predict_proba(X_test)
    predictions_test = np.argmax(proba_test, axis=1)
    logger.info("\n" + classification_report(y_test, predictions_test, target_names=["SELL", "HOLD", "BUY"]))
    
    model_path = output_dir / "trained_model.pkl"
    scaler_path = output_dir / "scaler.pkl"
    metadata_path = output_dir / "model_metadata.json"
    
    joblib.dump(model, model_path)
    joblib.dump(scaler, scaler_path)
    
    metadata = {
        "trained_at": datetime.utcnow().isoformat(),
        "n_samples_train": len(train_df),
        "n_samples_val": len(val_df),
        "n_samples_test": len(test_df),
        "feature_cols": FEATURE_COLS,
        "confidence_threshold": best_threshold,
        "test_metrics": test_metrics,
        "lgb_params": model.get_params()
    }
    
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)
    
    logger.info(f"Modelo guardado en {output_dir}")
    logger.info(f"  - {model_path}")
    logger.info(f"  - {scaler_path}")
    logger.info(f"  - {metadata_path}")


if __name__ == "__main__":
    logger.add("logs/train_model.log", rotation="50 MB", level="INFO")
    main()
