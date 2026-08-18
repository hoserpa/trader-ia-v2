"""Backtest realista del modelo fiel a la lógica del bot (TP/SL, trailing, parcial, horas máx, fees).

Reproduce la lógica de bot/trading/engine.py + risk_manager.py sobre datos históricos Kraken:
  - Señales evaluadas cada 30 min sobre la última vela 15m cerrada
  - Entrada long (BUY) / short (SELL) con confianza >= umbral
  - SL 2.5*ATR, TP 3*ATR, trailing (activación +0.8%, distancia 1*ATR)
  - Salida parcial 50% a 1.5R, force-close por horas máx, cierre por señal contraria
  - Fees maker 0.16% por transacción, max 3 trades/día, sizing basado en riesgo
"""
import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
from indicators.technical import calculate_indicators
from indicators.features import FeatureBuilder, PAIR_MAP
from feature_engineering import resample_to_htf, merge_htf_features, HTF_FEATURE_COLS

PAIRS = ["BTC/EUR", "ETH/EUR", "SOL/EUR"]
DATA_DIR = Path(__file__).parent / "output" / "data_kraken"

MAKER_FEE = 0.0016
ANALYSIS_INTERVAL = timedelta(minutes=30)


def load_model(model_dir: Path):
    model = joblib.load(model_dir / "trained_model.pkl")
    scaler = joblib.load(model_dir / "scaler.pkl")
    metadata = json.loads((model_dir / "model_metadata.json").read_text())
    return model, scaler, metadata.get("feature_cols", [])


def analyze_position(pos: dict, candle_row: pd.Series, atr: float, hours_open: float, signal: str, signal_conf: float, max_hours: float, close_conf: float):
    """Evalúa si una posición abierta debe cerrarse (intrabar con high/low).
    Retorna: (cerrar_todo, reason, close_price, pnl_pct)
    """
    entry = pos["entry"]
    ptype = pos["type"]
    sl = pos["sl"]
    tp = pos["tp"]
    high, low, close = candle_row["high"], candle_row["low"], candle_row["close"]

    if ptype == "long":
        if low <= sl:
            return True, "stop_loss", sl
        if high >= tp:
            return True, "take_profit", tp
        trail = pos.get("trail")
        if trail is not None and low <= trail:
            return True, "trailing_stop", trail
        pnl = (close - entry) / entry
    else:
        if high >= sl:
            return True, "stop_loss", sl
        if low <= tp:
            return True, "take_profit", tp
        trail = pos.get("trail")
        if trail is not None and high >= trail:
            return True, "trailing_stop", trail
        pnl = (entry - close) / entry

    if hours_open > max_hours:
        return True, "force_close", close
    if signal in ("BUY", "SELL") and signal_conf >= close_conf:
        if (ptype == "long" and signal == "SELL") or (ptype == "short" and signal == "BUY"):
            return True, "model_signal", close

    return False, None, None


def update_trailing(pos: dict, candle_row: pd.Series, atr: float):
    """Actualiza trailing stop como el engine (solo trasbar barra a barra)."""
    high, low, close = candle_row["high"], candle_row["low"], candle_row["close"]
    entry = pos["entry"]
    if pos["type"] == "long":
        pnl_pct = (close - entry) / entry
        if pnl_pct >= 0.008 and atr > 0:
            proposed = close - atr
            if pos.get("trail") is None or proposed > pos["trail"]:
                pos["trail"] = proposed
    else:
        pnl_pct = (entry - close) / entry
        if pnl_pct >= 0.008 and atr > 0:
            proposed = close + atr
            if pos.get("trail") is None or proposed < pos["trail"]:
                pos["trail"] = proposed


def should_take_partial(pos: dict, candle_row: pd.Series, atr: float):
    """Salida parcial 50% a 1.5R como el engine."""
    if pos.get("partial_done"):
        return False
    entry = pos["entry"]
    risk_per_unit = atr * 2.5
    if risk_per_unit <= 0:
        return False
    if pos["type"] == "long":
        r_multiple = (candle_row["close"] - entry) / risk_per_unit
    else:
        r_multiple = (entry - candle_row["close"]) / risk_per_unit
    return r_multiple >= 1.5


