"""Evaluación del modelo: métricas, curvas y backtest con vectorbt.

Usage:
    python evaluate_model.py --model output/model/trained_model.pkl --data output/features/features_with_labels.parquet
"""
import argparse
from pathlib import Path
import json
import numpy as np
import pandas as pd
import joblib
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    classification_report, confusion_matrix, roc_curve, auc
)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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


def calculate_sharpe(returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    """Calcula Sharpe ratio usando retornos logarítmicos."""
    if returns.empty or returns.std() == 0:
        return 0.0
    log_returns = np.log(1 + returns)
    if log_returns.std() == 0:
        return 0.0
    return np.sqrt(252) * log_returns.mean() / log_returns.std()


def calculate_max_drawdown(equity: pd.Series) -> float:
    """Calcula máximo drawdown."""
    if equity.empty:
        return 0.0
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax
    return -drawdown.min() if drawdown.min() < 0 else 0.0


def run_backtest(df: pd.DataFrame, model, scaler, buy_threshold: float, sell_threshold: float, initial_cash: float = 10000):
    """Ejecuta backtest manual sin vectorbt."""
    
    X = df[FEATURE_COLS].values
    X = scaler.transform(X)
    
    proba = model.predict(X)
    signals = []
    
    position_open = False
    entry_price = 0
    
    for probs in proba:
        max_idx = np.argmax(probs)
        max_prob = probs[max_idx]
        
        if max_idx == 2 and max_prob >= buy_threshold and not position_open:
            signals.append(1)
            position_open = True
        elif max_idx == 0 and max_prob >= sell_threshold and position_open:
            signals.append(-1)
            position_open = False
        else:
            signals.append(0)
    
    signals = pd.Series(signals, index=df.index)
    close = df["close"].values
    
    logger.info(f"Distribución de señales: BUY={np.sum(signals==1)}, SELL={np.sum(signals==-1)}, HOLD={np.sum(signals==0)}")
    
    cash = initial_cash
    position = 0
    entry_price = 0
    trades = []
    equity_curve = [initial_cash]
    
    fee_rate = 0.001
    stop_loss = 0.015
    take_profit = 0.03
    position_size = 0.03
    
    for i in range(len(signals)):
        sig = signals.iloc[i]
        price = close[i]
        
        if sig == 1 and position == 0:
            position = (cash * position_size) / price
            entry_price = price
            cash = cash - (position * price)
            
        elif sig == -1 and position > 0:
            proceeds = position * price
            proceeds *= (1 - fee_rate)
            pnl = proceeds - (position * entry_price)
            cash = cash + proceeds
            trades.append(pnl)
            position = 0
            entry_price = 0
        
        if position > 0 and entry_price > 0:
            pnl_pct = (price - entry_price) / entry_price
            if pnl_pct <= -stop_loss:
                proceeds = position * price * (1 - fee_rate)
                pnl = proceeds - (position * entry_price)
                cash = cash + proceeds
                trades.append(pnl)
                position = 0
                entry_price = 0
            elif pnl_pct >= take_profit:
                proceeds = position * price * (1 - fee_rate)
                pnl = proceeds - (position * entry_price)
                cash = cash + proceeds
                trades.append(pnl)
                position = 0
                entry_price = 0
        
        equity = cash + position * price
        equity_curve.append(equity)
    
    if position > 0:
        proceeds = position * close[-1] * (1 - fee_rate)
        pnl = proceeds - (position * entry_price)
        cash = cash + proceeds
        trades.append(pnl)
    
    final_equity = cash + position * close[-1]
    total_return = (final_equity - initial_cash) / initial_cash
    
    equity_series = pd.Series(equity_curve)
    returns = equity_series.pct_change().dropna()
    
    logger.info(f"Equity min: {equity_series.min():.2f}, max: {equity_series.max():.2f}")
    logger.info(f"Equity first 5: {equity_series.head().tolist()}")
    logger.info(f"Equity last 5: {equity_series.tail().tolist()}")
    
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    win_rate = len(wins) / len(trades) if trades else 0
    
    trade_returns = []
    for t in trades:
        trade_returns.append(t / initial_cash)
    trade_returns = pd.Series(trade_returns)
    
    logger.info(f"Total trades: {len(trades)}, Wins: {len(wins)}, Losses: {len(losses)}")
    
    stats = {
        "total_return": total_return,
        "sharpe_ratio": calculate_sharpe(trade_returns) if len(trade_returns) > 1 else 0,
        "max_drawdown": calculate_max_drawdown(equity_series),
        "win_rate": win_rate,
        "total_trades": len(trades),
        "avg_trade": np.mean(trades) if trades else 0,
    }
    
    logger.info(f"Final equity: {final_equity:.2f}, Trades: {len(trades)}")
    
    return stats, signals


def plot_confusion_matrix(y_true, y_pred, output_path: Path):
    """Guarda matriz de confusión."""
    cm = confusion_matrix(y_true, y_pred)
    
    fig, ax = plt.subplots(figsize=(8, 6))
    im = ax.imshow(cm, interpolation="nearest", cmap=plt.cm.Blues)
    ax.figure.colorbar(im, ax=ax)
    
    ax.set(xticks=np.arange(cm.shape[1]),
           yticks=np.arange(cm.shape[0]),
           xticklabels=["SELL", "HOLD", "BUY"],
           yticklabels=["SELL", "HOLD", "BUY"],
           xlabel="Predicho",
           ylabel="Real")
    
    thresh = cm.max() / 2.0
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            ax.text(j, i, format(cm[i, j], "d"),
                   ha="center", va="center",
                   color="white" if cm[i, j] > thresh else "black")
    
    plt.title("Matriz de Confusión")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    logger.info(f"Matriz de confusión guardada: {output_path}")


def plot_roc_curves(y_true, proba, output_path: Path):
    """Guarda curvas ROC."""
    fig, ax = plt.subplots(figsize=(8, 6))
    
    for i, label in enumerate(["SELL", "HOLD", "BUY"]):
        y_binary = (y_true == i).astype(int)
        if len(np.unique(y_binary)) < 2:
            continue
        fpr, tpr, _ = roc_curve(y_binary, proba[:, i])
        roc_auc = auc(fpr, tpr)
        ax.plot(fpr, tpr, label=f"{label} (AUC = {roc_auc:.3f})")
    
    ax.plot([0, 1], [0, 1], "k--", label="Random")
    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate")
    ax.set_title("Curvas ROC")
    ax.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    logger.info(f"Curvas ROC guardadas: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Evalúa modelo con backtest")
    parser.add_argument("--model", type=str, default="output/model/trained_model.pkl")
    parser.add_argument("--scaler", type=str, default="output/model/scaler.pkl")
    parser.add_argument("--metadata", type=str, default="output/model/model_metadata.json")
    parser.add_argument("--data", type=str, default="output/features/features_with_labels.parquet")
    parser.add_argument("--output", type=str, default="output/evaluation")
    parser.add_argument("--initial-cash", type=float, default=10000)
    parser.add_argument("--threshold", type=float, default=None, help="Confidence threshold for all classes (default: from metadata or 0.60)")
    parser.add_argument("--buy-threshold", type=float, default=0.40, help="Threshold for BUY signals")
    parser.add_argument("--sell-threshold", type=float, default=0.40, help="Threshold for SELL signals")
    args = parser.parse_args()
    
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Cargando modelo: {args.model}")
    model = joblib.load(args.model)
    scaler = joblib.load(args.scaler)
    
    with open(args.metadata) as f:
        metadata = json.load(f)
    buy_threshold = args.buy_threshold if args.threshold is None else args.threshold
    sell_threshold = args.sell_threshold if args.threshold is None else args.threshold
    logger.info(f"Thresholds: BUY>={buy_threshold}, SELL>={sell_threshold}")
    
    logger.info(f"Cargando datos: {args.data}")
    df = pd.read_parquet(args.data)
    df = df.sort_values("timestamp").reset_index(drop=True)
    
    logger.info(f"Datos - close min: {df['close'].min():.4f}, max: {df['close'].max():.4f}, mean: {df['close'].mean():.4f}")
    
    X = df[FEATURE_COLS].values
    X = scaler.transform(X)
    y = df["label"].astype(int).values
    
    proba = model.predict(X)
    predictions = np.argmax(proba, axis=1)
    
    test_metrics = {
        "accuracy": accuracy_score(y, predictions),
        "precision_macro": precision_score(y, predictions, average="macro", zero_division=0),
        "recall_macro": recall_score(y, predictions, average="macro", zero_division=0),
        "f1_macro": f1_score(y, predictions, average="macro", zero_division=0),
        "precision_buy": precision_score(y, predictions, labels=[2], average=None, zero_division=0)[0],
        "precision_sell": precision_score(y, predictions, labels=[0], average=None, zero_division=0)[0],
    }
    
    logger.info("Métricas de clasificación:")
    for k, v in test_metrics.items():
        logger.info(f"  {k}: {v:.4f}")
    
    logger.info("\n" + classification_report(y, predictions, target_names=["SELL", "HOLD", "BUY"]))
    
    plot_confusion_matrix(y, predictions, output_dir / "confusion_matrix.png")
    plot_roc_curves(y, proba, output_dir / "roc_curves.png")
    
    logger.info("Ejecutando backtest...")
    bt_stats, signals = run_backtest(df, model, scaler, buy_threshold, sell_threshold, args.initial_cash)
    
    logger.info("Estadísticas del backtest:")
    for k, v in bt_stats.items():
        logger.info(f"  {k}: {v:.4f}")
    
    all_metrics = {
        "classification_metrics": test_metrics,
        "backtest_stats": bt_stats,
        "buy_threshold": buy_threshold,
        "sell_threshold": sell_threshold,
        "initial_cash": args.initial_cash
    }
    
    with open(output_dir / "evaluation_results.json", "w") as f:
        json.dump(all_metrics, f, indent=2)
    
    logger.info(f"Evaluación completada. Resultados en {output_dir}")
    
    MIN_PRECISION = 0.60
    MIN_SHARPE = 1.0
    MAX_DRAWDOWN = 0.15
    
    logger.info("\n=== VALIDACIÓN CONTRA MÍNIMOS DEL SPEC ===")
    logger.info(f"  Precision BUY >= {MIN_PRECISION}: {'✓' if test_metrics['precision_buy'] >= MIN_PRECISION else '✗'} ({test_metrics['precision_buy']:.3f})")
    logger.info(f"  Precision SELL >= {MIN_PRECISION}: {'✓' if test_metrics['precision_sell'] >= MIN_PRECISION else '✗'} ({test_metrics['precision_sell']:.3f})")
    logger.info(f"  Sharpe >= {MIN_SHARPE}: {'✓' if bt_stats['sharpe_ratio'] >= MIN_SHARPE else '✗'} ({bt_stats['sharpe_ratio']:.3f})")
    logger.info(f"  Max Drawdown <= {MAX_DRAWDOWN*100}%: {'✓' if bt_stats['max_drawdown'] <= MAX_DRAWDOWN else '✗'} ({bt_stats['max_drawdown']*100:.1f}%)")


if __name__ == "__main__":
    logger.add("logs/evaluate_model.log", rotation="50 MB", level="INFO")
    main()
