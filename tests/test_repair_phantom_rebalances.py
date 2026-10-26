"""Detección de rebalanceos fantasma sobre una BD sintética."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from database.init_db import SessionLocal
from database.models import Trade
from scripts.repair_phantom_rebalances import detect_phantom_rebalances


def _trade(trade_id, cycle_id, reason, pnl=None, fee=0.1):
    return SimpleNamespace(
        id=trade_id,
        cycle_id=cycle_id,
        reason=reason,
        pnl_eur=pnl,
        fee_eur=fee,
        timestamp=datetime(2026, 10, 8, 12, 0) + timedelta(minutes=trade_id),
        pair="BTC/EUR",
        side="BUY" if pnl is None else "SELL",
        amount_crypto=1.0,
        price=100.0,
    )


def test_detecta_solo_cierres_sin_pierna_viva():
    trades = [
        _trade(1, "c1", "grid"),                 # apertura
        _trade(2, "c1", "grid", pnl=1.0),        # cierre
        _trade(3, "c1", "rebalance", pnl=0.5),   # fantasma (prev=2, par)
        _trade(4, "c2", "grid"),                 # apertura
        _trade(5, "c2", "rebalance", pnl=-1.0),  # legítima (prev=1, impar)
        _trade(6, "c3", "grid"),                 # apertura
        _trade(7, "c3", "grid", pnl=1.0),        # cierre
        _trade(8, "c3", "grid"),                 # apertura
        _trade(9, "c3", "rebalance", pnl=0.2),   # legítima (prev=3, impar)
    ]

    assert [t.id for t in detect_phantom_rebalances(trades)] == [3]


def test_detecta_sobre_filas_reales_de_sqlite():
    with SessionLocal() as db:
        rows = [
            Trade(pair="BTC/EUR", side="SELL", amount_crypto=1.0, amount_eur=100.0,
                  price=100.0, fee_eur=0.1, pnl_eur=None, mode="demo",
                  cycle_id="c1", reason="grid", timestamp=datetime(2026, 10, 8, 12, 0)),
            Trade(pair="BTC/EUR", side="BUY", amount_crypto=1.0, amount_eur=99.0,
                  price=99.0, fee_eur=0.1, pnl_eur=0.9, mode="demo",
                  cycle_id="c1", reason="grid", timestamp=datetime(2026, 10, 8, 12, 1)),
            Trade(pair="BTC/EUR", side="SELL", amount_crypto=1.0, amount_eur=101.0,
                  price=101.0, fee_eur=0.1, pnl_eur=-1.0, mode="demo",
                  cycle_id="c1", reason="rebalance", timestamp=datetime(2026, 10, 8, 12, 2)),
            Trade(pair="BTC/EUR", side="BUY", amount_crypto=1.0, amount_eur=100.0,
                  price=100.0, fee_eur=0.1, pnl_eur=None, mode="demo",
                  cycle_id="c2", reason="grid", timestamp=datetime(2026, 10, 8, 12, 3)),
            Trade(pair="BTC/EUR", side="SELL", amount_crypto=1.0, amount_eur=102.0,
                  price=102.0, fee_eur=0.1, pnl_eur=-2.0, mode="demo",
                  cycle_id="c2", reason="rebalance", timestamp=datetime(2026, 10, 8, 12, 4)),
        ]
        db.add_all(rows)
        db.commit()

        loaded = db.query(Trade).order_by(Trade.id.asc()).all()
        phantoms = detect_phantom_rebalances(loaded)

    assert len(phantoms) == 1
    assert phantoms[0].cycle_id == "c1"
    assert phantoms[0].reason == "rebalance"
