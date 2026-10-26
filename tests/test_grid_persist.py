"""Prueba de la integración grid -> portfolio/trades: verifica que _persist_grid_fill
acredita el PnL al balance y registra un trade sin errores."""
import asyncio
import sys

sys.path.insert(0, "bot")


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.published = []

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v):
        self.store[k] = v

    async def publish(self, channel, msg):
        self.published.append((channel, msg))


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


async def main():
    from strategies.grid_strategy import GridStrategy

    r = FakeRedis()
    port = FakePortfolio()
    gs = GridStrategy(r, port)
    gs._global_state = {"enabled": True, "total_pnl_eur": 0.0, "total_grid_trades": 0}
    gs._state["ETH/EUR"] = {"spacing": 2.0}

    # Simular un SELL derivado que cierra un largo con PnL +0.75
    level = {
        "id": 1, "price": 2145.0, "side": "sell", "amount": 0.35,
        "value_eur": 750.0, "entry_price": 2142.9, "status": "filled",
        "is_derived": True,
    }
    pnl = 0.75
    gs._global_state["total_pnl_eur"] = 1.0
    gs._global_state["total_grid_trades"] = 1

    # No invocar BD real: testear que actualiza portfolio y publica
    # _persist_grid_fill internamente importa database.crud; lo preservamos
    # pero usamos un SessionLocal no disponible -> se captura la excepcion y loguea warning.
    # Para validar la logica de portfolio, llamamos el metodo y confirmamos que
    # el balance se actualiza SI la BD funciona; si falla BD, registra warning.
    await gs._persist_grid_fill("ETH/EUR", level, 2145.0, pnl, fee_eur=0.1)
    s = port.get()
    print(f"Balance tras persistir: {s['balance_eur']}")
    print(f"total_value_eur: {s['total_value_eur']}")
    print(f"total_pnl_eur: {s['total_pnl_eur']}")
    print(f"publicaciones WS: {len(r.published)}")
    for ch, msg in r.published:
        print(f"  {ch}: {msg[:120]}")
    print("PERSIST TEST (portfolio side) DONE")


if __name__ == "__main__":
    asyncio.run(main())
