"""Prueba de regresión: el estado del grid (PnL/trades/fees/started_at) debe sobrevivir
reinicios y reconciliarse con el balance real del portfolio.

Cubre el bug de grid_strategy.start(): antes, un reinicio del contenedor que dejaba
enabled=False (p.ej. tras un shutdown limpio) hacía que el histórico del grid se
re-inicializara a cero, mientras el portfolio conservaba su acumulado -> dashboard
mostraba dos cifras de PnL contradictorias.
"""
import asyncio
import json
import sys

sys.path.insert(0, "bot")

from config import config
from strategies.grid_strategy import GridStrategy

PAIR = config.grid.pairs[0]


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
    def __init__(self, balance=100.0, initial=100.0):
        self._state = {
            "balance_eur": balance,
            "initial_balance_eur": initial,
            "positions": {},
            "total_value_eur": balance,
            "total_pnl_eur": balance - initial,
            "total_pnl_pct": (balance - initial) / initial * 100 if initial else 0,
        }

    def get(self):
        return self._state.copy()

    async def update_balance(self, delta):
        self._state["balance_eur"] = round(self._state["balance_eur"] + delta, 4)

    async def _save(self, state):
        self._state = state

    async def add_position(self, pair, position_data):
        self._state["positions"][pair] = position_data
        await self._save(self._state)

    async def remove_position(self, pair):
        self._state["positions"].pop(pair, None)


def make_level_state():
    return {
        "pair": PAIR,
        "current_price": 100.0,
        "center_price": 100.0,
        "lower": 95.0,
        "upper": 105.0,
        "spacing": 1.0,
        "leverage": 1,
        "levels": [
            {
                "id": 0, "price": 99.0, "side": "buy", "amount": 1.0,
                "value_eur": 99.0, "entry_price": 99.0, "status": "open",
            }
        ],
        "pnl_eur": 3.0,
        "pnl_pct": 3.0,
        "fees_eur": 0.05,
        "total_grid_trades": 300,
    }


async def case_restore_then_reconcile():
    """Estado histórico presente en Redis (aunque enabled=False) -> se restaura y
    el total_pnl_eur se reconcilia con el balance real del portfolio."""
    r = FakeRedis()
    r.store[f"grid:global"] = json.dumps({
        "enabled": False,
        "pairs": config.grid.pairs,
        "total_pnl_eur": 2.5,
        "total_grid_trades": 300,
        "total_fees_eur": 0.8,
        "started_at": "2026-08-28T07:19:00+00:00",
    })
    r.store[f"grid:state:{PAIR}"] = json.dumps(make_level_state())

    gs_ = GridStrategy(r, FakePortfolio(balance=103.0, initial=100.0))
    await gs_.start()

    assert gs_._running is True, "grid debe quedar running tras el restore"
    st = gs_._global_state
    assert st["started_at"] == "2026-08-28T07:19:00+00:00", \
        f"started_at debe preservarse, got {st['started_at']}"
    assert st["total_grid_trades"] == 300, \
        f"total_grid_trades debe preservarse, got {st['total_grid_trades']}"
    # Reconciliación: el PnL mostrado por el grid debe cuadrar con balance-inicial
    assert st["total_pnl_eur"] == 3.0, \
        f"total_pnl_eur debe reconciliarse a 3.0 (103-100), got {st['total_pnl_eur']}"
    assert gs_._state[PAIR]["total_grid_trades"] == 300, "niveles/estado por par deben mantenerse"
    print("CASE restore+reconcile OK: estado preservado, PnL reconciliado a 3.0")


async def case_no_state_initializes():
    """Sin estado histórico en Redis -> el grid se inicializa desde cero con niveles."""
    r = FakeRedis()
    r.store[f"price:{PAIR}"] = str(100.0)
    gs_ = GridStrategy(r, FakePortfolio(balance=100.0, initial=100.0))
    await gs_.start()

    assert gs_._running is True
    assert len(gs_._state[PAIR]["levels"]) > 0, "deben crearse niveles"
    assert gs_._global_state["total_pnl_eur"] == 0.0, \
        f"sin historial el PnL parte de 0, got {gs_._global_state['total_pnl_eur']}"
    assert gs_._global_state["started_at"], "started_at debe establecerse"
    print(f"CASE no-state OK: grid inicializado con {len(gs_._state[PAIR]['levels'])} niveles")


async def case_stop_preserves_pnl():
    """stop() NO debe borrar el histórico de PnL del grid (solo pausa)."""
    r = FakeRedis()
    r.store[f"grid:global"] = json.dumps({
        "enabled": True, "pairs": config.grid.pairs,
        "total_pnl_eur": 3.0, "total_grid_trades": 300, "total_fees_eur": 0.8,
        "started_at": "2026-08-28T07:19:00+00:00",
    })
    r.store[f"grid:state:{PAIR}"] = json.dumps(make_level_state())
    gs_ = GridStrategy(r, FakePortfolio(balance=103.0, initial=100.0))
    await gs_.start()
    await gs_.stop()

    assert gs_._global_state["enabled"] is False, "stop() debe poner enabled=False"
    assert gs_._global_state["total_pnl_eur"] == 3.0, \
        "stop() no debe borrar el PnL acumulado del grid"
    print("CASE stop preserves PnL OK: enabled=False pero total_pnl_eur=3.0 intacto")


async def main():
    await case_restore_then_reconcile()
    await case_no_state_initializes()
    await case_stop_preserves_pnl()
    print("ALL GRID RESTORE/STATE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
