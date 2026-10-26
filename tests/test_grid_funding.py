"""Test del patrimonio del grid: PnL realizado + posiciones abiertas valoradas.

El balance del demo es una cuenta de resultados (cada fill acredita PnL neto,
sin desplazar nocional: los contra-lados short son virtuales, sin margen). El
valor nuevo es que total_value_eur = balance + MTM de niveles abiertos
(patrimonio, long + / short −), que es lo que mide el drawdown de la salida a
real. El balance se reconstruye de forma idempotente desde el ledger
(initial + Σ pnl_eur) al arrancar.
"""
import asyncio
import importlib
import sys

sys.path.insert(0, "bot")

from config import config
from strategies.grid_strategy import GridStrategy
from trading.portfolio import Portfolio


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
    def __init__(self, balance=100.0):
        self.balance = balance

    def get(self):
        return {
            "balance_eur": self.balance,
            "initial_balance_eur": 100.0,
            "total_value_eur": self.balance,
        }

    async def update_balance(self, delta):
        self.balance = round(self.balance + delta, 4)

    async def _save(self, port):
        pass


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


LEVEL_BUY = {"id": 0, "side": "buy", "amount": 1.0,
             "entry_price": 2.0, "price": 2.0}
LEVEL_SELL_CLOSE = {"id": "0_sell_0", "side": "sell", "amount": 1.0,
                    "entry_price": 2.0, "price": 3.0, "cycle_id": "c"}


def _load_db(monkeypatch, tmp_path):
    monkeypatch.setattr(config.database, "sqlite_path", str(tmp_path / "f.db"))
    db_init = importlib.import_module("database.init_db")
    db_crud = importlib.import_module("database.crud")
    return db_init, db_crud


