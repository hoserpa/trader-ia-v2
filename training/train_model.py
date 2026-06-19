"""Entrenamiento del modelo LightGBM con walk-forward, Optuna y oversampling.

Usage:
    python train_model.py --data output/features/features_with_labels.parquet --output output/model
"""
import argparse
from datetime import datetime
from pathlib import Path
import json
import warnings
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import RobustScaler
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    classification_report, confusion_matrix
)
from sklearn.metrics import roc_auc_score
from sklearn.utils import resample
import joblib
from loguru import logger

warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")

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


def prepare_features(train_df, val_df, test_df, label_col: str = "label"):
    """Normaliza features con RobustScaler."""
    X_train = train_df[FEATURE_COLS].values
    X_val = val_df[FEATURE_COLS].values
    X_test = test_df[FEATURE_COLS].values

    scaler = RobustScaler()
    X_train = scaler.fit_transform(X_train)
    X_val = scaler.transform(X_val)
    X_test = scaler.transform(X_test)

    y_train = train_df[label_col].astype(int).values
    y_val = val_df[label_col].astype(int).values
    y_test = test_df[label_col].astype(int).values

    return X_train, X_val, X_test, y_train, y_val, y_test, scaler


def oversample_minority(X: np.ndarray, y: np.ndarray, strength: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Oversamplea BUY (2) y SELL (0).
    strength=1.0: upsample a igual que HOLD (~33% cada una).
    strength=0.5: upsample a 50% del tamaño de HOLD.
    strength=0.0: sin oversampling.
    """
    if strength <= 0:
        return X, y

    df = pd.DataFrame(X)
    df["_label"] = y

    hold = df[df["_label"] == 1]
    buy = df[df["_label"] == 2]
    sell = df[df["_label"] == 0]

    target_size = int(max(len(hold), len(buy), len(sell)) * strength)
    target_size = max(target_size, 1)

    if len(buy) > 0:
        n_buy = min(target_size, len(buy) * 5)
        buy_oversampled = resample(buy, replace=True, n_samples=n_buy, random_state=42)
    else:
        buy_oversampled = buy

    if len(sell) > 0:
        n_sell = min(target_size, len(sell) * 5)
        sell_oversampled = resample(sell, replace=True, n_samples=n_sell, random_state=42)
    else:
        sell_oversampled = sell

    hold_target = min(len(hold), target_size * 2)
    hold_undersampled = resample(hold, replace=False, n_samples=hold_target, random_state=42)

    balanced = pd.concat([buy_oversampled, sell_oversampled, hold_undersampled], ignore_index=True)
    balanced = balanced.sample(frac=1, random_state=42).reset_index(drop=True)

    X_balanced = balanced.drop(columns=["_label"]).values
    y_balanced = balanced["_label"].values.astype(int)

    logger.info(f"Oversampling (strength={strength}): {len(y)} -> {len(y_balanced)} (BUY={sum(y_balanced==2)}, SELL={sum(y_balanced==0)}, HOLD={sum(y_balanced==1)})")
    return X_balanced, y_balanced


def train_lightgbm(X_train, y_train, X_val, y_val):
    """Entrena modelo LightGBM multiclase."""
    from lightgbm import LGBMClassifier

    model = LGBMClassifier(
        objective="multiclass",
        num_class=3,
        n_estimators=1000,
        max_depth=5,
        num_leaves=31,
        learning_rate=0.03,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.01,
        reg_lambda=0.01,
        n_jobs=2,
        verbose=-1,
        random_state=42,
        class_weight="balanced",
    )

    logger.info("Entrenando LightGBM con early stopping...")
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(stopping_rounds=50), lgb.log_evaluation(period=100)]
    )

    return model


def optimize_with_optuna(X_train, y_train, X_val, y_val, n_trials: int = 30):
    """Optimiza hiperparámetros con Optuna."""
    try:
        import optuna
    except ImportError:
        logger.warning("Optuna no instalado. Usando hiperparámetros por defecto.")
        return train_lightgbm(X_train, y_train, X_val, y_val)

    def objective(trial):
        params = {
            "objective": "multiclass",
            "num_class": 3,
            "n_estimators": trial.suggest_int("n_estimators", 300, 1500),
            "max_depth": trial.suggest_int("max_depth", 3, 8),
            "num_leaves": trial.suggest_int("num_leaves", 15, 63),
            "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.1, log=True),
            "min_child_samples": trial.suggest_int("min_child_samples", 10, 50),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
            "reg_alpha": trial.suggest_float("reg_alpha", 1e-4, 10.0, log=True),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-4, 10.0, log=True),
            "n_jobs": 2,
            "verbose": -1,
            "random_state": 42,
            "class_weight": "balanced",
        }

        model = lgb.LGBMClassifier(**params)
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            callbacks=[lgb.early_stopping(stopping_rounds=30), lgb.log_evaluation(period=0)]
        )

        proba = model.predict_proba(X_val)
        predictions = []
        for probs in proba:
            max_idx = np.argmax(probs)
            max_prob = probs[max_idx]
            if max_prob >= 0.60:
                predictions.append(max_idx)
            else:
                predictions.append(1)

        predictions = np.array(predictions)

        prec_buy = precision_score(y_val, predictions, labels=[2], average=None, zero_division=0)[0]
        prec_sell = precision_score(y_val, predictions, labels=[0], average=None, zero_division=0)[0]
        avg_precision = (prec_buy + prec_sell) / 2

        return avg_precision

    logger.info(f"Optimizando hiperparámetros con Optuna ({n_trials} trials)...")
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    best_params = study.best_params
    logger.info(f"Mejores hiperparámetros: {best_params}")
    logger.info(f"Mejor precision promedio BUY/SELL: {study.best_value:.4f}")

    best_params["n_jobs"] = 2
    best_params["verbose"] = -1
    best_params["random_state"] = 42
    best_params["class_weight"] = "balanced"
    best_params["objective"] = "multiclass"
    best_params["num_class"] = 3

    model = lgb.LGBMClassifier(**best_params)
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(stopping_rounds=50), lgb.log_evaluation(period=100)]
    )

    return model


def walk_forward_validation(df: pd.DataFrame, n_splits: int = 4, label_col: str = "label", oversample_strength: float = 1.0):
    """Walk-forward cross-validation para evaluar estabilidad temporal.
    Entrena en datos pasados, evalúa en futuros, avanza la ventana.
    """
    df = df.sort_values("timestamp").reset_index(drop=True)
    tscv = TimeSeriesSplit(n_splits=n_splits)

    fold_metrics = []
    for fold, (train_idx, val_idx) in enumerate(tscv.split(df)):
        train_df = df.iloc[train_idx].copy()
        val_df = df.iloc[val_idx].copy()

        train_start = train_df["timestamp"].iloc[0]
        train_end = train_df["timestamp"].iloc[-1]
        val_start = val_df["timestamp"].iloc[0]
        val_end = val_df["timestamp"].iloc[-1]

        logger.info(f"Fold {fold+1}: train={str(train_start)[:19]}..{str(train_end)[:19]} ({len(train_df)}), val={str(val_start)[:19]}..{str(val_end)[:19]} ({len(val_df)})")

        X_train = train_df[FEATURE_COLS].values
        y_train = train_df[label_col].astype(int).values
        X_val = val_df[FEATURE_COLS].values
        y_val = val_df[label_col].astype(int).values

        scaler = RobustScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_val_scaled = scaler.transform(X_val)

        X_train_bal, y_train_bal = oversample_minority(X_train_scaled, y_train, strength=oversample_strength)

        model = train_lightgbm(X_train_bal, y_train_bal, X_val_scaled, y_val)

        proba = model.predict_proba(X_val_scaled)
        predictions = np.argmax(proba, axis=1)

        metrics = {
            "fold": fold + 1,
            "train_size": len(train_df),
            "val_size": len(val_df),
            "accuracy": accuracy_score(y_val, predictions),
            "precision_buy": precision_score(y_val, predictions, labels=[2], average=None, zero_division=0)[0],
            "precision_sell": precision_score(y_val, predictions, labels=[0], average=None, zero_division=0)[0],
            "recall_buy": recall_score(y_val, predictions, labels=[2], average=None, zero_division=0)[0],
            "recall_sell": recall_score(y_val, predictions, labels=[0], average=None, zero_division=0)[0],
            "f1_macro": f1_score(y_val, predictions, average="macro", zero_division=0),
        }

        try:
            metrics["auc_ovr"] = roc_auc_score(y_val, proba, multi_class="ovr")
        except Exception:
            metrics["auc_ovr"] = 0.0

        fold_metrics.append(metrics)
        logger.info(f"  Fold {fold+1} metrics: precision_buy={metrics['precision_buy']:.4f}, precision_sell={metrics['precision_sell']:.4f}, f1_macro={metrics['f1_macro']:.4f}")

    avg_metrics = {}
    for key in fold_metrics[0]:
        if key not in ("fold", "train_size", "val_size"):
            avg_metrics[f"avg_{key}"] = np.mean([m[key] for m in fold_metrics])
            avg_metrics[f"std_{key}"] = np.std([m[key] for m in fold_metrics])

    logger.info("Walk-forward results (avg ± std):")
    for k, v in avg_metrics.items():
        logger.info(f"  {k}: {v:.4f}")

    return fold_metrics, avg_metrics


def evaluate_model(model, X, y, threshold: float = 0.60):
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
    parser = argparse.ArgumentParser(description="Entrena modelo LightGBM con walk-forward + Optuna")
    parser.add_argument("--data", type=str, default="output/features/features_with_labels.parquet")
    parser.add_argument("--output", type=str, default="output/model")
    parser.add_argument("--test-size", type=float, default=0.15)
    parser.add_argument("--val-size", type=float, default=0.15)
    parser.add_argument("--n-trials", type=int, default=30, help="Trials de Optuna")
    parser.add_argument("--label", type=str, default="label", help="Columna de label (label, label_3, label_6, label_12)")
    parser.add_argument("--oversample-strength", type=float, default=1.0, help="Fuerza de oversampling (0.0=no oversampling, 1.0=balance completo)")
    parser.add_argument("--walk-forward", action="store_true", help="Ejecutar walk-forward validation")
    parser.add_argument("--no-optuna", action="store_true", help="Saltar optimización Optuna")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Cargando datos de {args.data}")
    df = pd.read_parquet(args.data)

    label_col = args.label if args.label in df.columns else [c for c in df.columns if c.startswith("label_")][0]

    label_cols = [c for c in df.columns if c.startswith("label_")]
    logger.info(f"Total muestras: {len(df)}")
    logger.info(f"Labels disponibles: {['label'] + label_cols}")
    logger.info(f"Usando columna label: {label_col}")
    label_counts = df[label_col].value_counts().sort_index()
    for label_val, count in label_counts.items():
        pct = count / len(df) * 100
        name = {0: "SELL", 1: "HOLD", 2: "BUY"}.get(label_val, "UNK")
        logger.info(f"  {name}: {count} ({pct:.1f}%)")

    if args.walk_forward:
        logger.info("\n=== WALK-FORWARD VALIDATION ===")
        fold_metrics, avg_metrics = walk_forward_validation(df, n_splits=4, label_col=label_col, oversample_strength=args.oversample_strength)

        wf_path = output_dir / "walk_forward_metrics.json"
        with open(wf_path, "w") as f:
            json.dump({"folds": fold_metrics, "average": avg_metrics}, f, indent=2)
        logger.info(f"Walk-forward metrics guardadas en {wf_path}")

    train_df, val_df, test_df = load_and_split_data(df, args.test_size, args.val_size)

    X_train, X_val, X_test, y_train, y_val, y_test, scaler = prepare_features(train_df, val_df, test_df, label_col=label_col)

    X_train_bal, y_train_bal = oversample_minority(X_train, y_train, strength=args.oversample_strength)

    if args.no_optuna:
        model = train_lightgbm(X_train_bal, y_train_bal, X_val, y_val)
    else:
        model = optimize_with_optuna(X_train_bal, y_train_bal, X_val, y_val, n_trials=args.n_trials)

    logger.info("Evaluando en validation set...")
    val_metrics, _ = evaluate_model(model, X_val, y_val)

    logger.info("Métricas en validation set:")
    for k, v in val_metrics.items():
        logger.info(f"  {k}: {v:.4f}")

    logger.info("Evaluando en test set...")
    test_metrics, _ = evaluate_model(model, X_test, y_test)

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
        "n_samples_after_oversampling": len(X_train_bal),
        "feature_cols": FEATURE_COLS,
        "confidence_threshold": 0.60,
        "label_col": label_col,
        "oversample_strength": args.oversample_strength,
        "validation_metrics": val_metrics,
        "test_metrics": test_metrics,
        "lgb_params": model.get_params(),
        "optuna_trials": None if args.no_optuna else args.n_trials,
    }

    if args.walk_forward:
        metadata["walk_forward"] = avg_metrics

    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info(f"Modelo guardado en {output_dir}")
    logger.info(f"  - {model_path}")
    logger.info(f"  - {scaler_path}")
    logger.info(f"  - {metadata_path}")


if __name__ == "__main__":
    logger.add("logs/train_model.log", rotation="50 MB", level="INFO")
    main()
