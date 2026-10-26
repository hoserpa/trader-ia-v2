from fastapi import APIRouter
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))
from config import config

router = APIRouter()


@router.get("/status")
async def bot_status():
    from api.main import get_redis
    redis = get_redis()
    raw = await redis.get("bot:status")
    return json.loads(raw) if raw else {"status": "unknown"}


@router.get("/grid")
async def grid_status():
    from api.main import _trading_engine
    if _trading_engine and hasattr(_trading_engine, 'grid_strategy'):
        return _trading_engine.grid_strategy.get_state()
    return {"enabled": False}


@router.get("/config-history")
async def config_history(limit: int = 50):
    from database.crud import get_config_history
    from database.init_db import SessionLocal

    db = SessionLocal()
    try:
        rows = get_config_history(db, limit=limit)
        return [
            {
                "id": r.id,
                "ts": r.ts.isoformat() if r.ts else None,
                "config_hash": r.config_hash,
                "source": r.source,
                "config": json.loads(r.config_json),
            }
            for r in rows
        ]
    finally:
        db.close()


@router.get("/rebalances")
async def rebalances(limit: int = 50):
    from database.crud import get_rebalance_events
    from database.init_db import SessionLocal

    db = SessionLocal()
    try:
        rows = get_rebalance_events(db, limit=limit)
        return [
            {
                "id": r.id,
                "ts": r.ts.isoformat() if r.ts else None,
                "pair": r.pair,
                "price": r.price,
                "center_old": r.center_old,
                "center_new": r.center_new,
                "deviation_pct": r.deviation_pct,
                "positions_closed": r.positions_closed,
                "resting_cancelled": r.resting_cancelled,
                "realized_pnl_eur": r.realized_pnl_eur,
                "fees_eur": r.fees_eur,
                "config_hash": r.config_hash,
            }
            for r in rows
        ]
    finally:
        db.close()


@router.get("/config")
async def bot_config():
    from config import config
    return {
        "mode": config.trading.mode,
        "pairs": config.trading.pairs,
        "timeframe": config.trading.timeframe,
        "exchange": {
            "name": config.exchange.name,
            "taker_fee": config.exchange.taker_fee,
            "maker_fee": config.exchange.maker_fee,
        },
        "grid": {
            "enabled": config.grid.enabled,
            "leverage": config.grid.leverage,
            "levels": config.grid.levels_per_pair,
            "min_lot_value_eur": config.grid.min_lot_value_eur,
            "capital_pct": config.grid.capital_pct,
            "range_pct": config.grid.range_pct,
            "poll_interval": config.grid.poll_interval,
            "stop_loss_pct": config.grid.stop_loss_pct,
        }
    }
