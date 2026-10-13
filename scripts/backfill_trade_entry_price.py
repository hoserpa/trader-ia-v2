#!/usr/bin/env python3
"""Rellena trades.entry_price en los cierres históricos.

Los cierres de shorts incluyen el rollover de margen en su pnl_eur, algo que
`_derive_entry` (bot/database/crud.py) no contempla: para shorts largos la
entrada derivada se desvía >0.5% y la apertura real queda como fila "open"
fantasma en el dashboard.

Este script empareja cada cierre con su apertura real usando el invariante del
grid: el contra-nivel hereda el importe del padre, así que la apertura del
cierre tiene el mismo `amount_crypto`, par y lado opuesto, y es la más reciente
sin consumir. Reejecutarlo es idempotente (solo rellena los NULL).

No toca pnl_eur ni el balance; solo rellena entry_price.

Uso (con el entorno del proyecto, p.ej. dentro del contenedor api):
    python scripts/backfill_trade_entry_price.py --dry-run
    python scripts/backfill_trade_entry_price.py
    python scripts/backfill_trade_entry_price.py --pair SOL/EUR
"""
import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))

AMOUNT_TOL = 1e-9


def backfill(db, Trade, pair_filter: str | None, dry_run: bool) -> int:
    trades = (
        db.query(Trade)
        .order_by(Trade.timestamp.asc(), Trade.id.asc())
        .all()
    )
    opens: dict[str, list] = defaultdict(list)
    used: set[int] = set()
    changed = 0

    for t in trades:
        if pair_filter and t.pair != pair_filter:
            continue
        if t.pnl_eur is None:
            opens[t.pair].append(t)
            continue

        match = None
        for op in reversed(opens[t.pair]):
            if id(op) in used or op.side.upper() == t.side.upper():
                continue
            if abs(op.amount_crypto - t.amount_crypto) > AMOUNT_TOL:
                continue
            match = op
            break
        if match is None:
            continue

        used.add(id(match))
        if t.entry_price is None:
            t.entry_price = match.price
            changed += 1
            print(f"id {t.id} {t.pair} {t.side} {t.amount_crypto} -> entry {match.price}")

    if not dry_run:
        db.commit()
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", default=None, help="Filtrar por un par, p.ej. SOL/EUR")
    parser.add_argument("--dry-run", action="store_true", help="No escribe en la BD")
    args = parser.parse_args()

    from database.init_db import SessionLocal
    from database.models import Trade

    with SessionLocal() as db:
        changed = backfill(db, Trade, args.pair, args.dry_run)

    print(f"{'[dry-run] ' if args.dry_run else ''}entry_price rellenado en {changed} cierres")


if __name__ == "__main__":
    main()
