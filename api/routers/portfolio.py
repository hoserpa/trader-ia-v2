from fastapi import APIRouter, Depends, Query
import json
import redis.asyncio as aioredis
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))
from database.crud import get_portfolio_history, reset_portfolio_data, reset_full_portfolio_data
from database.init_db import SessionLocal

router = APIRouter()


@router.get("")
async def get_portfolio():
    from api.main import get_redis
    redis = get_redis()
    raw = await redis.get("portfolio:state")
    return json.loads(raw) if raw else {"error": "Portfolio no disponible aún"}


@router.get("/history")
def get_history(days: int = Query(default=30, ge=1, le=365)):
    db = SessionLocal()
    try:
        snapshots = get_portfolio_history(db, days)
        return [{"timestamp": s.timestamp.isoformat(), "total_value_eur": s.total_value_eur,
                 "balance_eur": s.balance_eur, "total_pnl_eur": s.total_pnl_eur,
                 "total_pnl_pct": s.total_pnl_pct} for s in snapshots]
    finally:
        db.close()


@router.post("/reset")
async def reset_history():
    from api.main import get_redis
    redis = get_redis()
    status_raw = await redis.get("bot:status")
    if status_raw:
        import json
        status = json.loads(status_raw)
        if status.get("mode") != "demo":
            return {"error": "Solo disponible en modo DEMO"}
    
    db = SessionLocal()
    try:
        result = reset_portfolio_data(db)
        return {"success": True, **result}
    finally:
        db.close()


@router.post("/reset-full")
async def reset_full():
    """Reset completo: borra todo (trades, posiciones, snapshots, balance Redis).
    Solo disponible en modo DEMO."""
    from api.main import get_redis
    redis = get_redis()
    status_raw = await redis.get("bot:status")
    if status_raw:
        import json
        status = json.loads(status_raw)
        if status.get("mode") != "demo":
            return {"error": "Solo disponible en modo DEMO"}
    
    db = SessionLocal()
    try:
        result = reset_full_portfolio_data(db)
        
        await redis.delete("portfolio:state")
        await redis.delete("bot:stats")
        await redis.delete("open_positions")
        await redis.set("portfolio:balance_eur", "10000")
        
        return {"success": True, **result}
    finally:
        db.close()
