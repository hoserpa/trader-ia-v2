"""Test del guard de staleness muerto del grid.

`_get_price` consultaba una clave `price_ts:{pair}` que nadie escribe: el check
de staleness era codigo muerto. La proteccion real contra precios viejos es el
TTL de 60s de `price:{pair}` (la clave desaparece -> _get_price devuelve None).
Eliminado el branch: _get_price solo confia en la existencia de la clave.

Este test registra las claves Redis consultadas y exige que no se lea price_ts.
"""
import asyncio
import sys

sys.path.insert(0, "bot")

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
    def get(self):
        return {"balance_eur": 100.0}


def test_price_ts_no_se_consulta():
    redis = FakeRedis()
    gs = GridStrategy(redis, FakePortfolio())

    accessed = []
    orig_get = redis.get

    async def spy(k):
        accessed.append(k)
        return await orig_get(k)

    redis.get = spy
    redis.store["price:TEST/EUR"] = "100.0"

    assert asyncio.run(gs._get_price("TEST/EUR")) == 100.0
    assert accessed == ["price:TEST/EUR"], f"claves consultadas: {accessed}"


if __name__ == "__main__":
    test_price_ts_no_se_consulta()
    print("OK: price_ts no consultado")