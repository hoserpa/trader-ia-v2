"""Regresión: _build_round_trips debe emparejar un cierre con su apertura real
usando el entry_price guardado, aunque el pnl del cierre esté desplazado por el
rollover de margen del short (que _derive_entry no contempla).

Caso real: SOL/EUR open SELL 106.81 (03-oct) y close BUY 105.37 (07-oct) con
pnl=0.0551 (incluye ~0.0402€ de rollover). La entrada derivada sin rollover da
106.27 -> 0.502% de desvío > TOL 0.5% -> sin entry_price la apertura queda como
fila "open" fantasma. Con entry_price (columna nueva) empareja de forma exacta.

Uso:
    pytest test_entry_price_matching.py -v
"""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot"))

_BOT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot", "database")


def _matcher():
    """Importa el _build_round_trips real sin ejecutar database/__init__.py
    (crea la BD) ni heredar stubs que otros tests dejan en sys.modules."""
    for m in ("database", "database.crud", "database.models"):
        sys.modules.pop(m, None)
    sa = sys.modules.get("sqlalchemy")
    if sa is not None and not hasattr(sa, "__version__"):  # stub, no real
        for m in [k for k in list(sys.modules) if k == "sqlalchemy" or k.startswith("sqlalchemy.")]:
            del sys.modules[m]
    import types

    pkg = types.ModuleType("database")
    pkg.__path__ = [_BOT_DB]
    sys.modules["database"] = pkg
    from database.crud import _build_round_trips

    return _build_round_trips


def _trade(side, price, amount, pnl, fee, ts, entry_price=None, cycle="c1", tid=1):
    return NS(
        id=tid, pair="SOL/EUR", side=side, price=price, amount_crypto=amount,
        amount_eur=round(price * amount, 4), fee_eur=fee, pnl_eur=pnl,
        entry_price=entry_price, cycle_id=cycle, mode="demo", reason="grid",
        timestamp=ts, position_id=None,
    )


def test_short_close_with_rollover_matches_stored_entry():
    _build_round_trips = _matcher()

    t0 = datetime(2026, 10, 3, 17, 44)
    amount = 0.07494205
    open_t = _trade("SELL", 106.81, amount, None, 0.0126, t0, tid=1)
    close_t = _trade(
        "BUY", 105.37, amount, 0.0551, 0.0126,
        t0 + timedelta(hours=80.27), entry_price=106.81, tid=2,
    )

    closed, opens = _build_round_trips([open_t, close_t])

    assert opens == [], f"la apertura quedó sin emparejar: {opens}"
    assert len(closed) == 1
    row = closed[0]
    assert row["entry_price"] == 106.81
    assert row["side"] == "SELL"
    assert row["status"] == "closed"


def test_legacy_close_without_entry_price_still_derives():
    _build_round_trips = _matcher()

    t0 = datetime(2026, 10, 1, 0, 0)
    amount = 0.075
    open_t = _trade("SELL", 100.0, amount, None, 0.0126, t0, tid=1)
    close_t = _trade("BUY", 99.0, amount, 0.0724, 0.0126, t0 + timedelta(hours=1), tid=2)

    closed, opens = _build_round_trips([open_t, close_t])

    assert opens == []
    assert closed[0]["entry_price"] == 100.0


def _load_backfill():
    import importlib.util

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "backfill_trade_entry_price.py")
    spec = importlib.util.spec_from_file_location("backfill_trade_entry_price", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mem_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import database.models as models

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    models.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), models.Trade


def test_backfill_fills_entry_price_on_closes():
    _matcher()
    mod = _load_backfill()
    db, Trade = _mem_session()

    t0 = datetime(2026, 10, 3, 17, 44)
    db.add(Trade(pair="SOL/EUR", side="SELL", amount_crypto=0.07494205, amount_eur=8.0,
                 price=106.81, fee_eur=0.0126, pnl_eur=None, mode="demo",
                 cycle_id="c1", reason="grid", timestamp=t0))
    db.add(Trade(pair="SOL/EUR", side="BUY", amount_crypto=0.07494205, amount_eur=7.9,
                 price=105.37, fee_eur=0.0126, pnl_eur=0.0551, mode="demo",
                 cycle_id="c1", reason="grid", timestamp=t0 + timedelta(hours=80)))
    db.commit()

    changed = mod.backfill(db, Trade, None, dry_run=False)
    assert changed == 1

    close = db.query(Trade).filter_by(side="BUY").one()
    assert close.entry_price == 106.81

    assert mod.backfill(db, Trade, None, dry_run=False) == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
