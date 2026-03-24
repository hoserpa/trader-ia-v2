from fastapi import APIRouter, Depends, Query
import json
import redis.asyncio as aioredis
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))
from database.crud import get_portfolio_history
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
