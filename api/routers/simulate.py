"""Endpoint para ejecutar simulacion de trading."""
import asyncio
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from typing import Optional

from bot.config import config
from bot.database.crud import upsert_candles, get_candle_count
from bot.database.init_db import init_db, SessionLocal
from bot.indicators.technical import calculate_indicators, get_atr
from bot.indicators.features import FeatureBuilder
from bot.model.predictor import ModelPredictor
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pandas as pd
import numpy as np

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
            except Exception as e:
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
    from dataclasses import dataclass, field
    from typing import Optional as Opt

    @dataclass
    class SimPosition:
        pair: str
        amount_crypto: float
        entry_price: float
        stop_loss_price: float
        take_profit_price: float
        amount_eur_invested: float

    @dataclass
    class SimTrade:
        side: str
        amount_crypto: float
        amount_eur: float
        price: float
        fee_eur: float
        pnl_eur: float

    initial_balance = config.trading.demo_initial_balance
    balance = initial_balance
    positions = {}
    trades = []
    equity_history = []

    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    candles_data = {}
    for pair in config.trading.pairs:
        from bot.database.models import Candle
        candles = session.query(Candle).filter(
            Candle.pair == pair,
            Candle.timeframe == config.trading.timeframe,
        ).order_by(Candle.timestamp).all()

        if candles:
            df = pd.DataFrame([{
                "timestamp": c.timestamp,
                "open": c.open, "high": c.high, "low": c.low,
                "close": c.close, "volume": c.volume,
            } for c in candles])
            cutoff = datetime.utcnow() - timedelta(days=days)
            df = df[df["timestamp"] >= cutoff].reset_index(drop=True)
            if len(df) >= config.trading.candles_required + 100:
                candles_data[pair] = df

    session.close()

    if not candles_data:
        return {"error": "Sin datos suficientes"}

    predictor = ModelPredictor()
    feature_builder = FeatureBuilder()

    all_timestamps = set()
    for candles in candles_data.values():
        all_timestamps.update(candles["timestamp"].tolist())

    cutoff = datetime.utcnow() - timedelta(days=days)
    timestamps = sorted([ts for ts in all_timestamps if ts >= cutoff])

    for ts in timestamps:
        prices = {}
        candles_for_analysis = {}

        for pair, candles in candles_data.items():
            idx = candles["timestamp"].searchsorted(ts)
            if idx < len(candles):
                candles_for_analysis[pair] = candles.iloc[:idx+1].copy()
                prices[pair] = float(candles.iloc[idx]["close"])

        if not prices:
            continue

        for pair, candles in candles_for_analysis.items():
            if len(candles) < config.trading.candles_required:
                continue

            current_price = prices.get(pair)
            if not current_price:
                continue

            df_with_indicators = calculate_indicators(candles)
            features = feature_builder.build_features(df_with_indicators)
            if features is None:
                continue

            signal = predictor.predict(features)
            if signal is None:
                continue

            if pair in positions:
                pos = positions[pair]
                should_exit = False
                if current_price <= pos.stop_loss_price:
                    should_exit = True
                elif current_price >= pos.take_profit_price:
                    should_exit = True
                elif signal["signal"] == "SELL" and signal["confidence"] >= config.risk.sell_threshold:
                    should_exit = True

                if should_exit:
                    amount_eur = pos.amount_crypto * current_price
                    fee = amount_eur * config.exchange.taker_fee
                    pnl = amount_eur - fee - pos.amount_eur_invested
                    balance += amount_eur - fee
                    trades.append(SimTrade(
                        side="sell", amount_crypto=pos.amount_crypto,
                        amount_eur=amount_eur, price=current_price,
                        fee_eur=fee, pnl_eur=pnl
                    ))
                    del positions[pair]
            else:
                if signal["signal"] != "BUY":
                    continue
                if signal["confidence"] < config.risk.buy_threshold:
                    continue
                if len(positions) >= config.risk.max_open_positions:
                    continue

                atr = get_atr(df_with_indicators)
                atr_pct = atr / current_price if current_price > 0 else 0
                if atr_pct > config.risk.high_volatility_atr_threshold:
                    continue

                total_value = balance + sum(
                    p.amount_crypto * prices.get(p.pair, p.entry_price)
                    for p in positions.values()
                )
                risk_amount = total_value * config.risk.max_risk_per_trade_pct

                if atr > 0:
                    stop_distance_pct = atr_pct * config.risk.stop_loss_atr_multiplier
                    position_size = risk_amount / stop_distance_pct
                else:
                    position_size = risk_amount * 5

                position_size = min(position_size, total_value * 0.20)
                position_size = min(position_size, balance * 0.95)

                if position_size < config.risk.min_trade_eur:
                    continue

                amount_crypto = position_size / current_price
                stop_loss = current_price - (atr * config.risk.stop_loss_atr_multiplier)
                take_profit = current_price + (atr * config.risk.take_profit_atr_multiplier)
                fee = position_size * config.exchange.taker_fee

                balance -= position_size
                positions[pair] = SimPosition(
                    pair=pair, amount_crypto=amount_crypto,
                    entry_price=current_price, stop_loss_price=stop_loss,
                    take_profit_price=take_profit, amount_eur_invested=position_size
                )
                trades.append(SimTrade(
                    side="buy", amount_crypto=amount_crypto,
                    amount_eur=position_size, price=current_price,
                    fee_eur=fee, pnl_eur=0
                ))

        crypto_value = sum(
            p.amount_crypto * prices.get(p.pair, p.entry_price)
            for p in positions.values()
        )
        equity_history.append(balance + crypto_value)

    sells = [t for t in trades if t.side == "sell"]
    winners = [t for t in sells if t.pnl_eur > 0]
    losers = [t for t in sells if t.pnl_eur <= 0]
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
            if dd > max_dd:
                max_dd = dd

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
            "days": days,
            "initial_balance": initial_balance,
            "pairs": config.trading.pairs,
            "timeframe": config.trading.timeframe,
        },
        "results": {
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_return, 2),
            "total_trades": len(trades),
            "buy_trades": len([t for t in trades if t.side == "buy"]),
            "sell_trades": len(sells),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate_pct": round(win_rate, 1),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "total_fees": round(sum(t.fee_eur for t in trades), 2),
            "best_trade": round(max((t.pnl_eur for t in sells), default=0), 2),
            "worst_trade": round(min((t.pnl_eur for t in sells), default=0), 2),
        },
        "trades_detail": [
            {"pair": config.trading.pairs[i % len(config.trading.pairs)],
             "side": t.side, "price": round(t.price, 2),
             "pnl": round(t.pnl_eur, 2)}
            for i, t in enumerate(trades)
        ] if trades else [],
    }


@router.get("/simulate")
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
