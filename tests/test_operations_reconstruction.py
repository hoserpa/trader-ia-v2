"""Verificacion de la reconstruccion de operaciones (idas y vueltas).

El grid registra pierna a pierna: apertura (pnl_eur=NULL, fee pagada) y
cierres (pnl_eur = delta - fee_cierre). Todas las piernas de un nivel comparten
cycle_id. La reconstruccion debe devolver UNA fila por ida y vuelta real con:

  - entrada real de la pierna que abrio, salida del cierre
  - pnl neto que cuadra con el balance (suma de filas == PnL neto del ciclo)
  - total_fees sin contar dos veces la comision intermedia del ciclo

Reglas:
  - primera ida y vuelta: pnl = delta - fee_apertura - fee_cierre
  - siguientes (flips):  pnl = delta - fee_cierre (el flip es apertura+cierre
    del mismo fill, su comision ya quedo en la fila anterior)
  - suma(filas.pnl) == -fee_apertura + suma(cierres.pnl_eur)   (PnL del ciclo)
  - suma(filas.total_fees) == suma(fee_eur de todas las piernas)
"""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))


class _Leg:
    def __init__(self, side, price, amount, fee, pnl, cycle, ts, pair="SOL/EUR"):
        self.id = 1
        self.pair = pair
        self.side = side
        self.price = price
        self.amount_crypto = amount
        self.amount_eur = round(price * amount, 4)
        self.fee_eur = fee
        self.pnl_eur = pnl
        self.timestamp = ts
        self.mode = "demo"
        self.cycle_id = cycle
        self.position_id = None


def _t(side, price, amount, fee, pnl, cycle, day):
    return _Leg(side, price, amount, fee, pnl, cycle, datetime(2026, 9, day, 12, 0))


def _delta(opening_side, entry, exit, qty):
    if opening_side.upper() == "SELL":
        return (entry - exit) * qty
    return (exit - entry) * qty


def _close_stored(opening_side, entry, exit, qty, fee):
    """Como lo registra _record_fill en el cierre: delta - fee_cierre."""
    return round(_delta(opening_side, entry, exit, qty) - fee, 4)


def _cycle(open_leg, closes):
    return [open_leg] + closes


def test_multi_trip_cycle_cuadra_con_balance():
    from database.crud import _build_round_trips

    qty = 0.11439993
    open_leg = _t("BUY", 87.14, qty, 0.016, None, "c1", 1)
    closes = [
        _t("SELL", 90.40, qty, 0.0165, _close_stored("BUY", 87.14, 90.40, qty, 0.0165), "c1", 2),
        _t("BUY", 87.28, qty, 0.016, _close_stored("SELL", 90.40, 87.28, qty, 0.016), "c1", 3),
        _t("SELL", 84.26, qty, 0.0166, _close_stored("BUY", 87.28, 84.26, qty, 0.0166), "c1", 4),
    ]
    closed, open_rows = _build_round_trips(_cycle(open_leg, closes))
    assert len(open_rows) == 0
    assert len(closed) == 3, "una fila por ida y vuelta"

    r1, r2, r3 = closed
    assert (r1["side"], r1["entry_price"], r1["exit_price"]) == ("BUY", 87.14, 90.40)
    assert (r2["side"], r2["entry_price"], r2["exit_price"]) == ("SELL", 90.40, 87.28)
    assert (r3["side"], r3["entry_price"], r3["exit_price"]) == ("BUY", 87.28, 84.26)

    assert r1["pnl_eur"] == round(_delta("BUY", 87.14, 90.40, qty) - 0.016 - 0.0165, 4)
    assert r2["pnl_eur"] == round(_delta("SELL", 90.40, 87.28, qty) - 0.016, 4)
    assert r3["pnl_eur"] == round(_delta("BUY", 87.28, 84.26, qty) - 0.0166, 4)

    grid_pnl = -open_leg.fee_eur + round(sum(c.pnl_eur for c in closes), 4)
    assert round(sum(r["pnl_eur"] for r in closed), 4) == round(grid_pnl, 4)
    total_fees = round(sum(r["total_fees"] for r in closed), 4)
    assert total_fees == round(open_leg.fee_eur + sum(c.fee_eur for c in closes), 4)


def test_liquidacion_mismo_precio_resta_dos_comisiones():
    from database.crud import _build_round_trips

    qty = 0.05571198
    open_leg = _t("SELL", 91.04, qty, 0.0081, None, "liq", 1)
    closes = [_t("BUY", 91.04, qty, 0.0081, -0.0081, "liq", 5)]
    closed, _ = _build_round_trips(_cycle(open_leg, closes))
    assert len(closed) == 1
    assert closed[0]["pnl_eur"] == -0.0162
    assert closed[0]["total_fees"] == 0.0162


