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
            "capital_pct": config.grid.capital_pct,
            "range_pct": config.grid.range_pct,
            "atr_adaptive": config.grid.atr_adaptive,
            "poll_interval": config.grid.poll_interval,
            "stop_loss_pct": config.grid.stop_loss_pct,
        }
    }
