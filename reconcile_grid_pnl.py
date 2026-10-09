#!/usr/bin/env python3
"""Reconcilia el PnL acreditado por el grid (BD real) contra el PnL real
calculado de forma independiente a partir de los trades, para saber cuánto
del PnL reportado es artefacto del bug de doble contabilización en
GridStrategy._handle_fill (is_close = isinstance(id, str)).

No modifica nada. Solo lee la tabla trades y, opcionalmente, los precios
actuales desde Redis para valorar el inventario final.

Uso (desde la raíz del repo, con el entorno del proyecto activo):
    python reconcile_grid_pnl.py
    python reconcile_grid_pnl.py --pair SOL/EUR
    python reconcile_grid_pnl.py --csv trades_export.csv   # sin tocar la BD real

Qué calcula, por par y en total:
    - suma de pnl_eur ya acreditado (lo que el bot dice haber ganado)
    - PnL real: flujo de caja neto de los fills (BUY resta, SELL suma,
      menos fees) + valor del inventario neto final marcado al último precio
    - la diferencia entre ambos = artefacto del bug (aprox.)

Nota: esto es una cota, no un valor exacto. El inventario final se valora al
último precio conocido, así que si hay tendencia direccional fuerte en ese
tramo el resultado tiene ruido; para una cifra más limpia, ejecútalo con un
--as-of anterior al periodo con movimiento fuerte, o interpreta el resultado
junto al informe de la Fase 0 (investigación de 105,84€).
"""
import argparse
import csv
import sys
from collections import defaultdict


def load_trades_from_csv(path: str) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append({
                "pair": row["pair"],
                "side": row["side"].upper(),
                "amount_crypto": float(row["amount_crypto"]),
                "price": float(row["price"]),
                "fee_eur": float(row["fee_eur"]),
                "pnl_eur": float(row["pnl_eur"]) if row.get("pnl_eur") not in (None, "", "None") else None,
                "timestamp": row.get("timestamp", ""),
            })
    return rows


def load_trades_from_db(pair_filter: str | None) -> list[dict]:
    """Importa el modelo real del proyecto. Debe ejecutarse con el mismo
    PYTHONPATH/entorno que la app (para que 'database' se resuelva)."""
    try:
        from database.init_db import SessionLocal
        from database.models import Trade
    except ImportError as e:
        print(
            "No se pudo importar 'database.init_db'/'database.models'. "
            "Ejecuta este script desde la raíz del repo con el entorno del "
            "proyecto activo, o usa --csv para exportar la tabla trades "
            "manualmente (pair, side, amount_crypto, price, fee_eur, pnl_eur, timestamp).",
            file=sys.stderr,
        )
        raise SystemExit(1) from e

    rows = []
    with SessionLocal() as db:
        q = db.query(Trade)
        if pair_filter:
            q = q.filter(Trade.pair == pair_filter)
        for t in q.order_by(Trade.id.asc()).all():
            rows.append({
                "pair": t.pair,
                "side": t.side.upper(),
                "amount_crypto": float(t.amount_crypto),
                "price": float(t.price),
                "fee_eur": float(t.fee_eur),
                "pnl_eur": float(t.pnl_eur) if t.pnl_eur is not None else None,
                "timestamp": str(getattr(t, "timestamp", getattr(t, "created_at", ""))),
            })
    return rows


def get_last_prices(pairs: set[str]) -> dict[str, float]:
    """Intenta leer el último precio conocido de cada par desde Redis. Si no
    hay conexión disponible, usa el precio del último fill de ese par como
    aproximación (se avisa explícitamente)."""
    prices: dict[str, float] = {}
    try:
        import redis as redis_lib  # type: ignore
        r = redis_lib.Redis()
        for pair in pairs:
            raw = r.get(f"price:{pair}")
            if raw:
                prices[pair] = float(raw)
    except Exception:
        pass
    return prices


def reconcile(trades: list[dict], last_prices: dict[str, float]) -> None:
    by_pair: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_pair[t["pair"]].append(t)

    print(
        f"{'PAR':<10} {'fills':>6} {'PnL_acreditado':>15} {'PnL_real(MTM)':>15} "
        f"{'diferencia':>12} {'ratio':>7} {'inventario_final':>17}"
    )
    print("-" * 90)

    total_booked = 0.0
    total_real = 0.0

    for pair, pair_trades in sorted(by_pair.items()):
        booked = sum(t["pnl_eur"] for t in pair_trades if t["pnl_eur"] is not None)

        cash = 0.0
        position = 0.0
        fees = 0.0
        for t in pair_trades:
            sign = 1 if t["side"] == "SELL" else -1
            cash += sign * t["amount_crypto"] * t["price"]
            position -= sign * t["amount_crypto"]
            fees += t["fee_eur"]

        last_price = last_prices.get(pair)
        price_source = "redis"
        if last_price is None:
            last_price = pair_trades[-1]["price"]
            price_source = "último fill (aprox.)"

        inventory_value = position * last_price
        real_pnl = cash - fees + inventory_value

        ratio = booked / real_pnl if real_pnl else float("inf")
        print(
            f"{pair:<10} {len(pair_trades):>6} {booked:>15.4f} {real_pnl:>15.4f} "
            f"{booked - real_pnl:>12.4f} {ratio:>6.2f}x {inventory_value:>16.2f} "
            f"({price_source})"
        )

        total_booked += booked
        total_real += real_pnl

    print("-" * 90)
    ratio_total = total_booked / total_real if total_real else float("inf")
    print(
        f"{'TOTAL':<10} {len(trades):>6} {total_booked:>15.4f} {total_real:>15.4f} "
        f"{total_booked - total_real:>12.4f} {ratio_total:>6.2f}x"
    )
    print()
    print(
        "PnL_real(MTM) = flujo de caja neto de los fills (BUY resta, SELL suma, "
        "menos fees) + inventario neto final marcado al último precio.\n"
        "Si el ratio es notablemente > 1, esa diferencia es, con alta probabilidad, "
        "artefacto del bug de doble contabilización en _handle_fill (is_close = "
        "isinstance(id, str)), no beneficio real."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", default=None, help="Filtrar por un par, p.ej. SOL/EUR")
    parser.add_argument(
        "--csv", default=None,
        help="Ruta a un CSV exportado de la tabla trades, en vez de leer la BD directamente "
             "(columnas: pair, side, amount_crypto, price, fee_eur, pnl_eur, timestamp)",
    )
    args = parser.parse_args()

    if args.csv:
        trades = load_trades_from_csv(args.csv)
        if args.pair:
            trades = [t for t in trades if t["pair"] == args.pair]
    else:
        trades = load_trades_from_db(args.pair)

    if not trades:
        print("No se encontraron trades con esos filtros.", file=sys.stderr)
        raise SystemExit(1)

    pairs = {t["pair"] for t in trades}
    last_prices = get_last_prices(pairs)
    reconcile(trades, last_prices)


if __name__ == "__main__":
    main()
