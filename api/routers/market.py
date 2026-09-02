from fastapi import APIRouter, Query
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))
from config import config

router = APIRouter()


@router.get("/prices")
async def get_prices():
    from api.main import get_redis
    redis = get_redis()
    prices = {}
    for pair in config.trading.pairs:
        val = await redis.get(f"price:{pair}")
        prices[pair] = float(val) if val else None
    return prices


@router.get("/candles")
async def get_candles(
    pair: str = Query(..., description="Par, ej. BTC/EUR"),
    timeframe: str = Query("15m"),
    limit: int = Query(2000, ge=50, le=10000),
    days: int = Query(None, ge=1, le=90),
):
    """Devuelve velas OHLCV desde SQLite para dibujar la grafica de precio.

    timeframes agregados (1h, 4h) se resamplean en vuelo desde las velas 15m
    almacenadas."""
    from database.crud import get_candles
    from database.init_db import SessionLocal
    from datetime import datetime, timezone, timedelta
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    db = SessionLocal()
    try:
        agg_map = {"1h": ("60min", 4), "4h": ("240min", 16)}
        if timeframe in agg_map:
            freq, factor = agg_map[timeframe]
            base = get_candles(db, pair.strip(), "15m", limit=limit * factor, since=since)
            import pandas as pd
            df = pd.DataFrame([{
                "timestamp": c.timestamp, "open": c.open, "high": c.high,
                "low": c.low, "close": c.close, "volume": c.volume,
            } for c in base])
            from bot.indicators.features import resample_15m_to_htf
            agg = resample_15m_to_htf(df, freq) if not df.empty else df
            candles = [{
                "timestamp": r.timestamp.isoformat(),
                "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
            } for r in agg.itertuples()]
            candles = candles[-limit:]
        else:
            base = get_candles(db, pair.strip(), timeframe, limit=limit, since=since)
            candles = [{
                "timestamp": c.timestamp.isoformat(),
                "open": c.open, "high": c.high,
                "low": c.low, "close": c.close, "volume": c.volume,
            } for c in base]
        return candles
    finally:
        db.close()


@router.get("/signals")
async def get_signals():
    from api.main import get_redis
    from database.crud import get_recent_decisions
    from database.init_db import SessionLocal
    db = SessionLocal()
    try:
        decisions = get_recent_decisions(db, limit=len(config.trading.pairs) * 3)
        return [{
            "pair": d.pair, "signal": d.signal, "confidence": d.confidence,
            "prob_buy": d.prob_buy, "prob_sell": d.prob_sell, "prob_hold": d.prob_hold,
            "executed": d.executed, "timestamp": d.timestamp.isoformat() + "Z",
        } for d in decisions]
    finally:
        db.close()
