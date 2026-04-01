"""Operaciones CRUD sobre la base de datos."""
import json
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from .models import Candle, PortfolioSnapshot, Position, Trade, ModelDecision, SystemLog, BotConfig


def upsert_candles(db: Session, candles: list[dict]) -> int:
    """Inserta velas ignorando duplicados. Retorna número de velas insertadas."""
    inserted = 0
    for c in candles:
        exists = db.query(Candle).filter_by(
            pair=c["pair"], timeframe=c["timeframe"], timestamp=c["timestamp"]
        ).first()
        if not exists:
            db.add(Candle(**c))
            inserted += 1
    db.commit()
    return inserted


def get_candles(db: Session, pair: str, timeframe: str, limit: int = 500) -> list[Candle]:
    return (
        db.query(Candle)
        .filter_by(pair=pair, timeframe=timeframe)
        .order_by(desc(Candle.timestamp))
        .limit(limit)
        .all()
    )


def get_candle_count(db: Session, pair: str, timeframe: str) -> int:
    """Retorna el número de velas para un par y timeframe."""
    return db.query(func.count(Candle.id)).filter_by(
        pair=pair, timeframe=timeframe
    ).scalar() or 0


def save_portfolio_snapshot(db: Session, snapshot: dict) -> PortfolioSnapshot:
    obj = PortfolioSnapshot(
        timestamp=datetime.utcnow(),
        balance_eur=snapshot["balance_eur"],
        total_value_eur=snapshot["total_value_eur"],
        total_pnl_eur=snapshot["total_pnl_eur"],
        total_pnl_pct=snapshot["total_pnl_pct"],
        positions_json=json.dumps(snapshot.get("positions", {})),
    )
    db.add(obj)
    db.commit()
    return obj


def get_portfolio_history(db: Session, days: int = 30) -> list[PortfolioSnapshot]:
    since = datetime.utcnow() - timedelta(days=days)
    return (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.timestamp >= since)
        .order_by(PortfolioSnapshot.timestamp)
        .all()
    )


def create_position(db: Session, position_data: dict) -> Position:
    pos = Position(**position_data)
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return pos


def get_open_positions(db: Session) -> list[Position]:
    return db.query(Position).filter_by(status="open").all()


def get_open_position_by_pair(db: Session, pair: str) -> Optional[Position]:
    return db.query(Position).filter_by(pair=pair, status="open").first()


def close_position(db: Session, position_id: int, close_price: float, reason: str) -> Position:
    pos = db.query(Position).get(position_id)
    pos.status = "closed"
    pos.close_price = close_price
    pos.close_timestamp = datetime.utcnow()
    pos.close_reason = reason
    pos.pnl_eur = (close_price - pos.entry_price) * pos.amount_crypto
    pos.pnl_pct = (close_price - pos.entry_price) / pos.entry_price * 100
    db.commit()
    return pos


def create_trade(db: Session, trade_data: dict) -> Trade:
    trade = Trade(**trade_data)
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return trade


def get_trades(db: Session, limit: int = 50, offset: int = 0) -> list[Trade]:
    return (
        db.query(Trade)
        .order_by(desc(Trade.timestamp))
        .offset(offset)
        .limit(limit)
        .all()
    )


def count_trades_today(db: Session) -> int:
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(func.count(Trade.id)).filter(Trade.timestamp >= today).scalar()


def save_decision(db: Session, decision: dict) -> ModelDecision:
    obj = ModelDecision(**decision)
    db.add(obj)
    db.commit()
    return obj


def get_recent_decisions(db: Session, limit: int = 50) -> list[ModelDecision]:
    return (
        db.query(ModelDecision)
        .order_by(desc(ModelDecision.timestamp))
        .limit(limit)
        .all()
    )


def get_stats_summary(db: Session) -> dict:
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    total_trades = db.query(func.count(Trade.id)).scalar()
    closed_positions = db.query(Position).filter_by(status="closed").all()
    
    today_trades = db.query(Trade).filter(Trade.timestamp >= today).all()
    today_winners = [t for t in today_trades if t.side == "sell" and t.position and t.position.pnl_eur and t.position.pnl_eur > 0]
    today_losers = [t for t in today_trades if t.side == "sell" and t.position and t.position.pnl_eur and t.position.pnl_eur <= 0]
    
    today_errors = db.query(func.count(SystemLog.id)).filter(
        SystemLog.timestamp >= today,
        SystemLog.level.in_(["ERROR", "CRITICAL"])
    ).scalar() or 0
    
    if not closed_positions:
        return {
            "total_trades": total_trades,
            "win_rate": 0,
            "avg_pnl_pct": 0,
            "total_pnl_eur": 0,
            "trades_today": len(today_trades),
            "wins_today": len(today_winners),
            "losses_today": len(today_losers),
            "best_trade": 0,
            "worst_trade": 0,
            "max_drawdown": 0,
            "errors_today": today_errors,
        }

    winners = [p for p in closed_positions if p.pnl_eur and p.pnl_eur > 0]
    losers = [p for p in closed_positions if p.pnl_eur and p.pnl_eur <= 0]
    total_pnl = sum(p.pnl_eur for p in closed_positions if p.pnl_eur)
    avg_pnl_pct = sum(p.pnl_pct for p in closed_positions if p.pnl_pct) / len(closed_positions) if closed_positions else 0
    
    best_trade = max((p.pnl_eur for p in closed_positions if p.pnl_eur), default=0)
    worst_trade = min((p.pnl_eur for p in closed_positions if p.pnl_eur), default=0)
    
    max_drawdown = calculate_max_drawdown_from_snapshots(db)

    return {
        "total_trades": total_trades,
        "closed_positions": len(closed_positions),
        "win_rate": len(winners) / len(closed_positions) * 100 if closed_positions else 0,
        "avg_pnl_pct": avg_pnl_pct,
        "total_pnl_eur": total_pnl,
        "trades_today": len(today_trades),
        "wins_today": len(today_winners),
        "losses_today": len(today_losers),
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "max_drawdown": max_drawdown,
        "errors_today": today_errors,
    }


def calculate_max_drawdown_from_snapshots(db: Session) -> float:
    snapshots = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.timestamp).all()
    if len(snapshots) < 2:
        return 0.0
    
    values = [s.total_value_eur for s in snapshots]
    peak = values[0]
    max_dd = 0.0
    
    for value in values:
        if value > peak:
            peak = value
        drawdown = (peak - value) / peak if peak > 0 else 0
        if drawdown > max_dd:
            max_dd = drawdown
    
    return max_dd * 100


def save_log(db: Session, level: str, module: str, message: str, extra: dict = None):
    obj = SystemLog(
        level=level, module=module, message=message,
        extra_json=json.dumps(extra) if extra else None,
    )
    db.add(obj)
    db.commit()


def get_logs(db: Session, level: Optional[str] = None, limit: int = 100) -> list[SystemLog]:
    q = db.query(SystemLog).order_by(desc(SystemLog.timestamp))
    if level:
        q = q.filter_by(level=level.upper())
    return q.limit(limit).all()


def reset_portfolio_data(db: Session) -> dict:
    """Resetea el historial del portfolio (snapshots, trades, posiciones).
    Mantiene posiciones abiertas y el balance en Redis."""
    deleted_snapshots = db.query(PortfolioSnapshot).delete()
    deleted_trades = db.query(Trade).delete()
    deleted_positions = db.query(Position).filter_by(status="closed").delete()
    db.commit()
    return {
        "snapshots_deleted": deleted_snapshots,
        "trades_deleted": deleted_trades,
        "closed_positions_deleted": deleted_positions,
    }
