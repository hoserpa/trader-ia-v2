"""Test de atomicidad balance<->BD en _persist_grid_fill.

Antes el balance se acreditaba en el portfolio (Redis) ANTES de insertar el
trade en SQLite: si el insert fallaba, el balance quedaba desincronizado de la
BD en silencio (el dashboard mostraba un PnL que la BD no tenia).

El fix inserta el trade primero y solo despues acredita el PnL al balance:
cero divergencias even si la BD falla.
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
        self.events = []

    def get(self):
        return {
            "balance_eur": self.balance,
            "initial_balance_eur": 100.0,
        }

    async def update_balance(self, delta):
        self.balance = round(self.balance + delta, 4)
        self.events.append("credit")

    async def _save(self, port):
        self.events.append("save")


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


LEVEL = {"id": "0_sell_0", "side": "sell", "amount": 1.0,
         "entry_price": 100.0, "cycle_id": "c1", "price": 100.0}


def _load_db(monkeypatch, tmp_path):
    """Carga el paquete database apuntando a una SQLite temporal (evita C:\app)."""
    monkeypatch.setattr(config.database, "sqlite_path", str(tmp_path / "t.db"))
    db_init = importlib.import_module("database.init_db")
    db_crud = importlib.import_module("database.crud")
    return db_init, db_crud


def _gs(events):
    p = FakePortfolio()
    p.events = events
    return GridStrategy(FakeRedis(), p), p


def test_db_fallo_no_acredita_balance(monkeypatch, tmp_path):
    db_init, db_crud = _load_db(monkeypatch, tmp_path)
    events = []
    gs, p = _gs(events)
    monkeypatch.setattr(db_init, "SessionLocal", lambda: FakeSession())

    def boom(db, data):
        raise RuntimeError("boom")

    monkeypatch.setattr(db_crud, "create_trade", boom)

    asyncio.run(gs._persist_grid_fill("TEST/EUR", LEVEL, 103.0, 2.0, 0.1648))
    assert p.balance == 100.0, f"balance acreditado pese al fallo de BD: {p.balance}"
    assert "credit" not in events


def test_db_insertado_antes_de_acreditar(monkeypatch, tmp_path):
    db_init, db_crud = _load_db(monkeypatch, tmp_path)
    events = []
    gs, p = _gs(events)
    monkeypatch.setattr(db_init, "SessionLocal", lambda: FakeSession())

    def ok(db, data):
        events.append("db")

    monkeypatch.setattr(db_crud, "create_trade", ok)

    asyncio.run(gs._persist_grid_fill("TEST/EUR", LEVEL, 103.0, 2.0, 0.1648))
    assert events == ["db", "credit", "save"], f"orden de efectos: {events}"
    # balance = 100 + pnl neto del fill (2.0) - fee ya dentro del pnl
    assert p.balance == round(100.0 + 2.0, 4), p.balance


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp())
    config.database.sqlite_path = str(tmp / "t.db")
    _ = importlib.import_module("database.init_db"), importlib.import_module("database.crud")
    test_db_fallo_no_acredita_balance(None, tmp)
    test_db_insertado_antes_de_acreditar(None, tmp)
    print("OK: atomicidad balance<->BD")