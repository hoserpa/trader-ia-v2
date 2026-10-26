"""Modelo de margen en la pata corta (fiel a Kraken).

Un short en real presta la base con margen: fee de apertura (0.01-0.05% del
importe prestado) + rollover cada 4h (tasa fijada al abrir, 0.01-0.05%). El
demo lo modela igual: la apertura SELL debita el opening fee y el cierre BUY
debita el rollover proporcional al tiempo abierto. Tambien modela la garantia
(used margin = notional/leverage) y la liquidacion de margen: margin_level =
equity/used_margin x 100, call <= 80% y liquidacion <= 40% con comision 2-3%.
Verifica tambien que el lote minimo configurado no baje del minimo real
(ordermin/costmin).
"""
import asyncio
import importlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, "bot")

from config import config
from strategies.grid_strategy import (
    GridStrategy, _hours_between,
    margin_short_open, margin_short_rollover,
)


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

    async def add_position(self, pair, pos):
        pass

    async def remove_position(self, pair):
        pass

    async def update_balance(self, delta):
        self.balance = round(self.balance + delta, 4)

    def get(self):
        return {
            "balance_eur": self.balance,
            "initial_balance_eur": 100.0,
            "total_value_eur": self.balance,
        }


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, *a):
        self.warnings.append(" ".join(str(x) for x in a))

    def info(self, *a):
        pass

    def error(self, *a):
        pass

    def exception(self, *a):
        pass

    def debug(self, *a):
        pass


