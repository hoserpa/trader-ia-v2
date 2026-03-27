"""Endpoint para ejecutar simulacion de trading optimizada."""
import asyncio
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
import warnings

from bot.config import config
from bot.database.crud import upsert_candles, get_candle_count
from bot.database.init_db import init_db, SessionLocal
from bot.database.models import Candle
from bot.indicators.technical import calculate_indicators, get_atr
from bot.indicators.features import FeatureBuilder
from bot.model.predictor import ModelPredictor
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

router = APIRouter()


async def fetch_historical_data(pair: str, days: int = 90) -> int:
    import ccxt.async_support as ccxt
    
    exchange_id = config.exchange.name.lower()
    exchange = getattr(ccxt, exchange_id)({
        "apiKey": config.exchange.api_key,
        "secret": config.exchange.api_secret,
        "enableRateLimit": True,
        "timeout": 30000,
    })

    since = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    timeframe = config.trading.timeframe
    all_candles = []
    limit = 300

    try:
        await exchange.load_markets()
        while True:
            try:
                ohlcv = await exchange.fetch_ohlcv(pair, timeframe=timeframe, since=since, limit=limit)
            except Exception:
                break
            if not ohlcv:
                break
            all_candles.extend(ohlcv)
            since = ohlcv[-1][0] + 1
            if len(ohlcv) < limit:
                break
            await asyncio.sleep(0.5)
    finally:
        await exchange.close()

    if not all_candles:
        return 0

    candles_data = [{
        "pair": pair,
        "timeframe": timeframe,
        "timestamp": datetime.utcfromtimestamp(row[0] / 1000),
        "open": row[1], "high": row[2], "low": row[3],
        "close": row[4], "volume": row[5],
    } for row in all_candles]

    db = SessionLocal()
    try:
        inserted = upsert_candles(db, candles_data)
    finally:
        db.close()

    return inserted


