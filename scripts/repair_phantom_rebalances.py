#!/usr/bin/env python3
"""Detecta y borra trades `rebalance` fantasma (liquidaciones de aperturas resting).

Una fila es fantasma si, dentro de su `cycle_id`, el número de trades anteriores
por `id` es par: el ciclo estaba plano y la orden liquidada era una apertura que
nunca se ejecutó. Si es impar, había una pierna viva y la fila es legítima.

Uso (con el bot PARADO):
    python scripts/repair_phantom_rebalances.py            # dry-run
    python scripts/repair_phantom_rebalances.py --apply    # backup + CSV + borrado
    python scripts/repair_phantom_rebalances.py --db data/crypto_trader.db
"""
import argparse
import csv
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))


def detect_phantom_rebalances(trades) -> list:
    """Devuelve las filas `rebalance` fantasma de una lista ordenable por id."""
    previous_by_cycle: dict = defaultdict(int)
    phantoms = []
    for trade in sorted(trades, key=lambda t: t.id):
        cycle_id = getattr(trade, "cycle_id", None)
        if not cycle_id:
            continue
        if (getattr(trade, "reason", None) or "grid") == "rebalance":
            if previous_by_cycle[cycle_id] % 2 == 0:
                phantoms.append(trade)
        previous_by_cycle[cycle_id] += 1
    return phantoms


def _print_report(phantoms) -> None:
    if not phantoms:
        print("No se han detectado trades rebalance fantasma.")
        return

    print(f"{'ID':>6}  {'FECHA':<19}  {'PAR':<10}  {'LADO':<5}  {'PNL':>10}  {'FEE':>8}")
    print("-" * 70)
    totals_by_date: dict = defaultdict(lambda: [0, 0.0])
    for trade in phantoms:
        ts = trade.timestamp.isoformat() if trade.timestamp else ""
        date_key = trade.timestamp.date().isoformat() if trade.timestamp else "?"
        pnl = float(trade.pnl_eur or 0.0)
        totals_by_date[date_key][0] += 1
        totals_by_date[date_key][1] += pnl
        print(
            f"{trade.id:>6}  {ts:<19}  {trade.pair:<10}  {trade.side:<5}  "
            f"{pnl:>10.4f}  {float(trade.fee_eur or 0.0):>8.4f}"
        )

    print("-" * 70)
    for date_key, (count, pnl) in sorted(totals_by_date.items()):
        print(f"{date_key}: {count} filas, PnL {pnl:+.4f} EUR")
    print(
        f"TOTAL: {len(phantoms)} filas, PnL "
        f"{sum(float(t.pnl_eur or 0.0) for t in phantoms):+.4f} EUR"
    )


def _backup_and_export(db_path: str, phantoms) -> tuple[str, str]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{db_path}.bak-{stamp}"
    csv_path = os.path.join(os.path.dirname(db_path) or ".", f"phantom_rebalances_{stamp}.csv")

    shutil.copy2(db_path, backup_path)
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "id", "timestamp", "pair", "side", "amount_crypto", "price",
            "pnl_eur", "fee_eur", "cycle_id",
        ])
        for trade in phantoms:
            writer.writerow([
                trade.id,
                trade.timestamp.isoformat() if trade.timestamp else "",
                trade.pair,
                trade.side,
                trade.amount_crypto,
                trade.price,
                trade.pnl_eur,
                trade.fee_eur,
                trade.cycle_id,
            ])
    return backup_path, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica el borrado (sin este flag solo muestra el informe)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fuerza el modo informe (comportamiento por defecto)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Ruta a la SQLite (por defecto SQLITE_DB_PATH de la config)",
    )
    args = parser.parse_args()
    dry_run = args.dry_run or not args.apply

    from config import config

    if args.db:
        config.database.sqlite_path = args.db

    from database.init_db import SessionLocal
    from database.models import Trade

    with SessionLocal() as db:
        trades = db.query(Trade).order_by(Trade.id.asc()).all()
        phantoms = detect_phantom_rebalances(trades)
        _print_report(phantoms)

        if dry_run or not phantoms:
            if phantoms:
                print("\n[dry-run] Nada modificado. Usa --apply para borrar.")
            return 0

        db_path = os.path.abspath(args.db or config.database.sqlite_path)
        backup_path, csv_path = _backup_and_export(db_path, phantoms)
        ids = [trade.id for trade in phantoms]
        deleted = (
            db.query(Trade)
            .filter(Trade.id.in_(ids))
            .delete(synchronize_session=False)
        )
        db.commit()

    print(f"\nBorradas {deleted} filas.")
    print(f"Backup: {backup_path}")
    print(f"CSV:    {csv_path}")
    print("Reinicia el bot para que reconstruya el balance y el PnL desde el ledger.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
