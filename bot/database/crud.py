"""Operaciones CRUD sobre la base de datos."""
import json
import os
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from config import config
from .models import Candle, PortfolioSnapshot, Position, Trade, ConfigHistory, RebalanceEvent
from trading.accounting import trade_ledger_delta, trade_margin_fee


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


def get_open_positions(db: Session) -> list[Position]:
    return db.query(Position).filter_by(status="open").all()


def create_trade(db: Session, trade_data: dict) -> Trade:
    trade = Trade(**trade_data)
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return trade


def create_config_history(db: Session, data: dict) -> ConfigHistory:
    obj = ConfigHistory(**data)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def get_config_history(db: Session, limit: int = 50) -> list[ConfigHistory]:
    return (
        db.query(ConfigHistory)
        .order_by(ConfigHistory.ts.desc())
        .limit(limit)
        .all()
    )


def create_rebalance_event(db: Session, data: dict) -> RebalanceEvent:
    obj = RebalanceEvent(**data)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def get_rebalance_events(db: Session, limit: int = 50) -> list[RebalanceEvent]:
    return (
        db.query(RebalanceEvent)
        .order_by(RebalanceEvent.ts.desc())
        .limit(limit)
        .all()
    )


def get_trades(db: Session, limit: int = 50, offset: int = 0) -> list[Trade]:
    return (
        db.query(Trade)
        .order_by(desc(Trade.timestamp))
        .offset(offset)
        .limit(limit)
        .all()
    )


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

    Cada cierre se empareja primero por `open_trade_id` (vinculo exacto escrito
    al crear la contra-orden). Para filas antiguas sin vinculo se usa el
    entry_price guardado en el cierre (fiable) o, si falta, se deriva del PnL
    almacenado; y se busca en el pool de aperturas sin usar la del par y precio
    mas cercano (tolerancia 0.5%). Ninguna apertura se empareja dos veces.

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
    opens_pool: list[Trade] = []
    opens_by_key: dict[tuple[str, str], list[Trade]] = {}
    used_objects: set[int] = set()
    by_id = {t.id: t for t in sorted_trades if t.id is not None}
    fill_history: list[Trade] = []
    closed_rows: list[dict] = []
    open_rows: list[dict] = []

    for t in sorted_trades:
        if t.pnl_eur is None:
            opens_pool.append(t)
            opens_by_key.setdefault((t.pair, t.side.upper()), []).append(t)
            fill_history.append(t)
            continue

        hint = getattr(t, "entry_price", None)
        derived = hint if hint is not None else _derive_entry(t.side, t.price, t.pnl_eur, t.fee_eur, t.amount_crypto)
        linked_id = getattr(t, "open_trade_id", None)
        linked = by_id.get(linked_id) if linked_id is not None else None
        op = None
        if (
            linked is not None
            and linked.pnl_eur is None
            and id(linked) not in used_objects
            and linked.pair == t.pair
            and linked.side.upper() != t.side.upper()
        ):
            op = linked
        else:
            opposite = "BUY" if t.side.upper() == "SELL" else "SELL"
            best_diff = TOL
            for candidate in opens_by_key.get((t.pair, opposite), []):
                if id(candidate) in used_objects:
                    continue
                diff = abs(derived - candidate.price) / max(candidate.price, 1e-8)
                if diff < best_diff:
                    best_diff = diff
                    op = candidate

        if op is not None:
            used_objects.add(id(op))
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

    for op in opens_pool:
        if id(op) not in used_objects:
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
    Ordenadas por fecha de cierre (exit_timestamp) descendente; los ciclos aún
    abiertos caen arriba por su fecha de apertura.
    """
    trades = (
        db.query(Trade)
        .order_by(Trade.timestamp.asc())
        .all()
    )
    closed_rows, open_rows = _build_round_trips(trades)
    rows = sorted(
        closed_rows + open_rows,
        key=lambda r: r["exit_timestamp"] or r["entry_timestamp"],
        reverse=True,
    )
    return rows[offset:offset + limit]


def get_recent_operations(db: Session, limit: int = 8) -> list[dict]:
    """Devuelve las operaciones del grid mas recientes (ya agrupadas por ciclo)."""
    return get_operations(db, limit, 0)


def get_stats_summary(db: Session) -> dict:
    """Estadisticas derivadas de las operaciones reales del grid.

    El PnL total se computa DIRECTAMENTE desde las piernas (suma de pnl de
    cierres menos fees y comisiones de margen de aperturas), por lo que cuadra
    SIEMPRE con el balance (100 + total_pnl_eur = balance real) independiente
    del emparejamiento. Las comisiones totales incluyen todas las piernas
    (aperturas y cierres); las de margen se reportan aparte.
    """
    tz = ZoneInfo(os.getenv("DISPLAY_TIMEZONE", "UTC"))
    today = (
        datetime.now(tz)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(ZoneInfo("UTC"))
        .replace(tzinfo=None)
    )

    trades = db.query(Trade).order_by(Trade.timestamp.asc()).all()
    closed_rows, open_rows = _build_round_trips(trades)

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
    # resta su fee y su comision de margen. Cuadra SIEMPRE con el balance,
    # independiente del emparejamiento de filas de _build_round_trips.
    total_margin_fees = round(
        sum(
            trade_margin_fee(
                t.side,
                t.pnl_eur,
                t.margin_fee_eur,
                t.amount_crypto,
                t.price,
                config.grid.margin_open_fee_pct,
            )
            for t in trades
        ),
        4,
    )
    total_pnl = round(
        sum(
            trade_ledger_delta(
                t.side,
                t.pnl_eur,
                t.fee_eur,
                t.margin_fee_eur,
                t.amount_crypto,
                t.price,
                config.grid.margin_open_fee_pct,
            )
            for t in trades
        ),
        4,
    )
    win_rate = (wins / len(pnls) * 100) if pnls else 0
    avg_pnl_pct = round(sum(pnl_pcts) / len(pnl_pcts), 2) if pnl_pcts else 0.0

    today_wins = sum(1 for p in today_pnls if p > 0)
    today_losses = sum(1 for p in today_pnls if p < 0)

    best_trade = max(pnls) if pnls else 0
    worst_trade = min(pnls) if pnls else 0
    max_drawdown = calculate_max_drawdown_from_snapshots(db)

    rebalance_pnl = round(
        sum(r["pnl_eur"] for r in closed_rows if r.get("exit_reason") == "rebalance"), 4
    )
    grid_close_pnl = round(
        sum(r["pnl_eur"] for r in closed_rows if r.get("exit_reason") == "grid"), 4
    )
    rebalance_drag_pct = (
        round(-rebalance_pnl / grid_close_pnl * 100, 2) if grid_close_pnl else 0.0
    )

    max_open_notional = 0.0
    max_open_notional_pct = 0.0
    for snap in db.query(PortfolioSnapshot).all():
        try:
            positions = json.loads(snap.positions_json or "{}")
        except (TypeError, ValueError):
            positions = {}
        notional = 0.0
        for pos in positions.values():
            notional += float(pos.get("current_value_eur") or 0.0)
        if notional > max_open_notional:
            max_open_notional = notional
            if snap.total_value_eur:
                max_open_notional_pct = notional / snap.total_value_eur * 100

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
        "total_margin_fees_eur": total_margin_fees,
        "rebalance_drag_pct": rebalance_drag_pct,
        "max_open_notional_eur": round(max_open_notional, 4),
        "max_open_notional_pct": round(max_open_notional_pct, 2),
        "equity_max_drawdown_pct": max_drawdown,
        "trades_today": len(today_pnls),
        "today_operations": len(today_pnls),
        "today_closed": len(today_pnls),
        "wins_today": today_wins,
        "losses_today": today_losses,
        "best_trade": round(best_trade, 4),
        "worst_trade": round(worst_trade, 4),
        "max_drawdown": max_drawdown,
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


def reset_portfolio_data(db: Session) -> dict:
    """Resetea el historial del portfolio (snapshots, trades, posiciones).
    Mantiene posiciones abiertas y el balance en Redis.

    Solo permitido en modo demo: en modo real, los reseteos del ledger estan
    prohibidos. Tras el reset, el siguiente arranque reconstruye el balance
    desde el ledger vacio (vuelve al balance inicial).
    """
    if not config.trading.is_demo():
        raise PermissionError("Los reseteos de ledger solo estan permitidos en modo demo")
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
    if not config.trading.is_demo():
        raise PermissionError("Los reseteos de ledger solo estan permitidos en modo demo")
    deleted_snapshots = db.query(PortfolioSnapshot).delete()
    deleted_trades = db.query(Trade).delete()
    deleted_positions = db.query(Position).delete()
    db.commit()
    return {
        "snapshots_deleted": deleted_snapshots,
        "trades_deleted": deleted_trades,
        "positions_deleted": deleted_positions,
    }
