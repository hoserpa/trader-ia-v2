from fastapi import APIRouter, Query
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))
from database.crud import get_trades, get_stats_summary
from database.init_db import SessionLocal

router = APIRouter()


@router.get("")
def list_trades(limit: int = Query(default=50, ge=1, le=500), offset: int = 0):
    db = SessionLocal()
    try:
        trades = get_trades(db, limit, offset)
        return [{
            "id": t.id, "pair": t.pair, "side": t.side,
            "amount_crypto": t.amount_crypto, "amount_eur": t.amount_eur,
            "price": t.price, "fee_eur": t.fee_eur,
            "timestamp": t.timestamp.isoformat(), "mode": t.mode,
        } for t in trades]
    finally:
        db.close()


@router.get("/stats")
def trade_stats():
    db = SessionLocal()
    try:
        return get_stats_summary(db)
    finally:
        db.close()
