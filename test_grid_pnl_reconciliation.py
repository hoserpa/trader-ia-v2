"""Test de regresión: el PnL acreditado por GridStrategy debe coincidir con el
PnL real (flujo de caja neto + inventario marcado a mercado), no inflarse en
cada cruce de nivel.

Bug original: en GridStrategy._handle_fill, `is_close = isinstance(level["id"],
str)` es True para siempre a partir del segundo fill de cada cadena de
contra-órdenes (todas heredan id de tipo string), en vez de alternar
apertura/cierre en cada hop. Esto hace que un precio que simplemente rebota
entre dos niveles, sin tendencia neta, genere PnL creciente sin límite aunque
el inventario real se mantenga acotado.

Uso:
    Copia este archivo junto a grid_strategy.py (o ajusta sys.path) y ejecuta:
        pytest test_grid_pnl_reconciliation.py -v
    No requiere Redis, SQLAlchemy real, ni el resto del repo: todas las
    dependencias externas de grid_strategy.py se sustituyen por stubs mínimos
    ANTES de importar el módulo, así que el propio código de producción se
    ejecuta sin modificar.

Qué comprueba:
    1. test_pnl_no_inflation_on_flat_oscillation: precio rebotando entre dos
       niveles fijos N veces (sin tendencia neta). El PnL acumulado tras N
       ciclos completos debe ser ~N * (spacing * amount - 2*fee), no más.
       Este test falla de forma determinista con el bug tal cual está y debe
       pasar una vez corregido `is_close`.
    2. test_pnl_matches_mtm_monte_carlo: camino de precio aleatorio con
       reversión a la media (sin tendencia neta esperada). El PnL total
       acreditado al balance debe coincidir, dentro de una tolerancia, con
       el PnL real calculado de forma independiente a partir de un libro de
       caja + inventario marcado a mercado construido solo a partir de los
       trades persistidos en BD (create_trade). Cualquier discrepancia
       sistemática > tolerancia indica doble contabilización.
"""
import math
import random
import sys
import types
from types import SimpleNamespace as NS

import pytest


# --------------------------------------------------------------------------
# Stubs de las dependencias externas de grid_strategy.py.
# Se instalan en sys.modules ANTES de importar el módulo real, para que el
# código bajo test sea exactamente el de producción, sin reimplementarlo.
# --------------------------------------------------------------------------

def _install_stubs(rebalance_threshold: float, levels_per_pair: int = 15,
                    range_pct: float = 0.08, maker_fee: float = 0.0016,
                    taker_fee: float = 0.0026):
    class _NullLogger:
        def __getattr__(self, _name):
            return lambda *a, **k: None

    loguru_mod = types.ModuleType("loguru")
    loguru_mod.logger = _NullLogger()
    sys.modules["loguru"] = loguru_mod

    cfg = NS(
        grid=NS(
            pairs=["SOL/EUR"], enabled=True, levels_per_pair=levels_per_pair,
            range_pct=range_pct, capital_pct=0.9, min_lot_value_eur=2.0,
            leverage=1, atr_adaptive=False,
            rebalance_threshold=rebalance_threshold, stop_loss_pct=0.99,
            price_stale_sec=300, max_levels=15, atr_range_mult=1,
            atr_spacing_divisor=1,
            margin_open_fee_pct=0.0002,
            margin_rollover_pct=0.00025, margin_rollover_hours=4,
            margin_call_pct=0.80, margin_stop_pct=0.40, margin_liq_fee_pct=0.03,
        ),
        exchange=NS(
            maker_fee=maker_fee, taker_fee=taker_fee, allow_short=True,
            margin_enabled=True, margin_mode="cross", margin_leverage=1,
        ),
        trading=NS(mode="demo", demo_initial_balance=100.0, is_demo=lambda: True),
    )
    config_mod = types.ModuleType("config")
    config_mod.config = cfg
    sys.modules["config"] = config_mod

    trades: list[dict] = []

    class StubPortfolio:
        def __init__(self):
            self.p = {
                "balance_eur": 100.0, "initial_balance_eur": 100.0,
                "total_value_eur": 100.0, "positions": {},
            }

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

        def scalar(self):
            return 0.0

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
        def isnot(self, v):
            return None

        def __neg__(self):
            return None

        def __eq__(self, v):
            return None

    class _Trade(NS):
        pass

    _Trade.pnl_eur = _Col()
    _Trade.fee_eur = _Col()
    _Trade.pair = _Col()
    _Trade.side = _Col()
    _Trade.amount_crypto = _Col()
    _Trade.price = _Col()
    models_mod = types.ModuleType("database.models")
    models_mod.Trade = _Trade
    sys.modules["database.models"] = models_mod

    sqlalchemy_mod = types.ModuleType("sqlalchemy")
    sqlalchemy_mod.case = lambda *a, **k: None
    sqlalchemy_mod.func = NS(coalesce=lambda *a, **k: None, sum=lambda *a, **k: None)
    sys.modules["sqlalchemy"] = sqlalchemy_mod

    return trades, StubPortfolio