def test_ciclo_abierto_devuelve_fila_abierta():
    from database.crud import _build_round_trips

    qty = 0.05501027
    open_leg = _t("SELL", 92.53, qty, 0.0081, None, "open1", 1)
    closed, open_rows = _build_round_trips(_cycle(open_leg, []))
    assert closed == []
    assert len(open_rows) == 1
    assert open_rows[0]["status"] == "open"
    assert open_rows[0]["pnl_eur"] is None
    assert open_rows[0]["amount_eur_entry"] == round(92.53 * qty, 4)
    assert open_rows[0]["total_fees"] == 0.0081


def test_cortos_y_largos_alternando_cuadran():
    from database.crud import _build_round_trips

    qty = 0.10900941
    open_leg = _t("SELL", 91.82, qty, 0.016, None, "c2", 2)
    closes = [_t("BUY", 90.10, qty, 0.0157, _close_stored("SELL", 91.82, 90.10, qty, 0.0157), "c2", 3)]
    closed, _ = _build_round_trips(_cycle(open_leg, closes))
    assert len(closed) == 1
    expected = round(_delta("SELL", 91.82, 90.10, qty) - 0.016 - 0.0157, 4)
    assert closed[0]["pnl_eur"] == expected


def test_interseccion_cerrado_con_abierto_no_rompe():
    from database.crud import _build_round_trips

    qty = 0.05
    a = _t("BUY", 10, qty, 0.01, None, "A", 1)
    b = _t("SELL", 11, qty, 0.01, _close_stored("BUY", 10, 11, qty, 0.01), "A", 2)
    c = _t("SELL", 12, qty, 0.01, None, "B", 3)
    closed = len([1 for r in _build_round_trips([a, b, c])[0] if r])
    assert closed == 1


def test_multicadena_mismo_ciclo_no_mezcla():
    """Un cycle_id puede contener piernas de DOS cadenas distintas (una
    liquidacion/rebalance hereda el cycle_id de su padre). El segundo cierre
    (SELL@95, entry derivada 97) NO debe emparejarse con la apertura BUY@100:
    debe reconstruirse con su entrada derivada y no contaminar la primera fila.
    """
    from database.crud import _build_round_trips

    open_a = _t("BUY", 100, 0.1, 0.01, None, "multi", 1)
    close_a = _t("SELL", 103, 0.1, 0.01, _close_stored("BUY", 100, 103, 0.1, 0.01), "multi", 2)
    close_b = _t("SELL", 95, 0.15, 0.015, _close_stored("BUY", 97, 95, 0.15, 0.015), "multi", 3)

    closed, open_rows = _build_round_trips([open_a, close_a, close_b])
    assert len(closed) == 2
    assert not open_rows

    r1, r2 = closed
    assert (r1["entry_price"], r1["exit_price"], r1["amount_crypto"]) == (100, 103, 0.1)
    assert (r2["entry_price"], r2["exit_price"], r2["amount_crypto"]) == (97, 95, 0.15)
    assert r1["side"] == "BUY" and r2["side"] == "BUY"

    grid_pnl = -open_a.fee_eur + round(sum(c.pnl_eur for c in [close_a, close_b]), 4)
    assert round(sum(r["pnl_eur"] for r in closed), 4) == round(grid_pnl, 4)
    total_fees = round(sum(r["total_fees"] for r in closed), 4)
    assert total_fees == round(open_a.fee_eur + close_a.fee_eur + close_b.fee_eur, 4)


def test_dos_cadenas_mismo_precio_no_doblemente_emparejadas():
    """Dos ciclos con aperturas al mismo precio y cantidades iguales: cada
    cierre debe consumir su propia apertura sin usar la otra dos veces."""
    from database.crud import _build_round_trips

    qty = 0.1
    a = _t("BUY", 100, qty, 0.01, None, "A", 1)
    b = _t("BUY", 100, qty, 0.01, None, "B", 1)
    ca = _t("SELL", 103, qty, 0.01, _close_stored("BUY", 100, 103, qty, 0.01), "A", 2)
    cb = _t("SELL", 104, qty, 0.01, _close_stored("BUY", 100, 104, qty, 0.01), "B", 3)

    closed, open_rows = _build_round_trips([a, b, ca, cb])
    assert len(closed) == 2
    assert not open_rows
    entries = sorted(r["entry_price"] for r in closed)
    exits = sorted(r["exit_price"] for r in closed)
    assert entries == [100.0, 100.0]
    assert exits == [103.0, 104.0]
    assert all(r["entry_fee"] == 0.01 for r in closed)
    assert len({r["id"] for r in closed}) == 1 or all(r["id"] in ("A", "B") for r in closed)