def compute_signal(model, scaler, feature_cols, builder, indicators_df, idx, pair, htf_cache=None):
    if idx < builder.MIN_ROWS:
        return None
    subset = indicators_df.iloc[:idx + 1]
    features = builder.build_features(subset, pair=pair)
    if features is None:
        return None

    if htf_cache is not None:
        ts = indicators_df["timestamp"].iloc[idx]
        for freq, prefix in [("1h", "h1_"), ("4h", "h4_")]:
            feat_htf, ts_htf = htf_cache[freq]
            htf_row = ts_htf[ts_htf <= ts]
            if len(htf_row) > 0:
                latest_idx = htf_row.index[-1]
                feat_dict = feat_htf.loc[latest_idx]
                for col in HTF_FEATURE_COLS:
                    features[f"{prefix}{col}"] = feat_dict.get(col, 0.0)
            else:
                for col in HTF_FEATURE_COLS:
                    features[f"{prefix}{col}"] = 0.0

    vector = np.array([features.get(name, 0.0) for name in feature_cols])
    probs = model.predict_proba(scaler.transform(vector.reshape(1, -1)))[0]
    classes = list(model.classes_)
    prob_dict = {int(c): float(p) for c, p in zip(classes, probs)}
    best_idx = int(np.argmax(probs))
    best_class = {0: "SELL", 1: "HOLD", 2: "BUY"}[best_idx]
    return best_class, probs[best_idx], prob_dict


