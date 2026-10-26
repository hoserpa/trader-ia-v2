"""Test del nivel central fantasma del grid.

Con 15 niveles (impar) y rango simetrico, la construccion ``lower + i*spacing``
coloca un nivel EXACTAMENTE en current_price. Ese nivel (side sell) se rellena
al instante en el primer check_orders con pnl = -fee: un ciclo fantasma que
abre un short sin spread real.

El fix desplaza los niveles media posicion de spacing para que ninguno
coincida con el centro.
"""
import asyncio
import sys

sys.path.insert(0, "bot")

from config import config
from strategies.grid_strategy import GridStrategy

_ORIG = (
    config.grid.pairs,
    config.grid.levels_per_pair,
    config.grid.range_pct,
    config.grid.enabled,
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
    def __init__(self):
        self.positions = {}

    def get(self):
        return {
            "balance_eur": 100.0,
            "initial_balance_eur": 100.0,
            "positions": self.positions,
        }

    async def add_position(self, pair, data):
        self.positions[pair] = data

    async def remove_position(self, pair):
        self.positions.pop(pair, None)

    async def update_balance(self, delta):
        pass

    async def _save(self, port):
        pass


def _setup():
    config.grid.pairs = ["TEST/EUR"]
    config.grid.levels_per_pair = 15
    config.grid.range_pct = 0.08
    config.grid.enabled = True


def _teardown():
    config.grid.pairs, config.grid.levels_per_pair, \
        config.grid.range_pct, config.grid.enabled = _ORIG


def _grid():
    gs = GridStrategy(FakeRedis(), FakePortfolio())
    gs.set_margin_support({"TEST/EUR": {"in_markets": True, "short_ok": True}})
    gs._running = True
    return gs


def test_ningun_nivel_coincide_con_precio_central():
    _setup()
    try:
        gs = _grid()
        asyncio.run(gs._init_pair_grid("TEST/EUR", 100.0, 100.0))
        prices = [l["price"] for l in gs._state["TEST/EUR"]["levels"]]
        assert len(prices) == config.grid.levels_per_pair
        assert prices == sorted(prices)
        assert all(abs(p - 100.0) > 1e-9 for p in prices), \
            "nivel colocado exactamente en el precio central"
    finally:
        _teardown()


def test_precio_centro_no_dispara_fills():
    _setup()
    try:
        gs = _grid()

        async def noop(*a, **kw):
            pass
        gs._persist_grid_fill = noop

        asyncio.run(gs._init_pair_grid("TEST/EUR", 100.0, 100.0))
        gs.redis.store["price:TEST/EUR"] = "100.0"
        asyncio.run(gs.check_orders())

        levels = gs._state["TEST/EUR"]["levels"]
        assert all(l["status"] == "open" for l in levels), \
            "se relleno un nivel sin que el precio cruzara ninguno"
        assert gs.portfolio.positions == {}
        assert gs._state["TEST/EUR"]["pnl_eur"] == 0.0
    finally:
        _teardown()


if __name__ == "__main__":
    test_ningun_nivel_coincide_con_precio_central()
    test_precio_centro_no_dispara_fills()
    print("OK: sin nivel central fantasma")