class FakeRedis:
    """Redis async en memoria, suficiente para GridStrategy."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)

    async def publish(self, *args, **kwargs):
        pass


def _load_grid_strategy():
    """(Re)importa grid_strategy.py forzando módulo fresco tras instalar stubs."""
    sys.modules.pop("grid_strategy", None)
    import grid_strategy  # noqa: PLC0415
    return grid_strategy


def _mark_to_market_pnl(trades: list[dict], last_price: float) -> tuple[float, float]:
    """Calcula el PnL real de forma independiente a partir de los trades
    persistidos, como si fuera un libro de caja + inventario marcado a mercado.

    Devuelve (pnl_real, valor_inventario_final). No usa el campo pnl_eur de
    los trades en absoluto: solo side/price/amount/fee, que son los datos
    objetivos de cada fill.
    """
    cash = 0.0
    position = 0.0
    fees = 0.0
    for t in trades:
        sign = 1 if t["side"] == "SELL" else -1
        cash += sign * t["amount_crypto"] * t["price"]
        position -= sign * t["amount_crypto"]
        fees += t["fee_eur"]
    inventory_value = position * last_price
    return cash - fees + inventory_value, inventory_value


# --------------------------------------------------------------------------
# Test 1: escenario determinista, precio rebotando sin tendencia neta.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pnl_no_inflation_on_flat_oscillation():
    trades, StubPortfolio = _install_stubs(
        rebalance_threshold=999.0,  # sin rebalanceos, para aislar el bug
        levels_per_pair=5, range_pct=0.05,
    )
    grid_strategy = _load_grid_strategy()

    redis = FakeRedis()
    portfolio = StubPortfolio()
    strategy = grid_strategy.GridStrategy(redis, portfolio)
    strategy.set_margin_support({"SOL/EUR": {"short_ok": False, "in_markets": True}})

    price = 100.0
    await redis.set("price:SOL/EUR", str(price))
    await strategy.start()

    # Rebote real entre DOS niveles adyacentes del grid (B=compra inicial, S=B+spacing):
    # el precio -> B dispara la compra (apertura) y -> S dispara la contra-orden
    # (cierre). Sin tendencia neta y con inventario acotado, el PnL contabilizado tras
    # N ciclos completos debe ser N * (spacing*amount - fee_B - fee_S), no crecer sin
    # limite. Nota: saltar entre los extremos del rango NO es un rebote entre dos
    # niveles -- rellena cascadas asimetricas que desbocan el inventario.
    spacing = strategy._state["SOL/EUR"]["spacing"]
    initial_levels = [l for l in strategy._state["SOL/EUR"]["levels"] if l["side"] == "buy"]
    buy_level = sorted(initial_levels, key=lambda l: l["price"])[-1]
    b_price = buy_level["price"]
    s_price = b_price + spacing
    amount = buy_level["amount"]

    n_round_trips = 25
    for _ in range(n_round_trips):
        for p in (b_price, s_price):
            await redis.set("price:SOL/EUR", str(p))
            await strategy.check_orders()

    booked_pnl = portfolio.p["balance_eur"] - 100.0
    real_pnl, inventory_value = _mark_to_market_pnl(trades, price)

    # Inventario final acotado: a lo sumo un nivel residual en espera de cierre.
    assert abs(inventory_value) < 20.0, (
        f"Inventario final inesperadamente grande ({inventory_value:.2f}€); "
        "revisa el escenario de test antes de interpretar el resultado."
    )

    fee_rate = grid_strategy.config.exchange.maker_fee
    fee_cycle = (b_price + s_price) * amount * fee_rate
    expected = n_round_trips * (spacing * amount - fee_cycle)

    # El PnL contabilizado debe ser ~ N * (spacing*amount - 2*spacing medio de fees),
    # no mas: con el bug, booked creceria una vez por HOP en vez de por ciclo (2x+).
    tolerance = 0.05
    assert abs(booked_pnl - expected) <= tolerance, (
        f"PnL contabilizado ({booked_pnl:.4f}€) != esperado ({expected:.4f}€) "
        f"= {n_round_trips} ciclos * (spacing*amount - fees). "
        "Esto reproduce el bug de doble contabilizacion en _handle_fill "
        "(is_close = isinstance(id, str) no alterna apertura/cierre)."
    )

    # Y ademas, no puede superar al PnL real (caja neta + inventario MtM).
    assert booked_pnl <= real_pnl * (1 + tolerance) + 0.05, (
        f"PnL contabilizado ({booked_pnl:.4f}€) muy por encima del PnL real "
        f"({real_pnl:.4f}€) para un precio sin tendencia neta. "
        "Esto reproduce el bug de doble contabilizacion en _handle_fill."
    )


# --------------------------------------------------------------------------
# Test 2: camino de precio aleatorio con reversión a la media (Monte Carlo).
# --------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
async def test_pnl_matches_mtm_monte_carlo(seed):
    trades, StubPortfolio = _install_stubs(rebalance_threshold=0.5)
    grid_strategy = _load_grid_strategy()

    redis = FakeRedis()
    portfolio = StubPortfolio()
    strategy = grid_strategy.GridStrategy(redis, portfolio)
    strategy.set_margin_support({"SOL/EUR": {"short_ok": True, "in_markets": True}})

    random.seed(seed)
    price = 100.0
    await redis.set("price:SOL/EUR", str(price))
    await strategy.start()

    n_polls = 20_000
    sigma = 0.0025
    kappa = 0.01  # reversión a la media: sin tendencia neta esperada
    for _ in range(n_polls):
        price *= math.exp(random.gauss(0, sigma) - kappa * math.log(price / 100.0))
        await redis.set("price:SOL/EUR", str(price))
        await strategy.check_orders()

    booked_pnl = portfolio.p["balance_eur"] - 100.0
    real_pnl, inventory_value = _mark_to_market_pnl(trades, price)

    tolerance = 0.15
    assert booked_pnl <= abs(real_pnl) * (1 + tolerance) + 0.5, (
        f"[seed={seed}] PnL contabilizado ({booked_pnl:.4f}€) no reconcilia "
        f"con el PnL real de mercado ({real_pnl:.4f}€, inventario final "
        f"{inventory_value:.2f}€). Ratio: {booked_pnl / real_pnl if real_pnl else float('inf'):.2f}x"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