def run_backtest(model, scaler, feature_cols, data: dict[str, pd.DataFrame], threshold: float, max_hours: float, max_daily: int, start_equity: float = 68.90, start: str = None, use_htf: bool = False):
    builder = FeatureBuilder()
    indicators = {p: calculate_indicators(df.copy()) for p, df in data.items()}

    htf_cache = {}
    if use_htf and any("h1_" in c for c in feature_cols):
        logger.info("Pre-computing HTF features (1h/4h)...")
        for pair, df in data.items():
            htf_cache[pair] = {}
            for freq in ["1h", "4h"]:
                df_htf = resample_to_htf(df, freq)
                df_htf = calculate_indicators(df_htf)
                feat_htf = builder.build_features_batch(df_htf, pair=pair)
                ts_htf = df_htf["timestamp"].iloc[feat_htf.index]
                htf_cache[pair][freq] = (feat_htf, ts_htf)
                logger.info(f"  {pair} {freq}: {len(feat_htf)} features")

    eq = start_equity
    trades = []
    positions = {}
    daily_entries = {}
    fees_total = 0.0
    equity_curve = []

    all_ts = sorted(set().union(*[set(df["timestamp"]) for df in indicators.values()]))
    start_ts = all_ts[0]
    analysis_ts = [t for t in all_ts if (t - start_ts).total_seconds() % 1800 == 0]
    if start:
        analysis_ts = [t for t in analysis_ts if t >= pd.Timestamp(start)]

    for ts in analysis_ts:
        equity_curve.append((ts, eq))
        for pair in PAIRS:
            if pair not in data:
                continue
            idx_arr = indicators[pair]["timestamp"].values
            pos_i = np.searchsorted(idx_arr, np.datetime64(ts), side="right") - 1
            if pos_i < 0 or pos_i >= len(indicators[pair]):
                continue
            row = indicators[pair].iloc[pos_i]
            atr = row.get("atr_14", 0.0) or 0.0

            sig = compute_signal(model, scaler, feature_cols, builder, indicators[pair], pos_i, pair, htf_cache=htf_cache.get(pair))
            if sig is None:
                continue
            signal, conf, _ = sig

            if pair in positions:
                pos = positions[pair]
                hours_open = (ts - pos["entry_ts"]).total_seconds() / 3600
                should_close, reason, close_price = analyze_position(
                    pos, row, atr, hours_open, signal, conf, max_hours=max_hours, close_conf=0.45
                )
                if should_close:
                    pnl = (pos["notional"] - pos["closed_notional"]) * (close_price / pos["entry"] - 1) * (1 if pos["type"] == "long" else -1)
                    fee = (pos["notional"] - pos["closed_notional"]) * MAKER_FEE
                    eq += pnl - fee
                    fees_total += fee
                    trades.append({
                        "pair": pair, "type": pos["type"], "entry": pos["entry"],
                        "exit": close_price, "entry_ts": str(pos["entry_ts"]), "exit_ts": str(ts),
                        "reason": reason, "pnl_eur": round(pnl - fee, 4), "notional": pos["notional"],
                    })
                    del positions[pair]
                    continue

                if should_take_partial(pos, row, atr):
                    partial_notional = pos["notional"] * 0.5
                    close_price = row["close"]
                    pnl = partial_notional * (close_price / pos["entry"] - 1) * (1 if pos["type"] == "long" else -1)
                    fee = partial_notional * MAKER_FEE
                    eq += pnl - fee
                    fees_total += fee
                    pos["closed_notional"] += partial_notional
                    pos["partial_done"] = True
                    pos["partial_price"] = close_price
                    trades.append({
                        "pair": pair, "type": pos["type"], "entry": pos["entry"],
                        "exit": close_price, "entry_ts": str(pos["entry_ts"]), "exit_ts": str(ts),
                        "reason": "partial_1.5R", "pnl_eur": round(pnl - fee, 4), "notional": round(partial_notional, 4),
                    })
                else:
                    update_trailing(pos, row, atr)
            else:
                if signal not in ("BUY", "SELL") or conf < threshold:
                    continue
                day = str(ts.date())
                if daily_entries.get(day, 0) >= max_daily:
                    continue
                avail = eq
                if avail * 0.95 < 5.0:
                    continue
                conf_mult = max(0.5, min(1.5, (conf - threshold) / (0.30 - threshold + 1e-10)))
                risk_amount = min(eq * 0.01 * conf_mult, eq * 0.03)
                stop_distance_pct = (atr / row["close"]) * 2.5 if atr > 0 and row["close"] > 0 else 0.01
                notional = min(risk_amount / stop_distance_pct, eq * 0.10, avail * 0.95)
                if notional < 5.0:
                    continue
                entry_price = row["close"]
                sl = entry_price - atr * 2.5 if signal == "BUY" else entry_price + atr * 2.5
                tp = entry_price + atr * 3.0 if signal == "BUY" else entry_price - atr * 3.0
                fee = notional * MAKER_FEE
                eq -= fee
                fees_total += fee
                daily_entries[day] = daily_entries.get(day, 0) + 1
                positions[pair] = {
                    "type": "long" if signal == "BUY" else "short",
                    "entry": entry_price, "sl": sl, "tp": tp, "notional": notional,
                    "closed_notional": 0.0, "entry_ts": ts, "trail": None, "partial_done": False,
                }

    for pair, pos in positions.items():
        eq -= (pos["notional"] - pos["closed_notional"]) * MAKER_FEE
        fees_total += (pos["notional"] - pos["closed_notional"]) * MAKER_FEE

    df_trades = pd.DataFrame(trades)
    stats = {
        "n_trades": len(df_trades),
        "total_pnl_eur": round(eq - start_equity, 4),
        "final_equity": round(eq, 4),
        "fees_eur": round(fees_total, 4),
        "max_drawdown_eur": 0.0,
    }
    if len(df_trades) > 0:
        wins = df_trades[df_trades["pnl_eur"] > 0]
        losses = df_trades[df_trades["pnl_eur"] <= 0]
        stats["win_rate"] = round(len(wins) / len(df_trades), 4)
        stats["avg_win"] = round(wins["pnl_eur"].mean(), 4) if len(wins) else 0.0
        stats["avg_loss"] = round(losses["pnl_eur"].mean(), 4) if len(losses) else 0.0
        stats["total_wins_eur"] = round(wins["pnl_eur"].sum(), 4)
        stats["total_losses_eur"] = round(losses["pnl_eur"].sum(), 4)
        stats["open_positions_at_end"] = len(positions)
        eq_arr = np.array([e for _, e in equity_curve])
        peak = np.maximum.accumulate(eq_arr)
        stats["max_drawdown_eur"] = round(float(np.max(peak - eq_arr)), 4)
        reasons = df_trades["reason"].value_counts().to_dict()
        stats["reasons"] = {k: int(v) for k, v in reasons.items()}
        by_type = df_trades.groupby("type")["pnl_eur"].agg(["count", "sum"]).round(4)
        stats["by_type"] = by_type.to_dict("index")
    return stats, df_trades, equity_curve


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="training/output/model_kraken")
    parser.add_argument("--data", type=str, default="training/output/data_kraken")
    parser.add_argument("--threshold", type=float, default=0.45)
    parser.add_argument("--max-hours", type=float, default=8.0)
    parser.add_argument("--max-daily", type=int, default=3)
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default="2026-08-10 14:00:00")
    parser.add_argument("--htf", action="store_true", default=True, help="Usar features multi-timeframe")
    parser.add_argument("--no-htf", dest="htf", action="store_false")
    args = parser.parse_args()

    model_dir = Path(args.model)
    data_dir = Path(args.data)
    model, scaler, feature_cols = load_model(model_dir)

    data = {}
    for pair in PAIRS:
        f = data_dir / f"{pair.replace('/', '-')}_15m.parquet"
        if not f.exists():
            logger.warning(f"Faltan datos para {pair}")
            continue
        df = pd.read_parquet(f)
        df = df[df["timestamp"] <= pd.Timestamp(args.end)].reset_index(drop=True)
        data[pair] = df
        logger.info(f"{pair}: {len(df)} velas ({df['timestamp'].min()} .. {df['timestamp'].max()})")

    stats, trades, curve = run_backtest(
        model, scaler, feature_cols, data,
        threshold=args.threshold, max_hours=args.max_hours, max_daily=args.max_daily,
        start=args.start, use_htf=args.htf,
    )
    logger.info(f"=== BACKTEST th={args.threshold} max_hours={args.max_hours} ===")
    for k, v in stats.items():
        logger.info(f"  {k}: {v}")

    out = model_dir / "backtest_results.json"
    with open(out, "w") as f:
        json.dump(stats, f, indent=2)
    logger.info(f"Guardado en {out}")


if __name__ == "__main__":
    main()
