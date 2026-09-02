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
    if not raw:
        return {"error": "Portfolio no disponible aún"}
    data = json.loads(raw)
    # La fuente de verdad de posiciones abiertas es el dict positions (el grid lo
    # puebla al abrir/cerrar ciclos); alineamos el contador con el.
    data["open_positions"] = len(data.get("positions", {}))
    return data


@router.get("/history")
def get_history(days: int = Query(default=30, ge=1, le=365)):
    db = SessionLocal()
    try:
        snapshots = get_portfolio_history(db, days)
        return [{"timestamp": s.timestamp.isoformat() + "Z", "total_value_eur": s.total_value_eur,
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
        
        initial_balance = float(os.getenv("DEMO_INITIAL_BALANCE", "100.0"))
        from datetime import datetime
        new_portfolio = {
            "balance_eur": initial_balance,
            "initial_balance_eur": initial_balance,
            "positions": {},
            "total_value_eur": initial_balance,
            "total_pnl_eur": 0.0,
            "total_pnl_pct": 0.0,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        await redis.set("portfolio:state", json.dumps(new_portfolio))
        await redis.delete("bot:stats")
        await redis.delete("open_positions")

        # Limpia tambien el estado del grid (niveles e historico) y lo reinicia
        # en caliente para garantizar un ciclo limpio sin reiniciar el contenedor.
        from bot.strategies.grid_strategy import REDIS_GRID_STATE_KEY, REDIS_GRID_GLOBAL_KEY
        from config import config
        for pair in config.grid.pairs:
            await redis.delete(REDIS_GRID_STATE_KEY.format(pair=pair))
        await redis.delete(REDIS_GRID_GLOBAL_KEY)

        from api.main import _trading_engine
        if _trading_engine and getattr(_trading_engine, "grid_strategy", None):
            await _trading_engine.grid_strategy.reset()
        
        return {"success": True, **result}
    finally:
        db.close()
