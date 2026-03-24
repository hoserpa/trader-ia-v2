from fastapi import APIRouter
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
            "executed": d.executed, "timestamp": d.timestamp.isoformat(),
        } for d in decisions]
    finally:
        db.close()
