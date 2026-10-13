"""Test del camino REAL del grid con broker mockeado.

No toca la red ni ningun exchange: sustituye el broker por un MockBroker en
memoria y verifica que, en modo real, el grid:

  1. coloca una orden limit por nivel al inicializar,
  2. detecta el fill consultando el broker (no por precio) y coloca la contra-orden,
  3. en rebalance, cancela las resting y cierra cada posicion viva a mercado,
  4. en margin call, cierra el corto a mercado,
  5. en stop-loss global, cierra toda la exposicion antes de parar.

El codigo bajo test es el de produccion (bot/strategies/grid_strategy.py); solo
se stubean las dependencias externas (config, portfolio, BD, telegram, redis).

Uso:
    .venv\\Scripts\\python.exe -m pytest test_grid_real_mode.py -v
"""
import os
import sys
import types
from dataclasses import dataclass
from types import SimpleNamespace as NS

import pytest

BOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot")
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)


@dataclass
class ExecutionResult:
    success: bool
    filled_amount: float = 0.0
    average_price: float = 0.0
    fee_eur: float = 0.0
    order_id: str = None
    status: str = None
    error: str = None
    raw: dict = None


class MockBroker:
    """Broker real simulado en memoria. Registra todas las llamadas."""

    mode = "real"

    def __init__(self):
        self.open_orders = {}
        self.filled = {}
        self.cancelled = []
        self.calls = []
        self.market_prices = {}
        self._seq = 0

    def _new_id(self):
        self._seq += 1
        return f"o{self._seq}"

    async def get_symbol(self, pair):
        return pair

    async def create_limit_buy(self, pair, amount, price, margin=False):
        oid = self._new_id()
        self.open_orders[oid] = {"id": oid, "pair": pair, "side": "buy",
                                 "amount": amount, "price": price}
        self.calls.append(("limit_buy", pair, amount, price, margin))
        return ExecutionResult(True, 0.0, price, order_id=oid, status="open")

    async def create_limit_sell(self, pair, amount, price, margin=False):
        oid = self._new_id()
        self.open_orders[oid] = {"id": oid, "pair": pair, "side": "sell",
                                 "amount": amount, "price": price}
        self.calls.append(("limit_sell", pair, amount, price, margin))
        return ExecutionResult(True, 0.0, price, order_id=oid, status="open")

    async def fetch_open_orders(self, pair):
        return [o for o in self.open_orders.values() if o["pair"] == pair]

    async def fetch_order_result(self, pair, order_id):
        if order_id in self.open_orders:
            o = self.open_orders[order_id]
            return ExecutionResult(True, 0.0, o["price"], order_id=order_id, status="open")
        if order_id in self.filled:
            return self.filled[order_id]
        return ExecutionResult(False, status="canceled", order_id=order_id)

    async def cancel_order(self, pair, order_id):
        self.open_orders.pop(order_id, None)
        self.cancelled.append(order_id)
        return True

    async def cancel_orders_for_pair(self, pair):
        for oid in [o["id"] for o in list(self.open_orders.values()) if o["pair"] == pair]:
            await self.cancel_order(pair, oid)
        return True

    async def close_long_market(self, pair, amount, reference_price=0.0):
        px = self.market_prices.get(pair, reference_price)
        self.calls.append(("close_long", pair, amount, px))
        return ExecutionResult(True, amount, px, order_id=self._new_id(), status="closed")

    async def close_short_market(self, pair, amount, reference_price=0.0):
        px = self.market_prices.get(pair, reference_price)
        self.calls.append(("close_short", pair, amount, px))
        return ExecutionResult(True, amount, px, order_id=self._new_id(), status="closed")

    def fill(self, order_id, price):
        o = self.open_orders.pop(order_id)
        self.filled[order_id] = ExecutionResult(
            True, o["amount"], price, fee_eur=0.0, order_id=order_id, status="closed"
        )


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)

    async def publish(self, *a, **k):
        pass


