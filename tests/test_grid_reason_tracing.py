"""Test de trazabilidad: cada cierre queda etiquetado por su origen.

La tabla trades no distinguia un cierre ordinario de grid de una liquidacion
por rebalanceo (indistinguibles salvo heuristicas externas). Ahora cada fill
guarda su `reason` ("grid"/"rebalance") y las operaciones reconstruidas
exponen `exit_reason` en cada cierre.
"""
import asyncio
import importlib
import sys

sys.path.insert(0, "bot")

from config import config
from strategies.grid_strategy import GridStrategy


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v, *a, **kw):
        self.store[k] = v

    async def delete(self, k):
        self.store.pop(k, None)

    async def publish(self, channel, msg):
        pass


class FakePortfolio:
    def __init__(self):
        self.balance = 100.0
        self.positions = {}

    def get(self):
        return {
            "balance_eur": self.balance,
            "initial_balance_eur": 100.0,
            "positions": self.positions,
        }

    async def update_balance(self, delta):
        self.balance = round(self.balance + delta, 4)

    async def _save(self, port):
        pass

    async def add_position(self, *a, **kw):
        pass

    async def remove_position(self, *a, **kw):
        pass


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


LEVEL = {"id": "0_sell_0", "side": "sell", "amount": 1.0,
         "entry_price": 100.0, "cycle_id": "c1", "price": 100.0}


def _load_db(monkeypatch, tmp_path):
    monkeypatch.setattr(config.database, "sqlite_path", str(tmp_path / "r.db"))
    db_init = importlib.import_module("database.init_db")
    db_crud = importlib.import_module("database.crud")
    return db_init, db_crud


def test_persist_grid_fill_guarda_reason(monkeypatch, tmp_path):
    db_init, db_crud = _load_db(monkeypatch, tmp_path)
    captured = []
    monkeypatch.setattr(db_init, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(db_crud, "create_trade", lambda db, data: captured.append(data))

    gs = GridStrategy(FakeRedis(), FakePortfolio())
    asyncio.run(gs._persist_grid_fill("SOL/EUR", LEVEL, 103.0, 2.0, 0.1648, reason="rebalance"))
    assert captured[-1]["reason"] == "rebalance"

    asyncio.run(gs._persist_grid_fill("SOL/EUR", LEVEL, 103.0, 2.0, 0.1648))
    assert captured[-1]["reason"] == "grid"


def test_liquidacion_rebalance_se_etiqueta(monkeypatch, tmp_path):
    db_init, db_crud = _load_db(monkeypatch, tmp_path)
    captured = []
    monkeypatch.setattr(db_init, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(db_crud, "create_trade", lambda db, data: captured.append(data))

    gs = GridStrategy(FakeRedis(), FakePortfolio())
    gs._state["SOL/EUR"] = {
        "levels": [{
            "id": "0_buy_1", "is_opening": False, "price": 99.5, "side": "buy",
            "amount": 1.0, "value_eur": 99.5, "entry_price": 100.0,
            "status": "open", "cycle_id": "c-a", "filled_at": None,
        }],
        "spacing": 1.0, "pnl_eur": 0.0, "fees_eur": 0.0,
        "total_grid_trades": 0, "lower": 88.0, "upper": 104.0,
        "leverage": 1, "center_price": 100.0, "current_price": 100.0,
    }

    asyncio.run(gs._liquidate_pair_positions("SOL/EUR", 96.3))
    assert captured, "la liquidacion debe persistir al menos un fill"
    assert all(c["reason"] == "rebalance" for c in captured), captured


def test_operations_exponen_exit_reason(monkeypatch, tmp_path):
    db_init, db_crud = _load_db(monkeypatch, tmp_path)
    from datetime import datetime

    class Leg:
        def __init__(self, side, price, qty, fee, pnl, reason, ts):
            self.side = side
            self.price = price
            self.amount_crypto = qty
            self.amount_eur = round(price * qty, 4)
            self.fee_eur = fee
            self.pnl_eur = pnl
            self.reason = reason
            self.timestamp = ts
            self.pair = "SOL/EUR"
            self.id = 1
            self.position_id = None
            self.cycle_id = None
            self.mode = "demo"

    t0 = datetime(2026, 9, 1, 12, 0)
    closed, _ = db_crud._build_round_trips([
        Leg("buy", 90.0, 1.0, 0.16, None, "grid", t0),
        Leg("sell", 91.0, 1.0, 0.16, 0.68, "rebalance", t0),
    ])
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "rebalance"


def test_trade_model_reason_default_grid():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from database.models import Base, Trade

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)
    with session() as db:
        t = Trade(pair="SOL/EUR", side="SELL", amount_crypto=1.0,
                  amount_eur=100.0, price=100.0, fee_eur=0.16, mode="demo")
        db.add(t)
        db.commit()
        db.refresh(t)
        assert t.reason == "grid"


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    class M:
        def setattr(self, *a):
            pass

    tmp = Path(tempfile.mkdtemp())
    config.database.sqlite_path = str(tmp / "r.db")
    _ = importlib.import_module("database.init_db"), importlib.import_module("database.crud")
    test_persist_grid_fill_guarda_reason(None, tmp)
    test_liquidacion_rebalance_se_etiqueta(None, tmp)
    test_operations_exponen_exit_reason(None, tmp)
    test_trade_model_reason_default_grid()
    print("OK: trazabilidad por reason")