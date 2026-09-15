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


def get_candles(db: Session, pair: str, timeframe: str, limit: int = 500, since=None) -> list[Candle]:
    query = (
        db.query(Candle)
        .filter_by(pair=pair, timeframe=timeframe)
        .order_by(desc(Candle.timestamp))
    )
    if since is not None:
        query = query.filter(Candle.timestamp >= since)
    return query.limit(limit).all()


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


def get_open_position_by_pair_dict(db: Session, pair: str) -> Optional[dict]:
    pos = db.query(Position).filter_by(pair=pair, status="open").first()
    if pos:
        return {
            "id": pos.id,
            "pair": pos.pair,
            "amount_crypto": pos.amount_crypto,
            "entry_price": pos.entry_price,
            "stop_loss_price": pos.stop_loss_price,
            "take_profit_price": pos.take_profit_price,
            "amount_eur_invested": pos.amount_eur_invested,
            "entry_timestamp": pos.entry_timestamp.isoformat() + "Z" if pos.entry_timestamp else None,
            "position_type": getattr(pos, "position_type", "long"),
        }
    return None


def update_position_order_ids(db: Session, position_id: int, sl_order_id: str = None, tp_order_id: str = None) -> Position:
    """Actualiza los IDs de órdenes stop-loss/take-profit de exchange en una posición."""
    pos = db.query(Position).get(position_id)
    if sl_order_id:
        pos.stop_loss_order_id = sl_order_id
    if tp_order_id:
        pos.take_profit_order_id = tp_order_id
    db.commit()
    return pos


def update_position_partial_pnl(db: Session, position_id: int, partial_pnl: float) -> Position:
    """Acumula PnL de una venta parcial en realized_pnl_eur de la posición."""
    pos = db.query(Position).get(position_id)
    pos.realized_pnl_eur = (pos.realized_pnl_eur or 0.0) + partial_pnl
    db.commit()
    return pos


def close_position(db: Session, position_id: int, close_price: float, reason: str, close_fee: float = 0.0) -> Position:
    pos = db.query(Position).get(position_id)
    pos.status = "closed"
    pos.close_price = close_price
    pos.close_timestamp = datetime.utcnow()
    pos.close_reason = reason
    pos_type = getattr(pos, "position_type", "long")
    realized = pos.realized_pnl_eur or 0.0
    if pos_type == "short":
        final_pnl = (pos.entry_price - close_price) * pos.amount_crypto - close_fee
        pos.pnl_pct = (pos.entry_price - close_price) / pos.entry_price * 100
    else:
        final_pnl = (close_price - pos.entry_price) * pos.amount_crypto - close_fee
        pos.pnl_pct = (close_price - pos.entry_price) / pos.entry_price * 100
    pos.pnl_eur = realized + final_pnl
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


def get_operations(db: Session, limit: int = 50, offset: int = 0) -> list[dict]:
    """Devuelve operaciones del grid agrupadas por ciclo (entrada + cierre).

    Cada operacion agrupa la pierna de apertura y la de cierre que comparten
    cycle_id. El cierre es cualquier pierna con pnl_eur (incluye ciclos de una
    sola pierna liquidada por rebalance). Para trades sin cycle_id
    (historicos), se agrupan por position_id o se devuelven como operacion
    unica con status abierta.

    Returns:
        list de dicts: {
            id, pair, entry_timestamp, entry_price, amount_crypto, entry_fee,
            exit_timestamp, exit_price, exit_fee, total_fees, pnl_eur, status, mode
        }
    """
    trades = (
        db.query(Trade)
        .order_by(Trade.timestamp.asc())
        .all()
    )
    groups: dict = {}
    order: list = []
    for t in trades:
        key = t.cycle_id or (f"pos_{t.position_id}" if t.position_id else f"id_{t.id}")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(t)

    ops = []
    for key in reversed(order):
        items = groups[key]
        if not items:
            continue
        items.sort(key=lambda x: x.timestamp)
        opening = items[0]
        closing = next((x for x in items if x.pnl_eur is not None), None)
        side = opening.side
        total_fees = round(sum(x.fee_eur for x in items), 4)
        if closing:
            status = "closed"
            pnl_eur = round(closing.pnl_eur, 4)
            exit_price = closing.price
            exit_fee = closing.fee_eur
            exit_ts = closing.timestamp
        else:
            status = "open"
            pnl_eur = None
            exit_price = None
            exit_fee = 0.0
            exit_ts = None
        ops.append({
            "id": key,
            "pair": opening.pair,
            "side": side,
            "status": status,
            "mode": opening.mode,
            "amount_crypto": round(opening.amount_crypto, 8),
            "entry_price": round(opening.price, 8),
            "entry_fee": round(opening.fee_eur, 4),
            "exit_price": round(exit_price, 8) if exit_price is not None else None,
            "exit_fee": round(exit_fee, 4),
            "total_fees": total_fees,
            "pnl_eur": pnl_eur,
            "amount_eur_entry": round(opening.amount_eur, 4),
            "entry_timestamp": opening.timestamp.isoformat() + "Z",
            "exit_timestamp": exit_ts.isoformat() + "Z" if exit_ts else None,
        })
        if len(ops) >= limit:
            break
    return ops[offset:]