def _gs(monkeypatch, tmp_path):
    db_init, db_crud = _load_db(monkeypatch, tmp_path)
    monkeypatch.setattr(db_init, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(db_crud, "create_trade", lambda db, data: None)
    p = FakePortfolio()
    return GridStrategy(FakeRedis(), p), p


def test_equity_suma_unrealized_no_doble_conteo(monkeypatch, tmp_path):
    """Patrimonio = realizado + Σ (precio−entrada)×cantidad de piernas ejecutadas."""
    gs, p = _gs(monkeypatch, tmp_path)
    p.balance = 100.0
    gs._state = {
        "SOL/EUR": {
            "current_price": 2.5, "center_price": 2.0,
            "levels": [
                {"id": "0_sell_1", "is_opening": False, "side": "sell", "amount": 1.0,
                 "entry_price": 2.0, "price": 3.0, "status": "open", "filled_at": None},
                {"id": "1_buy_2", "is_opening": False, "side": "buy", "amount": 1.0,
                 "entry_price": 3.0, "price": 2.0, "status": "open", "filled_at": None},
                {"id": 2, "is_opening": True, "side": "buy", "amount": 1.0,
                 "entry_price": 2.0, "price": 2.0, "status": "open", "filled_at": None},
            ],
        }
    }
    equity = gs._equity_eur({"balance_eur": 100.0})
    # long: +1.0×(2.5−2.0)=+0.5 ; short: +1.0×(3.0−2.5)=+0.5 ; resting no cuenta
    assert equity == 101.0, equity


def test_apertura_compra_solo_fee_credita(monkeypatch, tmp_path):
    """Una apertura no desplaza nocional: solo acredita −fee (shorts virtuales)."""
    gs, p = _gs(monkeypatch, tmp_path)
    fee = 2.0 * 1.0 * 0.0016
    asyncio.run(gs._persist_grid_fill("SOL/EUR", LEVEL_BUY, 2.0, -fee, fee))
    assert p.balance == round(100.0 - fee, 4), p.balance


def test_cierre_venta_acredita_pnl(monkeypatch, tmp_path):
    gs, p = _gs(monkeypatch, tmp_path)
    fee = 3.0 * 1.0 * 0.0016
    pnl = (3.0 - 2.0) * 1.0 - fee
    asyncio.run(gs._persist_grid_fill("SOL/EUR", LEVEL_SELL_CLOSE, 3.0, pnl, fee))
    assert p.balance == round(100.0 + pnl, 4), p.balance


def test_ciclo_completo_devuelve_pnl_neto(monkeypatch, tmp_path):
    gs, p = _gs(monkeypatch, tmp_path)
    fee_open = 2.0 * 1.0 * 0.0016
    fee_close = 3.0 * 1.0 * 0.0016
    asyncio.run(gs._persist_grid_fill("SOL/EUR", LEVEL_BUY, 2.0, -fee_open, fee_open))
    pnl_close = (3.0 - 2.0) * 1.0 - fee_close
    asyncio.run(gs._persist_grid_fill("SOL/EUR", LEVEL_SELL_CLOSE, 3.0, pnl_close, fee_close))
    expected = 100.0 + (3.0 - 2.0) * 1.0 - fee_open - fee_close
    assert p.balance == round(expected, 4), p.balance


def test_total_value_incluye_posiciones_abiertas():
    port = Portfolio(FakeRedis())
    asyncio.run(port.initialize(100.0))
    st = asyncio.run(port.update_valuations(
        {"SOL/EUR": 2.5},
        open_levels={"SOL/EUR": [{"side": "long", "amount": 1.0, "entry_price": 2.0}]},
    ))
    assert st["total_value_eur"] == 100.5, st["total_value_eur"]
    assert st["total_pnl_eur"] == 0.5


def test_total_value_short_resta_liabilidad():
    port = Portfolio(FakeRedis())
    asyncio.run(port.initialize(100.0))
    st = asyncio.run(port.update_valuations(
        {"SOL/EUR": 2.8},
        open_levels={"SOL/EUR": [{"side": "short", "amount": 1.0, "entry_price": 3.0}]},
    ))
    assert st["total_value_eur"] == 100.2, st["total_value_eur"]


def test_reconcile_grid_usa_patrimonio_no_caja(monkeypatch, tmp_path):
    gs, p = _gs(monkeypatch, tmp_path)
    p.get = lambda: {
        "balance_eur": 100.0,
        "initial_balance_eur": 100.0,
        "total_value_eur": 105.0,
    }
    gs._global_state = {}
    gs._reconcile_with_portfolio()
    assert gs._global_state["total_pnl_eur"] == 5.0


def test_rebuild_balance_desde_ledger(monkeypatch, tmp_path):
    """El balance se reconstruye desde el ledger con PnL realizado (no nocional).

    Bajo la contabilidad anterior (con nocional desplazado) la caja quedaba
    inflada por los contra-lados short virtuales. El rebuild idempotente la
    devuelve a initial + Σ pnl_eur y no vuelve a tocarla.
    """
    from sqlalchemy.orm import sessionmaker
    from database.init_db import get_engine
    from database.models import Base

    db_init, db_crud = _load_db(monkeypatch, tmp_path)
    engine = get_engine()
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db_init, "SessionLocal", Session)

    db = Session()
    db_crud.create_trade(db, {
        "pair": "SOL/EUR", "side": "sell", "amount_crypto": 1.0,
        "amount_eur": 103.0, "price": 103.0, "fee_eur": 0.1648,
        "pnl_eur": 2.0, "mode": "demo",
    })
    db.close()

    p = FakePortfolio(balance=103.0)
    gs = GridStrategy(FakeRedis(), p)
    asyncio.run(gs._rebuild_balance_from_ledger())
    assert p.balance == 102.0, p.balance
    asyncio.run(gs._rebuild_balance_from_ledger())
    assert p.balance == 102.0, f"rebuild no idempotente: {p.balance}"


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    class M:
        def setattr(self, *a):
            pass

    tmp = Path(tempfile.mkdtemp())
    config.database.sqlite_path = str(tmp / "f.db")
    _ = importlib.import_module("database.init_db"), importlib.import_module("database.crud")
    test_apertura_compra_solo_fee_credita(None, tmp)
    test_cierre_venta_acredita_pnl(None, tmp)
    test_ciclo_completo_devuelve_pnl_neto(None, tmp)
    test_total_value_incluye_posiciones_abiertas()
    test_total_value_short_resta_liabilidad()
    test_reconcile_grid_usa_patrimonio_no_caja(None, tmp)
    test_rebuild_balance_desde_ledger(None, tmp)
    print("OK: patrimonio = PnL realizado + MTM de abiertas")