def test_cross_pair_no_contamina():
    """Aperturas de otro par con precio parecido no deben robar el emparejamiento."""
    from database.crud import _build_round_trips

    qty = 0.1
    sol_open = _Leg("BUY", 100, qty, 0.01, None, "SOL1", datetime(2026, 9, 1, 12, 0))
    eth_open = _Leg("SELL", 102, qty, 0.01, None, "ETH1", datetime(2026, 9, 1, 12, 0), pair="ETH/EUR")
    sol_close = _Leg("SELL", 103, qty, 0.01, _close_stored("BUY", 100, 103, qty, 0.01), "SOL1", datetime(2026, 9, 2, 12, 0))

    closed, open_rows = _build_round_trips([sol_open, eth_open, sol_close])
    assert len(closed) == 1
    assert closed[0]["pair"] == "SOL/EUR"
    assert closed[0]["entry_price"] == 100
    assert closed[0]["exit_price"] == 103
    assert closed[0]["entry_fee"] == 0.01
    assert len(open_rows) == 1
    # El SELL@102 de ETH sigue abierto, sin ser consumido por el close SOL
    assert open_rows[0]["pair"] == "ETH/EUR"
    assert open_rows[0]["entry_price"] == 102


def test_doble_cierre_mismo_entry_snap_al_mejor_candidato():
    """Caso real (ciclo 96b30a37): un BUY@90.26 abierto da lugar a DOS cierres
    (SELL@90.10 y SELL@84.26), ambos con entrada derivada 90.26. El segundo
    cierre no encuentre apertura libre y debe snap a la pierna cuyo precio
    MEJOR coincida (90.26), NO al cierre anterior (90.10), para que el PnL de
    la fila cuadre con el almacenado."""
    from database.crud import _build_round_trips

    qty = 0.11067961
    open_leg = _t("BUY", 90.26, qty, 0.016, None, "dup", 1)
    close1 = _t("SELL", 90.10, qty, 0.016, _close_stored("BUY", 90.26, 90.10, qty, 0.016), "dup", 2)
    close2 = _t("SELL", 84.26, qty, 0.0149, _close_stored("BUY", 90.26, 84.26, qty, 0.0149), "dup", 3)

    closed, _ = _build_round_trips([open_leg, close1, close2])
    assert len(closed) == 2
    r1, r2 = closed
    assert (r1["entry_price"], r1["exit_price"]) == (90.26, 90.10)
    assert (r2["entry_price"], r2["exit_price"]) == (90.26, 84.26)
    assert r1["entry_fee"] == 0.016
    assert r2["entry_fee"] == 0.0
    # r2 debe cuadrar con el pnl almacenado: delta - fee_cierre (sin fee apertura,
    # que ya se atribuyo a r1)
    assert r2["pnl_eur"] == round(_delta("BUY", 90.26, 84.26, qty) - 0.0149, 4)
    # suma total == pnl del grid
    grid_pnl = -open_leg.fee_eur + round(close1.pnl_eur + close2.pnl_eur, 4)
    assert round(sum(r["pnl_eur"] for r in closed), 4) == round(grid_pnl, 4)


def test_get_operations_ordena_por_cierre():
    """El historial se ordena por fecha de cierre (los abiertos caen arriba)."""
    from database.crud import _build_round_trips
    qty = 0.1
    legs = [
        _t("BUY", 100.0, qty, 0.016, None, "c_ant", 1),
        _t("SELL", 101.0, qty, 0.016, _close_stored("BUY", 100.0, 101.0, qty, 0.016), "c_ant", 2),
        _t("SELL", 100.0, qty, 0.016, None, "c_ab", 5),
        _t("BUY", 99.0, qty, 0.016, _close_stored("SELL", 100.0, 99.0, qty, 0.016), "c_ab", 7),
        _t("BUY", 102.0, qty, 0.016, None, "c_open", 9),
    ]
    closed, open_rows = _build_round_trips(legs)
    rows = sorted(
        closed + open_rows,
        key=lambda r: r["exit_timestamp"] or r["entry_timestamp"],
        reverse=True,
    )
    # c_open es el unico abierto: sin exit_timestamp, cae arriba por su apertura
    assert rows[0]["id"] == "c_open"
    # los cerrados, por fecha de cierre descendente: c_ab (dia 7) antes que c_ant (dia 2)
    assert [r["id"] for r in rows[1:]] == ["c_ab", "c_ant"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("TODOS OK")