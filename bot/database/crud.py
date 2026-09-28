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


def _group_cycle_keys(trades: list[Trade]) -> tuple[dict, list]:
    """Agrupa las piernas por ciclo (cycle_id, position_id o id) sin perder orden."""
    groups: dict = {}
    order: list = []
    for t in trades:
        key = t.cycle_id or (f"pos_{t.position_id}" if t.position_id else f"id_{t.id}")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(t)
    return groups, order


def _net_pnl(opening_side: str, entry: float, exit: float, qty: float, fee_open: float, fee_close: float) -> float:
    """PnL neto de una ida y vuelta: diferencia de precio por cantidad menos
    las comisiones atribuidas (apertura + cierre; en flips posteriores a la
    primera ida y vuelta, fee_open = 0 para no contar dos veces la comision
    intermedia del ciclo)."""
    if opening_side.upper() == "SELL":
        return (entry - exit) * qty - fee_open - fee_close
    return (exit - entry) * qty - fee_open - fee_close


def _derive_entry(side: str, fill_price: float, pnl_eur: float, fee_close: float, qty: float) -> float:
    """Deriva el precio de entrada real del grid a partir del PnL almacenado.

    El grid guarda en cada cierre: pnl = delta - fee_cierre.
      SELL close: pnl = (fill - entry) * qty - fee  -> entry = fill - (pnl + fee) / qty
      BUY close:  pnl = (entry - fill) * qty - fee  -> entry = fill + (pnl + fee) / qty
    """
    if qty <= 0:
        return fill_price
    if side.upper() == "SELL":
        return fill_price - (pnl_eur + fee_close) / qty
    return fill_price + (pnl_eur + fee_close) / qty


def _build_round_trips(trades: list[Trade]) -> tuple[list[dict], list[dict]]:
    """Reconstruye las operaciones reales (ida y vuelta) desde las piernas.

    Un cycle_id puede agrupar piernas de VARIAS cadenas: el contra-nivel
    creado en una liquidacion/rebalance hereda el cycle_id de su padre, y si
    este ya operaba, los cierres de ambas cadenas aparecen en el mismo ciclo
    con cantidades y entradas distintas. Encadenar cierres secuencialmente
    (viejo enfoque) emparejaba cierres de cadenas distintas y fabricaba filas
    irreales (p.ej. 'BUY 90 -> 84 con +N e' cuando 84 era una liquidacion de
    otra cadena).

    Aqui cada cierre se empareja de forma GLOBAL (sin mirar cycle_id) con la
    apertura real: se deriva la entrada del cierre desde su PnL almacenado y
    se busca en el pool de aperturas sin usar la del par y precio mas cercano
    (tolerancia 0.5%). Ninguna apertura se empareja dos veces.

    - Emparejado a una apertura: el PnL de la fila descuenta las dos comisiones
      (apertura + cierre), asi la suma de filas == PnL neto del balance.
    - Flip (sin apertura en el pool, entrada derivada ≈ precio de una pierna
      anterior): se muestra la entrada de esa pierna exacta con fee_apertura = 0
      (esa comision ya quedo en la fila de su propia cadena).
    - Sin coincidencia: se muestra la entrada derivada con fee_apertura = 0.

    Returns:
        (closed_rows, open_rows): operaciones cerradas y aperturas sin cierre.
    """
    TOL = 0.005  # tolerancia 0.5% para emparejar precios
    sorted_trades = sorted(trades, key=lambda t: t.timestamp)
    opens_pool: list[list] = []  # [[trade, used], ...]
    fill_history: list[Trade] = []
    closed_rows: list[dict] = []
    open_rows: list[dict] = []

    for t in sorted_trades:
        if t.pnl_eur is None:
            opens_pool.append([t, False])
            fill_history.append(t)
            continue

        derived = _derive_entry(t.side, t.price, t.pnl_eur, t.fee_eur, t.amount_crypto)
        best_idx = -1
        best_diff = TOL
        for idx, (op, used) in enumerate(opens_pool):
            if used or t.pair != op.pair or t.side.upper() == op.side.upper():
                continue
            diff = abs(derived - op.price) / max(op.price, 1e-8)
            if diff < best_diff:
                best_diff = diff
                best_idx = idx

        if best_idx >= 0:
            op = opens_pool[best_idx][0]
            opens_pool[best_idx][1] = True
            entry = op.price
            entry_fee = op.fee_eur
            entry_ts = op.timestamp
        else:
            # Flip o cadena sin apertura en el pool: snap a la pierna previa cuyo
            # precio MEJOR coincida con la entrada derivada (el grid usa como
            # entry_price de un nivel el fill_price de su padre, asi que la entrada
            # real SIEMPRE es el precio exacto de alguna pierna ya registrada).
            snapped = None
            best_can = TOL
            for prev in fill_history:
                diff = abs(derived - prev.price) / max(prev.price, 1e-8)
                if diff <= best_can:
                    best_can = diff
                    snapped = prev
            entry = snapped.price if snapped is not None else derived
            entry_fee = 0.0
            entry_ts = snapped.timestamp if snapped is not None else t.timestamp

        row_side = "SELL" if t.side.upper() == "BUY" else "BUY"
        pnl = round(_net_pnl(row_side, entry, t.price, t.amount_crypto, entry_fee, t.fee_eur), 4)
        closed_rows.append({
            "id": t.cycle_id or f"id_{t.id}",
            "pair": t.pair,
            "side": row_side,
            "status": "closed",
            "mode": t.mode,
            "amount_crypto": round(t.amount_crypto, 8),
            "entry_price": round(entry, 8),
            "entry_fee": round(entry_fee, 4),
            "exit_price": round(t.price, 8),
            "exit_fee": round(t.fee_eur, 4),
            "exit_reason": getattr(t, "reason", "grid") or "grid",
            "total_fees": round(entry_fee + t.fee_eur, 4),
            "pnl_eur": pnl,
            "amount_eur_entry": round(entry * t.amount_crypto, 4),
            "entry_timestamp": entry_ts.isoformat() + "Z",
            "exit_timestamp": t.timestamp.isoformat() + "Z",
        })
        fill_history.append(t)

    for op, used in opens_pool:
        if not used:
            open_rows.append({
                "id": op.cycle_id or f"id_{op.id}",
                "pair": op.pair,
                "side": op.side,
                "status": "open",
                "mode": op.mode,
                "amount_crypto": round(op.amount_crypto, 8),
                "entry_price": round(op.price, 8),
                "entry_fee": round(op.fee_eur, 4),
                "exit_price": None,
                "exit_fee": 0.0,
                "total_fees": round(op.fee_eur, 4),
                "pnl_eur": None,
                "amount_eur_entry": round(op.amount_eur, 4),
                "entry_timestamp": op.timestamp.isoformat() + "Z",
                "exit_timestamp": None,
            })

    return closed_rows, open_rows