def install_stubs(mode="real", rebalance_threshold=0.08, stop_loss_pct=0.05):
    class _NullLogger:
        def __getattr__(self, _n):
            return lambda *a, **k: None

    loguru_mod = types.ModuleType("loguru")
    loguru_mod.logger = _NullLogger()
    sys.modules["loguru"] = loguru_mod

    cfg = NS(
        grid=NS(
            pairs=["SOL/EUR"], enabled=True, levels_per_pair=5, range_pct=0.05,
            capital_pct=0.9, min_lot_value_eur=2.0, leverage=1,
            rebalance_threshold=rebalance_threshold, stop_loss_pct=stop_loss_pct,
            poll_interval=15, margin_open_fee_pct=0.0002,
            margin_rollover_pct=0.00025, margin_rollover_hours=4,
            margin_call_pct=0.80, margin_stop_pct=0.40, margin_liq_fee_pct=0.03,
        ),
        exchange=NS(
            maker_fee=0.0016, taker_fee=0.0026, allow_short=True,
            margin_enabled=True, margin_mode="cross", margin_leverage=2,
        ),
        trading=NS(mode=mode, demo_initial_balance=100.0,
                   is_demo=lambda: mode == "demo"),
    )
    config_mod = types.ModuleType("config")
    config_mod.config = cfg
    sys.modules["config"] = config_mod

    trades = []

    class StubPortfolio:
        def __init__(self):
            self.p = {"balance_eur": 100.0, "initial_balance_eur": 100.0,
                      "total_value_eur": 100.0, "positions": {}}

        def get(self):
            return self.p

        async def update_balance(self, pnl):
            self.p["balance_eur"] += pnl

        async def _save(self, p):
            pass

        async def add_position(self, pair, d):
            self.p["positions"][pair] = d

        async def remove_position(self, pair):
            self.p["positions"].pop(pair, None)

    trading_mod = types.ModuleType("trading")
    sys.modules["trading"] = trading_mod
    portfolio_mod = types.ModuleType("trading.portfolio")
    portfolio_mod.Portfolio = StubPortfolio
    sys.modules["trading.portfolio"] = portfolio_mod

    broker_mod = types.ModuleType("trading.broker_abstraction")
    broker_mod.ExecutionResult = ExecutionResult
    broker_mod.build_broker = lambda: MockBroker()
    broker_mod.DemoBroker = lambda: MockBroker()
    sys.modules["trading.broker_abstraction"] = broker_mod

    notifications_mod = types.ModuleType("notifications")
    sys.modules["notifications"] = notifications_mod
    telegram_mod = types.ModuleType("notifications.telegram")
    telegram_mod._format_duration_between = lambda a, b: ""
    sys.modules["notifications.telegram"] = telegram_mod

    database_mod = types.ModuleType("database")
    sys.modules["database"] = database_mod
    crud_mod = types.ModuleType("database.crud")
    crud_mod.create_trade = lambda db, t: trades.append(t)
    crud_mod.get_operations = lambda *a, **k: []
    sys.modules["database.crud"] = crud_mod

    class _FakeQuery:
        def filter(self, *a, **k):
            return self

        def all(self):
            return []

    class _FakeDB:
        def query(self, *a, **k):
            return _FakeQuery()

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    init_db_mod = types.ModuleType("database.init_db")
    init_db_mod.SessionLocal = lambda: _FakeDB()
    sys.modules["database.init_db"] = init_db_mod

    class _Col:
        def __neg__(self):
            return None

        def __eq__(self, v):
            return None

    class _Trade(NS):
        pass

    for f in ("pnl_eur", "fee_eur", "pair", "side", "amount_crypto", "price"):
        setattr(_Trade, f, _Col())
    models_mod = types.ModuleType("database.models")
    models_mod.Trade = _Trade
    sys.modules["database.models"] = models_mod

    sqlalchemy_mod = types.ModuleType("sqlalchemy")
    sqlalchemy_mod.case = lambda *a, **k: None
    sqlalchemy_mod.func = NS(coalesce=lambda *a, **k: None, sum=lambda *a, **k: None)
    sys.modules["sqlalchemy"] = sqlalchemy_mod

    return trades


def load_grid():
    sys.modules.pop("strategies.grid_strategy", None)
    import strategies.grid_strategy as grid
    return grid


async def build_grid(mode="real", price=100.0, **kw):
    install_stubs(mode=mode, **kw)
    grid = load_grid()
    redis = FakeRedis()
    portfolio = sys.modules["trading.portfolio"].Portfolio()
    strategy = grid.GridStrategy(redis, portfolio)
    strategy.set_margin_support({"SOL/EUR": {"short_ok": True, "in_markets": True}})
    broker = MockBroker()
    strategy.broker = broker
    await redis.set("price:SOL/EUR", str(price))
    await strategy.start()
    return grid, strategy, broker, portfolio, redis


def _opening_level(strategy, side, require_order=True):
    from strategies.grid_strategy import _is_opening
    for lvl in strategy._state["SOL/EUR"]["levels"]:
        if lvl["status"] == "open" and lvl["side"] == side and _is_opening(lvl):
            if not require_order or lvl.get("order_id"):
                return lvl
    return None