def run_simulation(db_path: str, days: int = 30) -> dict:
    initial_balance = config.trading.demo_initial_balance
    balance = initial_balance
    positions = {}
    trades = []
    equity_history = []
    
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()
    
    cutoff = datetime.utcnow() - timedelta(days=days)
    min_candles = config.trading.candles_required + 50
    
    candles_data = {}
    
    for pair in config.trading.pairs:
        candles = session.query(Candle).filter(
            Candle.pair == pair,
            Candle.timeframe == config.trading.timeframe,
        ).order_by(Candle.timestamp).all()
        
        if len(candles) < min_candles:
            continue
        
        df = pd.DataFrame([{
            "timestamp": c.timestamp,
            "open": c.open, "high": c.high, "low": c.low,
            "close": c.close, "volume": c.volume,
        } for c in candles])
        
        df = df[df["timestamp"] >= cutoff].reset_index(drop=True)
        if len(df) < min_candles:
            continue
        
        candles_data[pair] = df
    
    session.close()
    
    if not candles_data:
        return {"error": "Sin datos suficientes"}
    
    predictor = ModelPredictor()
    if not predictor.is_model_loaded():
        return {"error": "Modelo no cargado"}
    
    fb = FeatureBuilder()
    
    all_timestamps = set()
    for df in candles_data.values():
        all_timestamps.update(df["timestamp"].tolist())
    
    timestamps = sorted([ts for ts in all_timestamps if ts >= cutoff])
    
    for ts in timestamps:
        prices = {}
        signals_for_pair = {}
        
        for pair, df in candles_data.items():
            idx_arr = df["timestamp"].values
            pos = np.searchsorted(idx_arr, ts, side="right") - 1
            
            if pos < 0 or pos >= len(df):
                continue
            
            candle_row = df.iloc[pos]
            prices[pair] = float(candle_row["close"])
            
            if pos < fb.MIN_ROWS:
                continue
            
            subset = df.iloc[:pos+1].copy()
            subset_ind = calculate_indicators(subset)
            features = fb.build_features(subset_ind)
            
            if features is not None:
                signal = predictor.predict(features)
                if signal:
                    signals_for_pair[pair] = signal
        
        if not prices:
            continue
        
        for pair in list(positions.keys()):
            pos = positions[pair]
            current_price = prices.get(pair)
            if not current_price:
                continue
            
            should_exit = False
            if current_price <= pos["stop_loss"]:
                should_exit = True
            elif current_price >= pos["take_profit"]:
                should_exit = True
            else:
                signal = signals_for_pair.get(pair)
                if signal and signal["signal"] == "SELL" and signal["confidence"] >= config.risk.sell_threshold:
                    should_exit = True
            
            if should_exit:
                amount_eur = pos["amount_crypto"] * current_price
                fee = amount_eur * config.exchange.taker_fee
                pnl = amount_eur - fee - pos["invested"]
                balance += amount_eur - fee
                trades.append({"side": "sell", "price": current_price, "pnl": pnl, "fee": fee})
                del positions[pair]
        
        for pair in candles_data.keys():
            if pair in positions:
                continue
            if len(positions) >= config.risk.max_open_positions:
                break
            
            signal = signals_for_pair.get(pair)
            if not signal or signal["signal"] != "BUY":
                continue
            if signal["confidence"] < config.risk.buy_threshold:
                continue
            
            current_price = prices.get(pair)
            if not current_price:
                continue
            
            idx_arr = candles_data[pair]["timestamp"].values
            pos = np.searchsorted(idx_arr, ts, side="right") - 1
            if pos < 0:
                continue
            
            subset = candles_data[pair].iloc[:pos+1].copy()
            subset_ind = calculate_indicators(subset)
            atr = get_atr(subset_ind)
            atr_pct = atr / current_price if current_price > 0 else 0
            
            if atr_pct > config.risk.high_volatility_atr_threshold:
                continue
            
            total_value = balance + sum(
                p["amount_crypto"] * prices.get(p["pair"], p["entry"])
                for p in positions.values()
            )
            risk_amount = total_value * config.risk.max_risk_per_trade_pct
            
            if atr > 0:
                stop_dist_pct = atr_pct * config.risk.stop_loss_atr_multiplier
                pos_size = risk_amount / stop_dist_pct
            else:
                pos_size = risk_amount * 5
            
            pos_size = min(pos_size, total_value * 0.20)
            pos_size = min(pos_size, balance * 0.95)
            
            if pos_size < config.risk.min_trade_eur:
                continue
            
            amount_crypto = pos_size / current_price
            stop_loss = current_price - (atr * config.risk.stop_loss_atr_multiplier)
            take_profit = current_price + (atr * config.risk.take_profit_atr_multiplier)
            fee = pos_size * config.exchange.taker_fee
            
            balance -= pos_size
            positions[pair] = {
                "pair": pair, "amount_crypto": amount_crypto,
                "entry": current_price, "stop_loss": stop_loss,
                "take_profit": take_profit, "invested": pos_size,
            }
            trades.append({"side": "buy", "price": current_price, "fee": fee})
        
        crypto_val = sum(
            p["amount_crypto"] * prices.get(p["pair"], p["entry"])
            for p in positions.values()
        )
        equity_history.append(balance + crypto_val)
    
    sells = [t for t in trades if t["side"] == "sell"]
    winners = [t for t in sells if t.get("pnl", 0) > 0]
    losers = [t for t in sells if t.get("pnl", 0) <= 0]
    win_rate = len(winners) / len(sells) * 100 if sells else 0
    
    final_equity = equity_history[-1] if equity_history else initial_balance
    total_return = (final_equity - initial_balance) / initial_balance * 100
    
    max_dd = 0.0
    if equity_history:
        peak = equity_history[0]
        for val in equity_history:
            if val > peak:
                peak = val
            dd = (peak - val) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
    
    sharpe = 0.0
    if len(equity_history) >= 10:
        returns = []
        for i in range(1, len(equity_history)):
            if equity_history[i-1] > 0:
                ret = (equity_history[i] - equity_history[i-1]) / equity_history[i-1]
                returns.append(ret)
        if returns:
            mean_ret = np.mean(returns)
            std_ret = np.std(returns)
            if std_ret > 0:
                sharpe = (mean_ret / std_ret) * np.sqrt(252 * 24)
    
    return {
        "success": True,
        "params": {
            "days": days, "initial_balance": initial_balance,
            "pairs": list(candles_data.keys()),
            "timeframe": config.trading.timeframe,
            "timestamps": len(timestamps),
        },
        "results": {
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_return, 2),
            "total_trades": len(trades),
            "buy_trades": len([t for t in trades if t["side"] == "buy"]),
            "sell_trades": len(sells),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate_pct": round(win_rate, 1),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "total_fees": round(sum(t.get("fee", 0) for t in trades), 2),
            "best_trade": round(max((t.get("pnl", 0) for t in sells), default=0), 2),
            "worst_trade": round(min((t.get("pnl", 0) for t in sells), default=0), 2),
        },
        "trades_detail": [
            {"side": t["side"], "price": round(t["price"], 2), "pnl": round(t.get("pnl", 0), 2)}
            for t in trades[-50:]
        ],
    }


@router.get("/")
async def simulate(
    days: int = Query(default=30, ge=7, le=90),
    download_data: bool = Query(default=False),
):
    init_db()
    db_path = config.database.sqlite_path

    if download_data:
        required_candles = int((90 * 24 * 60) / 5)
        for pair in config.trading.pairs:
            db = SessionLocal()
            try:
                existing = get_candle_count(db, pair, config.trading.timeframe)
            finally:
                db.close()

            if existing < required_candles:
                await fetch_historical_data(pair, days=90)

    result = run_simulation(db_path, days=days)
    return result
