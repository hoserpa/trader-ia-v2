"""Prueba de gating de short en el grid por soporte real de margen.

Regresión + nuevo comportamiento: el grid no debe crear niveles SELL que abririan
un short cuando el par real no permite margen/corto, ya que el demo debe ser fiel
al modo real.
"""
import asyncio
import sys

sys.path.insert(0, "bot")

from config import config
from strategies.grid_strategy import GridStrategy


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v):
        self.store[k] = v

    async def publish(self, channel, msg):
        pass


class FakePortfolio:
    def __init__(self):
        self._state = {
            "balance_eur": 100.0,
            "initial_balance_eur": 100.0,
            "positions": {},
            "total_value_eur": 100.0,
            "total_pnl_eur": 0.0,
            "total_pnl_pct": 0.0,
        }

    def get(self):
        return self._state.copy()

    async def update_balance(self, delta):
        self._state["balance_eur"] = round(self._state["balance_eur"] + delta, 4)

    async def _save(self, state):
        self._state = state


def make_grid():
    return GridStrategy(FakeRedis(), FakePortfolio())


def test_init_with_broker_demo_allows_short_without_flags():
    """En DEMO, el grid simula shorts donde la API real lo permita (short_ok),
    sin requerir EXCHANGE_ALLOW_SHORT ni EXCHANGE_MARGIN_ENABLED (que son del real)."""
    orig_allow = config.exchange.allow_short
    orig_margin = config.exchange.margin_enabled
    orig_demo = config.exchange  # noop
    try:
        config.exchange.allow_short = False
        config.exchange.margin_enabled = False
        gs = make_grid()
        gs.set_margin_support({
            "PAIR": {"in_markets": True, "short_leverage": 3, "short_ok": True,
                     "long_leverage": 2, "margin_ok": True}
        })
        assert gs._pair_short_ok("PAIR") is True
        asyncio.run(gs._init_pair_grid("PAIR", 100.0, 10.0))
        sides = [l["side"] for l in gs._state["PAIR"]["levels"]]
        assert "sell" in sides, f"DEMO deberia simular short (short_ok=True): {sides}"
        print(f"OK demo simula short sin flags: niveles={len(sides)} sides={sorted(set(sides))}")
    finally:
        config.exchange.allow_short = orig_allow
        config.exchange.margin_enabled = orig_margin


def test_init_without_broker_is_long_only():
    """Sin broker (o sin soporte de short), el grid no crea niveles SELL."""
    gs = make_grid()
    gs._state.clear()
    # sin _margin_support -> _pair_short_ok() False -> long-only
    asyncio.run(gs._init_pair_grid("PAIR", 100.0, 10.0))
    levels = gs._state["PAIR"]["levels"]
    sides = [l["side"] for l in levels]
    assert "sell" not in sides, f"Sell levels presentes sin soporte de short: {sides}"
    assert "buy" in sides
    print(f"OK long-only: niveles={len(levels)} sides={sorted(set(sides))}")


def test_init_with_short_support_keeps_sell():
    """Con soporte de short/margen real (market + config), el grid crea SELL."""
    # Config de margen OK + mercado simulado que soporta short
    orig_allow = config.exchange.allow_short
    orig_margin = config.exchange.margin_enabled
    orig_leverage = config.exchange.margin_leverage
    try:
        config.exchange.allow_short = True
        config.exchange.margin_enabled = True
        config.exchange.margin_leverage = 2
        gs = make_grid()
        gs.set_margin_support({
            "PAIR": {"in_markets": True, "short_leverage": 3, "short_ok": True,
                     "long_leverage": 2, "margin_ok": True}
        })
        asyncio.run(gs._init_pair_grid("PAIR", 100.0, 10.0))
        levels = gs._state["PAIR"]["levels"]
        sides = [l["side"] for l in levels]
        assert "sell" in sides, f"SELL esperado con soporte de short: {sides}"
        assert "buy" in sides
        print(f"OK short permitido: niveles={len(levels)} sides={sorted(set(sides))}")
    finally:
        config.exchange.allow_short = orig_allow
        config.exchange.margin_enabled = orig_margin
        config.exchange.margin_leverage = orig_leverage


if __name__ == "__main__":
    test_init_without_broker_is_long_only()
    test_init_with_short_support_keeps_sell()
    test_init_with_broker_demo_allows_short_without_flags()
    print("ALL GRID SHORT-GATING TESTS PASSED")