def _gs(monkeypatch, tmp_path):
    monkeypatch.setattr(config.database, "sqlite_path", str(tmp_path / "f.db"))
    db_init = importlib.import_module("database.init_db")
    db_crud = importlib.import_module("database.crud")
    monkeypatch.setattr(db_init, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(db_crud, "create_trade", lambda db, data: None)
    p = FakePortfolio()
    g = GridStrategy(FakeRedis(), p)
    g._state = {
        "SOL/EUR": {
            "current_price": 100.0, "center_price": 100.0, "spacing": 1.0,
            "levels": [], "pnl_eur": 0.0, "fees_eur": 0.0,
            "margin_fees_eur": 0.0, "total_grid_trades": 0,
        }
    }
    g._global_state = {
        "total_pnl_eur": 0.0, "total_fees_eur": 0.0, "total_margin_fees_eur": 0.0,
    }
    return g, p


def test_margin_helpers():
    assert margin_short_open(10.0, 0.0002) == 0.002
    assert margin_short_rollover(10.0, 10.0, 0.00025, 4) == pytest.approx(0.00625, abs=1e-4)
    start = datetime.now(timezone.utc) - timedelta(hours=10)
    end = datetime.now(timezone.utc)
    assert abs(_hours_between(start.isoformat(), end.isoformat()) - 10.0) < 0.01
    assert _hours_between("", "") == 0.0


def test_short_open_debita_opening_fee(monkeypatch, tmp_path):
    g, p = _gs(monkeypatch, tmp_path)
    level = {"id": 5, "side": "sell", "price": 100.0, "entry_price": 100.0,
             "amount": 0.1, "status": "open"}
    asyncio.run(g._handle_fill("SOL/EUR", level, 100.0))
    fee = 100.0 * 0.1 * 0.0016
    margin = margin_short_open(100.0 * 0.1, config.grid.margin_open_fee_pct)
    assert p.balance == round(100.0 - fee - margin, 4), p.balance
    counter = g._state["SOL/EUR"]["levels"][0]
    assert counter["side"] == "buy"
    assert counter["margin_open_eur"] == margin
    assert g._state["SOL/EUR"]["margin_fees_eur"] == margin


def test_short_cierre_debita_rollover(monkeypatch, tmp_path):
    g, p = _gs(monkeypatch, tmp_path)
    opened_at = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    level = {"id": "5_buy_6", "side": "buy", "price": 99.5, "entry_price": 100.0,
             "amount": 0.1, "cycle_id": "c1", "status": "open", "opened_at": opened_at,
             "filled_at": None}
    asyncio.run(g._handle_fill("SOL/EUR", level, 99.5))
    fee = 99.5 * 0.1 * 0.0016
    elapsed = _hours_between(opened_at, level["filled_at"])
    rollover = margin_short_rollover(
        100.0 * 0.1, elapsed,
        config.grid.margin_rollover_pct, config.grid.margin_rollover_hours)
    expect_pnl = round((100.0 - 99.5) * 0.1 - fee - rollover, 4)
    assert p.balance == round(100.0 + expect_pnl, 4), p.balance
    assert g._state["SOL/EUR"]["margin_fees_eur"] == rollover
    assert g._global_state["total_margin_fees_eur"] == rollover


def test_short_abierto_corto_no_debita_rollover(monkeypatch, tmp_path):
    g, p = _gs(monkeypatch, tmp_path)
    level = {"id": 5, "side": "sell", "price": 100.0, "entry_price": 100.0,
             "amount": 0.1, "status": "open"}
    asyncio.run(g._handle_fill("SOL/EUR", level, 100.0))
    assert g._global_state["total_margin_fees_eur"] == margin_short_open(
        10.0, config.grid.margin_open_fee_pct)


def test_long_cierre_no_debita_margen(monkeypatch, tmp_path):
    g, p = _gs(monkeypatch, tmp_path)
    opened_at = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    level = {"id": "0_sell_1", "side": "sell", "price": 101.0, "entry_price": 100.0,
             "amount": 0.1, "cycle_id": "c2", "status": "open", "opened_at": opened_at}
    asyncio.run(g._handle_fill("SOL/EUR", level, 101.0))
    fee = 101.0 * 0.1 * 0.0016
    pnl = (101.0 - 100.0) * 0.1 - fee
    assert p.balance == round(100.0 + pnl, 4), p.balance
    assert g._state["SOL/EUR"]["margin_fees_eur"] == 0.0


def test_rebalance_liquida_short_con_rollover(monkeypatch, tmp_path):
    g, p = _gs(monkeypatch, tmp_path)
    opened_at = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    counter = {"id": "7_buy_8", "side": "buy", "price": 99.5, "entry_price": 100.0,
               "amount": 0.1, "cycle_id": "c3", "status": "open", "opened_at": opened_at,
               "filled_at": None}
    g._state["SOL/EUR"]["levels"] = [counter]
    asyncio.run(g._liquidate_pair_positions("SOL/EUR", 90.0))
    fee = 90.0 * 0.1 * config.exchange.taker_fee
    rollover = margin_short_rollover(
        100.0 * 0.1, 12.0,
        config.grid.margin_rollover_pct, config.grid.margin_rollover_hours)
    expect_pnl = (100.0 - 90.0) * 0.1 - fee - rollover
    assert p.balance == round(100.0 + expect_pnl, 4), p.balance
    assert g._state["SOL/EUR"]["margin_fees_eur"] == rollover
    assert g._global_state["total_margin_fees_eur"] == rollover


def test_lote_minimo_menor_que_ordermin_avisa(monkeypatch, tmp_path):
    import strategies.grid_strategy as gmod
    g, p = _gs(monkeypatch, tmp_path)
    monkeypatch.setattr(config.grid, "min_lot_value_eur", 2.0)
    fake_log = FakeLogger()
    monkeypatch.setattr(gmod, "logger", fake_log)
    g.set_margin_support({"SOL/EUR": {"in_markets": True, "short_ok": True,
                                      "ordermin": 0.06, "costmin": 0.45}})
    asyncio.run(g._init_pair_grid("SOL/EUR", 100.0, 10.0))
    assert any("minimo real Kraken" in w for w in fake_log.warnings)


def test_lote_minimo_suficiente_no_avisa(monkeypatch, tmp_path):
    import strategies.grid_strategy as gmod
    g, p = _gs(monkeypatch, tmp_path)
    monkeypatch.setattr(config.grid, "min_lot_value_eur", 7.0)
    fake_log = FakeLogger()
    monkeypatch.setattr(gmod, "logger", fake_log)
    g.set_margin_support({"SOL/EUR": {"in_markets": True, "short_ok": True,
                                      "ordermin": 0.06, "costmin": 0.45}})
    asyncio.run(g._init_pair_grid("SOL/EUR", 100.0, 10.0))
    assert not any("minimo real Kraken" in w for w in fake_log.warnings)


def _add_short(g, pair="SOL/EUR", entry=100.0, amount=0.5, opened_at=None, lid="7_buy_8", cid="c_liq"):
    if not opened_at:
        opened_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    g._state[pair]["levels"].append({
        "id": lid, "is_opening": False, "side": "buy", "price": entry, "entry_price": entry,
        "amount": amount, "cycle_id": cid, "status": "open",
        "opened_at": opened_at, "filled_at": None, "margin_open_eur": 0.002,
    })


def test_margin_health_sin_shorts(monkeypatch, tmp_path):
    g, p = _gs(monkeypatch, tmp_path)
    g._state["SOL/EUR"]["levels"] = [{"id": 3, "side": "sell", "price": 105.0,
                                      "entry_price": 105.0, "amount": 0.1, "status": "open",
                                      "filled_at": None}]
    h = g.margin_health({"SOL/EUR": 104.0})
    assert h["used_margin_eur"] == 0.0
    assert h["margin_level_pct"] is None


def test_margin_health_used_margin_notional_sobre_leverage(monkeypatch, tmp_path):
    g, p = _gs(monkeypatch, tmp_path)
    _add_short(g, entry=100.0, amount=0.5)
    lev = max(config.exchange.margin_leverage, 1)
    h = g.margin_health({"SOL/EUR": 101.0})
    assert h["used_margin_eur"] == round(100.0 * 0.5 / lev, 4)
    assert h["margin_level_pct"] is not None


def test_margin_health_ignora_niveles_originales_y_longs(monkeypatch, tmp_path):
    g, p = _gs(monkeypatch, tmp_path)
    g._state["SOL/EUR"]["levels"] = [
        {"id": 0, "side": "buy", "price": 100.0, "entry_price": 100.0, "amount": 0.2,
         "status": "open", "filled_at": None},
        {"id": "0_sell_1", "side": "sell", "price": 102.0, "entry_price": 100.0,
         "amount": 0.2, "cycle_id": "c_long", "status": "open", "filled_at": None},
    ]
    h = g.margin_health({"SOL/EUR": 101.0})
    assert h["used_margin_eur"] == 0.0
    assert h["margin_level_pct"] is None
    assert h["unrealized_eur"] == round((101.0 - 100.0) * 0.2, 4)


def test_margin_call_avisa_y_no_liquida(monkeypatch, tmp_path):
    import strategies.grid_strategy as gmod
    g, p = _gs(monkeypatch, tmp_path)
    fake_log = FakeLogger()
    monkeypatch.setattr(gmod, "logger", fake_log)
    p.balance = 18.0
    _add_short(g)
    cover = g._state["SOL/EUR"]["levels"][0]
    n = asyncio.run(g._check_short_liquidation("SOL/EUR", 100.0))
    assert n == 0
    assert cover["status"] == "open"
    assert any("MARGIN CALL" in w for w in fake_log.warnings)
    asyncio.run(g._check_short_liquidation("SOL/EUR", 101.0))
    assert sum(1 for w in fake_log.warnings if "MARGIN CALL" in w) == 1


def test_margin_call_no_avisa_sobre_nivel(monkeypatch, tmp_path):
    import strategies.grid_strategy as gmod
    g, p = _gs(monkeypatch, tmp_path)
    fake_log = FakeLogger()
    monkeypatch.setattr(gmod, "logger", fake_log)
    p.balance = 40.0
    _add_short(g)
    asyncio.run(g._check_short_liquidation("SOL/EUR", 100.0))
    assert not any("MARGIN CALL" in w for w in fake_log.warnings)


def test_liquidacion_cierra_short_con_comision(monkeypatch, tmp_path):
    import strategies.grid_strategy as gmod
    g, p = _gs(monkeypatch, tmp_path)
    fake_log = FakeLogger()
    monkeypatch.setattr(gmod, "logger", fake_log)
    p.balance = 10.0
    _add_short(g, entry=100.0, amount=0.5)
    cover = g._state["SOL/EUR"]["levels"][0]
    balance_before = p.balance
    liq_price = 101.0
    n = asyncio.run(g._check_short_liquidation("SOL/EUR", liq_price))
    assert n == 1
    assert cover["status"] == "filled"
    entry = 100.0
    fee = liq_price * 0.5 * gmod.config.exchange.maker_fee
    liq_fee = round(entry * 0.5 * config.grid.margin_liq_fee_pct, 4)
    pnl = (entry - liq_price) * 0.5 - fee - liq_fee
    assert p.balance == round(balance_before + pnl, 4), p.balance
    assert g._state["SOL/EUR"]["margin_fees_eur"] == liq_fee
    assert g._global_state["total_margin_fees_eur"] == liq_fee
    assert any("LIQUIDACION" in w for w in fake_log.warnings)


def test_liquida_todas_las_shorts_del_par(monkeypatch, tmp_path):
    g, p = _gs(monkeypatch, tmp_path)
    p.balance = 8.0
    _add_short(g, entry=100.0, amount=0.5, lid="a")
    _add_short(g, entry=102.0, amount=0.5, lid="b")
    n = asyncio.run(g._check_short_liquidation("SOL/EUR", 102.0))
    assert n == 2
    assert all(l["status"] == "filled" for l in g._state["SOL/EUR"]["levels"])


def test_liquidacion_reconcilia_balance_con_margin_stop(monkeypatch, tmp_path):
    g, p = _gs(monkeypatch, tmp_path)
    p.balance = 100.0
    _add_short(g, entry=100.0, amount=0.6)
    h = g.margin_health({"SOL/EUR": 250.0})
    assert h["margin_level_pct"] == pytest.approx(33.33, abs=0.01)
    n = asyncio.run(g._check_short_liquidation("SOL/EUR", 250.0))
    assert n == 1
    assert g.margin_health({"SOL/EUR": 250.0})["margin_level_pct"] is None


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _FakeTrade:
    side = "SELL"
    pnl_eur = None
    fee_eur = 0.0
    margin_fee_eur = None
    amount_crypto = 0.0
    price = 0.0

    def __init__(self, rows):
        self.rows = rows

    def query(self, *cols):
        return _Q(self.rows)


def test_rebuild_ledger_incluye_margin_open_del_short(monkeypatch, tmp_path):
    import database.models as m
    g, p = _gs(monkeypatch, tmp_path)
    fee = 100.0 * 0.1 * 0.0016
    margin = margin_short_open(10.0, config.grid.margin_open_fee_pct)
    p.balance = round(100.0 - 2 * (fee + margin), 4)
    p.initial_balance_eur = 100.0
    p.get = lambda: {"balance_eur": p.balance, "initial_balance_eur": 100.0,
                     "total_value_eur": p.balance}
    rows = [
        ("SELL", None, fee, None, 0.1, 100.0),
        ("SELL", None, fee, None, 0.1, 100.0),
    ]
    monkeypatch.setattr(m, "Trade", _FakeTrade(rows))

    class Session:
        def query(self, *cols):
            return _Q(rows)

        def close(self):
            pass

    db_init = importlib.import_module("database.init_db")
    monkeypatch.setattr(db_init, "SessionLocal", lambda: Session())
    asyncio.run(g._rebuild_balance_from_ledger())
    expected = round(100.0 - 2 * (fee + margin), 4)
    assert p.balance == expected, (p.balance, expected)


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    test_margin_helpers()
    test_short_open_debita_opening_fee(None, tmp)
    test_short_cierre_debita_rollover(None, tmp)
    test_short_abierto_corto_no_debita_rollover(None, tmp)
    test_long_cierre_no_debita_margen(None, tmp)
    print("OK: margen short = opening + rollover proporcional")