def get_operations(db: Session, limit: int = 50, offset: int = 0) -> list[dict]:
    """Devuelve las operaciones del grid: una fila por ida y vuelta REAL.

    Cada cierre se empareja con su apertura (incluye liquidaciones por
    rebalance y stop-loss). El PnL de cada fila es neto de las dos comisiones
    del ciclo y cuadra con el balance. Los ciclos abiertos (sin cierre) se
    devuelven como fila abierta con su inversion.
    """
    trades = (
        db.query(Trade)
        .order_by(Trade.timestamp.asc())
        .all()
    )
    closed_rows, open_rows = _build_round_trips(trades)
    rows = sorted(
        closed_rows + open_rows,
        key=lambda r: r["entry_timestamp"],
        reverse=True,
    )
    return rows[offset:offset + limit]


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
    """Estadisticas derivadas de las operaciones reales del grid.

    El PnL total se computa DIRECTAMENTE desde las piernas (suma de pnl de
    cierres menos fees de aperturas), por lo que cuadra SIEMPRE con el balance
    (100 + total_pnl_eur = balance real) independiente del emparejamiento. Las
    comisiones totales incluyen todas las piernas (aperturas y cierres).
    """
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_errors = db.query(func.count(SystemLog.id)).filter(
        SystemLog.timestamp >= today,
        SystemLog.level.in_(["ERROR", "CRITICAL"])
    ).scalar() or 0

    trades = db.query(Trade).order_by(Trade.timestamp.asc()).all()
    _, order = _group_cycle_keys(trades)
    closed_rows, open_rows = _build_round_trips(trades)
    opens = [x for x in trades if x.pnl_eur is None]
    closes = [x for x in trades if x.pnl_eur is not None]

    pnls = [r["pnl_eur"] for r in closed_rows]
    pnl_pcts = []
    for r in closed_rows:
        notional = round(r["amount_crypto"] * r["entry_price"], 4)
        pnl_pcts.append(r["pnl_eur"] / notional * 100 if notional else 0.0)

    today_pnls = []
    for r in closed_rows:
        ts = r["exit_timestamp"]
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
        except (TypeError, ValueError):
            continue
        if dt >= today:
            today_pnls.append(r["pnl_eur"])

    total_fees = round(sum(x.fee_eur for x in trades), 4)
    open_ops = len(open_rows)
    total_ops = len(closed_rows) + open_ops
    total_trades = len(trades)
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    flat = sum(1 for p in pnls if p == 0)
    # PnL neto real: cada cierre acredita (delta - fee_cierre) y cada apertura
    # resta su fee. Cuadra SIEMPRE con el balance (100 + total_pnl = balance),
    # independiente del emparejamiento de filas de _build_round_trips.
    total_pnl = round(sum(c.pnl_eur for c in closes) - sum(o.fee_eur for o in opens), 4)
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
        "closed_positions": len(closed_rows),
        "closed_operations": len(closed_rows),
        "open_operations": open_ops,
        "wins_total": wins,
        "losses_total": losses,
        "flat_total": flat,
        "win_rate": round(win_rate, 2),
        "avg_pnl_eur": round(total_pnl / len(pnls), 4) if pnls else 0,
        "avg_pnl_pct": avg_pnl_pct,
        "total_pnl_eur": total_pnl,
        "total_fees_eur": total_fees,
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
