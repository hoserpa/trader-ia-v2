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
        "analysis_interval": config.trading.analysis_interval,
        "risk": {
            "max_risk_per_trade_pct": config.risk.max_risk_per_trade_pct,
            "max_open_positions": config.risk.max_open_positions,
            "buy_threshold": config.risk.buy_threshold,
            "sell_threshold": config.risk.sell_threshold,
            "stop_loss_atr_multiplier": config.risk.stop_loss_atr_multiplier,
            "take_profit_atr_multiplier": config.risk.take_profit_atr_multiplier,
        }
    }