def get_recent_operations(db: Session, limit: int = 8) -> list[dict]:
    """Devuelve las operaciones del grid mas recientes (ya agrupadas por ciclo)."""
    return get_operations(db, limit, 0)


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
    """Estadisticas derivadas de los trades del grid en la tabla trades.

    El grid escribe pierna a pierna (apertura sin pnl, cierres con pnl). Las
    metricas se calculan sobre TODOS los cierres (piernas con pnl_eur), no solo
    el primero de cada ciclo, para no subcontar ciclos con varios rellenos.
    """
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_errors = db.query(func.count(SystemLog.id)).filter(
        SystemLog.timestamp >= today,
        SystemLog.level.in_(["ERROR", "CRITICAL"])
    ).scalar() or 0

    trades = db.query(Trade).order_by(Trade.timestamp.asc()).all()

    groups: dict = {}
    order: list = []
    for t in trades:
        key = t.cycle_id or (f"pos_{t.position_id}" if t.position_id else f"id_{t.id}")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(t)

    pnls = []
    pnl_pcts = []
    total_fees = 0.0
    today_pnls = []
    for key in order:
        items = groups[key]
        items.sort(key=lambda x: x.timestamp)
        total_fees += round(sum(x.fee_eur for x in items), 4)
        for x in items:
            if x.pnl_eur is None:
                continue
            pnls.append(x.pnl_eur)
            notional = round(x.amount_crypto * x.price, 4)
            pnl_pcts.append(x.pnl_eur / notional * 100 if notional else 0.0)
            if x.timestamp >= today:
                today_pnls.append(x.pnl_eur)

    open_ops = sum(1 for k in order if all(x.pnl_eur is None for x in groups[k]))
    total_ops = len(order)
    total_trades = len(trades)
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    flat = sum(1 for p in pnls if p == 0)
    total_pnl = round(sum(pnls), 4)
    win_rate = (wins / len(pnls) * 100) if pnls else 0
    avg_pnl_pct = round(sum(pnl_pcts) / len(pnl_pcts), 2) if pnl_pcts else 0.0

    today_wins = sum(1 for p in today_pnls if p > 0)
    today_losses = sum(1 for p in today_pnls if p < 0)

    best_trade = max(pnls) if pnls else 0
    worst_trade = min(pnls) if pnls else 0
    max_drawdown = calculate_max_drawdown_from_snapshots(db)

    return {
        "total_trades": total_trades,
        "total_operations": total_ops,
        "closed_positions": len(pnls),
        "closed_operations": len(pnls),
        "open_operations": open_ops,
        "wins_total": wins,
        "losses_total": losses,
        "flat_total": flat,
        "win_rate": round(win_rate, 2),
        "avg_pnl_eur": round(total_pnl / len(pnls), 4) if pnls else 0,
        "avg_pnl_pct": avg_pnl_pct,
        "total_pnl_eur": total_pnl,
        "total_fees_eur": round(total_fees, 4),
        "trades_today": len(today_pnls),
        "today_operations": len(today_pnls),
        "today_closed": len(today_pnls),
        "wins_today": today_wins,
        "losses_today": today_losses,
        "best_trade": round(best_trade, 4),
        "worst_trade": round(worst_trade, 4),
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


def reset_full_portfolio_data(db: Session) -> dict:
    """Reset completo: borra todo (snapshots, trades, posiciones ABIERTAS Y CERRADAS, stats).
    Para uso exclusivo en modo DEMO."""
    deleted_snapshots = db.query(PortfolioSnapshot).delete()
    deleted_trades = db.query(Trade).delete()
    deleted_positions = db.query(Position).delete()
    db.commit()
    return {
        "snapshots_deleted": deleted_snapshots,
        "trades_deleted": deleted_trades,
        "positions_deleted": deleted_positions,
    }
