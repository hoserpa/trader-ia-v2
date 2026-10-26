"""Prueba unitaria de la logica de PnL del grid (neto de comisiones Kraken) + persistencia al portfolio."""
import asyncio
import sys

sys.path.insert(0, "bot")

from config import config

RATE = config.exchange.maker_fee


def fee_eur(price, amount=1.0):
    """Comision en EUR para un fill."""
    return price * amount * RATE


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

    async def add_position(self, pair, position_data):
        self._state["positions"][pair] = position_data
        await self._save(self._state)

    async def remove_position(self, pair):
        self._state["positions"].pop(pair, None)


def make_grid_state():
    return {
        "pair": "PAIR",
        "current_price": 100.0,
        "center_price": 100.0,
        "lower": 95.0,
        "upper": 105.0,
        "spacing": 1.0,
        "leverage": 1,
        "levels": [],
        "pnl_eur": 0.0,
        "pnl_pct": 0.0,
        "fees_eur": 0.0,
        "total_grid_trades": 0,
    }


def round4(x):
    return round(x, 4)


async def main():
    from strategies.grid_strategy import GridStrategy

    # ---- CASE 1: ciclo largo. BUY inicial 99 -> SELL derivado 100 ----
    r = FakeRedis()
    gs = GridStrategy(r, FakePortfolio())
    gs._global_state = {"enabled": True, "total_pnl_eur": 0.0, "total_fees_eur": 0.0, "total_grid_trades": 0}
    gs._state["PAIR"] = make_grid_state()

    # BUY inicial (id int) a 99 se llena a su precio (99)
    buy_init = {
        "id": 0, "price": 99.0, "side": "buy", "amount": 1.0,
        "value_eur": 99.0, "entry_price": 99.0, "status": "open",
    }
    gs._state["PAIR"]["levels"].append(buy_init)
    await gs._handle_fill("PAIR", buy_init, 99.0)
    s = gs._state["PAIR"]

    # El BUY inicial: como entry=fill=99, pnl=(99-99)*1 - fee = -fee_eur solo comision
    buy_fee = fee_eur(99.0)
    assert round4(s["pnl_eur"]) == round4(-buy_fee), f"BUY inicial neto debe ser -{buy_fee}, got {s['pnl_eur']}"
    assert s["total_grid_trades"] == 1
    print(f"CASE1a OK: BUY inicial @99 -> PnL {s['pnl_eur']:+.4f} (solo comision {buy_fee:.4f})")

    # SELL derivado a 100 cierra el largo (spread=1)
    sell_der = gs._state["PAIR"]["levels"][-1]
    assert isinstance(sell_der["id"], str), "counter derivado debe tener id string"
    assert sell_der["price"] == 100.0
    await gs._handle_fill("PAIR", sell_der, 100.0)
    s = gs._state["PAIR"]

    # PnL neto del ciclo completo: spread(1) - 2 comisiones en EUR (a 99 y 100)
    sell_fee = fee_eur(100.0)
    expected_pair_pnl = (100.0 - 99.0) - buy_fee - sell_fee
    assert abs(s["pnl_eur"] - expected_pair_pnl) < 1e-6, f"Esperado {expected_pair_pnl}, got {s['pnl_eur']}"
    print(f"CASE1b OK: ciclo largo completo -> PnL neto {s['pnl_eur']:+.4f} (spread 1 - {buy_fee:.4f} - {sell_fee:.4f})")

    # CASE 1c: portfolio debe acreditar el PnL neto de ambos fills (balance sincronizado)
    port = gs.portfolio
    expected_balance = 100.0 + expected_pair_pnl
    assert abs(port.get()["balance_eur"] - expected_balance) < 1e-3, \
        f"Balance esperado {expected_balance:.4f}, got {port.get()['balance_eur']:.4f}"
    assert round4(port.get()["total_pnl_eur"]) == round4(expected_balance - 100.0)
    ws = [m for (ch, m) in r.published if "portfolio_update" in m]
    assert len(ws) >= 2, f"Esperados portfolio_update en WS, got {len(ws)}"
    print(f"CASE1c OK: portfolio balance {port.get()['balance_eur']:.4f}, WS updates={len(ws)}")

    # ---- CASE 2: ciclo corto. SELL inicial 101 -> BUY derivado 100 ----
    r2 = FakeRedis()
    gs2 = GridStrategy(r2, FakePortfolio())
    gs2._global_state = {"enabled": True, "total_pnl_eur": 0.0, "total_fees_eur": 0.0, "total_grid_trades": 0}
    gs2._state["PAIR"] = make_grid_state()
    sell_init = {
        "id": 0, "price": 101.0, "side": "sell", "amount": 1.0,
        "value_eur": 101.0, "entry_price": 101.0, "status": "open",
    }
    gs2._state["PAIR"]["levels"].append(sell_init)
    await gs2._handle_fill("PAIR", sell_init, 101.0)
    s2 = gs2._state["PAIR"]
    sell_fee_init = fee_eur(101.0)
    assert round4(s2["pnl_eur"]) == round4(-sell_fee_init)

    buy_der = gs2._state["PAIR"]["levels"][-1]
    assert isinstance(buy_der["id"], str)
    assert buy_der["price"] == 100.0  # 101 - spacing
    await gs2._handle_fill("PAIR", buy_der, 100.0)
    s2 = gs2._state["PAIR"]
    buy_fee_der = fee_eur(100.0)
    expected2 = (101.0 - 100.0) - sell_fee_init - buy_fee_der
    assert abs(s2["pnl_eur"] - expected2) < 1e-6, f"Esperado {expected2}, got {s2['pnl_eur']}"
    assert abs(gs2.portfolio.get()["balance_eur"] - (100.0 + expected2)) < 1e-3
    print(f"CASE2 OK: ciclo corto completo -> PnL neto {s2['pnl_eur']:+.4f}, balance {gs2.portfolio.get()['balance_eur']:.4f}")

    # ---- CASE 3: fees acumuladas correctas (2 fills) ----
    s = gs._state["PAIR"]
    expected_fees = buy_fee + sell_fee
    assert abs(s["fees_eur"] - expected_fees) < 1e-6, f"fees_eur esperado {expected_fees}, got {s['fees_eur']}"
    assert abs(gs._global_state["total_fees_eur"] - expected_fees) < 1e-6
    print(f"CASE3 OK: fees acumuladas {s['fees_eur']:.4f} ({buy_fee:.4f}+{sell_fee:.4f})")

    print("ALL GRID PNL + COMMISSIONS TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