def _closing_level(strategy, side):
    from strategies.grid_strategy import _is_opening
    for lvl in strategy._state["SOL/EUR"]["levels"]:
        if lvl["status"] == "open" and lvl["side"] == side and not _is_opening(lvl):
            return lvl
    return None


@pytest.mark.asyncio
async def test_real_init_places_limit_orders():
    _, strategy, broker, _, _ = await build_grid()
    open_levels = [l for l in strategy._state["SOL/EUR"]["levels"] if l["status"] == "open"]
    assert open_levels, "el grid real debe crear niveles"
    assert all(l.get("order_id") for l in open_levels), "cada nivel debe tener orden real"
    assert len(broker.open_orders) == len(open_levels)


@pytest.mark.asyncio
async def test_real_fill_detected_by_broker_places_counter():
    _, strategy, broker, _, redis = await build_grid()
    buy = _opening_level(strategy, "buy")
    assert buy is not None
    broker.fill(buy["order_id"], buy["price"])
    await redis.set("price:SOL/EUR", str(buy["price"]))
    await strategy.check_orders()

    assert buy["status"] == "filled", "el fill debe detectarse via broker"
    counter = [o for o in broker.open_orders.values() if o["side"] == "sell"]
    assert counter, "debe colocarse la contra-orden de venta en el exchange"


@pytest.mark.asyncio
async def test_real_rebalance_cancels_and_closes_live_position():
    _, strategy, broker, _, redis = await build_grid()
    buy = _opening_level(strategy, "buy")
    broker.fill(buy["order_id"], buy["price"])
    await redis.set("price:SOL/EUR", str(buy["price"]))
    await strategy.check_orders()

    live = _closing_level(strategy, "sell")
    assert live is not None, "el fill de apertura debe dejar una posicion viva (cierre sell)"
    live_order_id = live["order_id"]

    new_price = strategy._state["SOL/EUR"]["center_price"] * 1.20
    broker.market_prices["SOL/EUR"] = new_price
    await redis.set("price:SOL/EUR", str(new_price))
    await strategy.check_orders()

    kinds = [c[0] for c in broker.calls]
    assert "close_long" in kinds, "rebalance real debe cerrar la posicion larga a mercado"
    assert live_order_id in broker.cancelled, "debe cancelarse la orden resting antes de recentrar"


@pytest.mark.asyncio
async def test_real_margin_call_closes_short_market():
    _, strategy, broker, portfolio, _ = await build_grid()
    sell = _opening_level(strategy, "sell")
    assert sell is not None, "el par admite shorts en el test"
    broker.fill(sell["order_id"], sell["price"])
    await strategy._check_real_fills("SOL/EUR")

    short_leg = _closing_level(strategy, "buy")
    assert short_leg is not None, "abrir un corto deja una pata de recompra viva"

    portfolio.p["balance_eur"] = 0.5
    await strategy._check_short_liquidation("SOL/EUR", sell["price"] * 1.5)

    kinds = [c[0] for c in broker.calls]
    assert "close_short" in kinds, "margin call real debe cerrar el corto a mercado"


@pytest.mark.asyncio
async def test_real_global_stop_loss_flattens():
    _, strategy, broker, portfolio, _ = await build_grid()
    buy = _opening_level(strategy, "buy")
    broker.fill(buy["order_id"], buy["price"])
    await strategy._check_real_fills("SOL/EUR")
    assert _closing_level(strategy, "sell") is not None

    broker.market_prices["SOL/EUR"] = strategy._state["SOL/EUR"]["center_price"]
    portfolio.p["total_value_eur"] = 100.0
    strategy._global_state["total_pnl_eur"] = -1000.0
    await strategy._check_global_stop_loss()

    assert "close_long" in [c[0] for c in broker.calls], "SL real debe cerrar la exposicion"
    assert strategy._running is False, "tras el SL el grid debe quedar parado"


@pytest.mark.asyncio
async def test_demo_price_fill_unchanged():
    """El modo demo sigue detectando fills por precio y no toca el broker."""
    _, strategy, broker, _, redis = await build_grid(mode="demo")
    buy = _opening_level(strategy, "buy", require_order=False)
    assert buy is not None
    assert not buy.get("order_id"), "demo no coloca ordenes en broker"
    await redis.set("price:SOL/EUR", str(buy["price"]))
    await strategy.check_orders()
    assert buy["status"] == "filled"
    assert broker.calls == [], "demo nunca llama al broker para ejecutar"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
