"""Backfill del PnL de posiciones cerradas desde la tabla trades.

Corrige posiciones cuyo pnl_eur/pnl_pct fue calculado sin descontar
el fee de cierre (bug en crud.close_position antes del fix).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from database.init_db import SessionLocal
from database.models import Position, Trade


def recompute_position_pnl(position) -> tuple:
    """Recomputa pnl_eur y pnl_pct de una posición desde sus trades."""
    trades = (
        SessionLocal()
        .query(Trade)
        .filter(Trade.position_id == position.id)
        .order_by(Trade.timestamp)
        .all()
    )
    if not trades:
        return None, None

    pos_type = getattr(position, "position_type", "long")

    if pos_type == "short":
        entries = [t for t in trades if t.side == "short"]
        closes = [t for t in trades if t.side == "buy_to_close"]
        if not entries:
            return None, None
        entry = entries[0]
        invested = entry.amount_eur - entry.fee_eur
        cost = sum(t.amount_eur + t.fee_eur for t in closes)
        pnl_eur = invested - cost
    else:
        entries = [t for t in trades if t.side == "buy"]
        closes = [t for t in trades if t.side == "sell"]
        if not entries:
            return None, None
        entry = entries[0]
        invested = entry.amount_eur - entry.fee_eur
        proceeds = sum(t.amount_eur - t.fee_eur for t in closes)
        pnl_eur = proceeds - invested

    pnl_pct = pnl_eur / invested * 100 if invested else 0.0
    return pnl_eur, pnl_pct


def main() -> None:
    db = SessionLocal()
    closed = db.query(Position).filter(Position.status == "closed").all()
    updated = 0
    for pos in closed:
        pnl_eur, pnl_pct = recompute_position_pnl(pos)
        if pnl_eur is None:
            continue
        pos.pnl_eur = pnl_eur
        pos.pnl_pct = pnl_pct
        db.commit()
        updated += 1
        print(
            f"#{pos.id} {pos.pair} {getattr(pos, 'position_type', 'long'):5s} "
            f"pnl_eur={pnl_eur:+.4f} pnl_pct={pnl_pct:+.2f}%"
        )
    db.close()
    print(f"Actualizadas {updated} posiciones de {len(closed)} cerradas")


if __name__ == "__main__":
    main()
