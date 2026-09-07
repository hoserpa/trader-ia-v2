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

    # Las posiciones abiertas se derivan por ciclo (get_operations agrupa por
    # cycle_id), no por par: así se ven shorts/longs solapados del mismo par.
    # El PnL no realizado se calcula con el precio actual (como update_valuations).
    from database.crud import get_operations
    from config import config
    current_prices: dict = {}
    open_positions: dict = {}

    db = SessionLocal()
    try:
        ops = get_operations(db, limit=100)
    finally:
        db.close()

    for op in ops:
        if op["status"] != "open":
            continue
        pair = op["pair"]
        if pair not in current_prices:
            p_raw = await redis.get(f"price:{pair}")
            try:
                current_prices[pair] = float(p_raw) if p_raw else None
            except (ValueError, TypeError):
                current_prices[pair] = None
        price = current_prices.get(pair) or op["entry_price"]
        amount = op["amount_crypto"]
        invested = op["amount_eur_entry"]
        current_value = amount * price
        exit_fee = current_value * config.exchange.taker_fee
        if op["side"] == "SELL":
            pnl = invested - current_value - exit_fee
        else:
            pnl = current_value - invested - exit_fee
        open_positions[op["id"]] = {
            "cycle_id": op["id"],
            "pair": pair,
            "position_type": "short" if op["side"] == "SELL" else "long",
            "entry_price": op["entry_price"],
            "amount_crypto": amount,
            "amount_eur_invested": round(invested, 4),
            "current_price": round(price, 8),
            "pnl_eur": round(pnl, 4),
            "pnl_pct": round(pnl / invested * 100, 4) if invested > 0 else 0.0,
            "stop_loss_price": None,
            "take_profit_price": None,
        }

    data["positions"] = open_positions
    data["open_positions"] = len(open_positions)
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
