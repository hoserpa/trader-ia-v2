"""Prueba unitaria de la correccion de PnL del grid con slippage.

El bug: la pierna de apertura acreditaba la mejora de precio no realizada (slippage
favorable) y la pierna de cierre volvia a contar esa misma mejora (doble conteo).
Con fills exactos al limite (epsilon=0) no se notaba, por eso los tests originales
pasaban. Con slippaje real el balance quedaba inflado.

Correccion: la apertura debita solo su comision (-fee); el cierre acredita el PnL
realizado del ciclo. El PnL neto por ciclo debe coincidir con
(precio_venta_real - precio_compra_real) * cantidad - 2 comisiones.
"""
import asyncio
import sys

sys.path.insert(0, "bot")

from config import config

RATE = config.exchange.maker_fee


def fee_eur(price, amount=1.0):
    return price * amount * RATE


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


async def case_buy_slippage():
    """Ciclo largo con slippage favorable en la compra.

    Buy limite 100 se llena a 99 (mejora de 1). Sell derivado (target 101) se llena
    a 101 exacto. PnL REAL = (101-99)*1 - 2 fees = 2 - 2 fees.
    Antes del fix el balance daba 3 - 2 fees (inflado en 1).
    """
    from strategies.grid_strategy import GridStrategy

    r = FakeRedis()
    gs = GridStrategy(r, FakePortfolio())
    gs._global_state = {"enabled": True, "total_pnl_eur": 0.0, "total_fees_eur": 0.0, "total_grid_trades": 0}
    gs._state["PAIR"] = make_grid_state()

    buy_init = {
        "id": 0, "price": 100.0, "side": "buy", "amount": 1.0,
        "value_eur": 100.0, "entry_price": 100.0, "status": "open",
    }
    gs._state["PAIR"]["levels"].append(buy_init)

    # Apertura BUY con slippage: limite 100, fill a 99
    await gs._handle_fill("PAIR", buy_init, 99.0)
    s = gs._state["PAIR"]
    buy_fee = fee_eur(99.0)

    # La apertura NO debe acreditar la mejora no realizada (+1). Solo -fee.
    assert round4(s["pnl_eur"]) == round4(-buy_fee), \
        f"Apertura debe ser solo -fee ({-buy_fee:.4f}), got {s['pnl_eur']:.4f}"
    assert abs(gs.portfolio.get()["balance_eur"] - (100.0 - buy_fee)) < 1e-3, \
        f"Balance tras apertura {gs.portfolio.get()['balance_eur']:.4f}"
    print(f"CASE buy-slippage OK: apertura -> PnL {s['pnl_eur']:+.4f} (solo -fee), balance {gs.portfolio.get()['balance_eur']:.4f}")

    # Tras la apertura la posicion esta abierta en el portafolio
    pos = gs.portfolio.get()["positions"]
    assert "PAIR" in pos and pos["PAIR"]["position_type"] == "long", \
        f"Posicion long debe estar abierta tras BUY: {pos}"
    print(f"CASE buy-slippage OK: posicion abierta long @ {pos['PAIR']['entry_price']}")

    # Cierre SELL: derivado target 101, fill a 101 (exacto)
    sell_der = gs._state["PAIR"]["levels"][-1]
    assert isinstance(sell_der["id"], str), "derivado debe ser str id"
    assert sell_der["price"] == 101.0
    await gs._handle_fill("PAIR", sell_der, 101.0)
    s = gs._state["PAIR"]
    sell_fee = fee_eur(101.0)

    expected = (101.0 - 99.0) - buy_fee - sell_fee
    assert abs(s["pnl_eur"] - expected) < 1e-6, \
        f"Ciclo PnL esperado {expected:.4f} (real, sin inflar), got {s['pnl_eur']:.4f}"
    expected_balance = 100.0 + expected
    assert abs(gs.portfolio.get()["balance_eur"] - expected_balance) < 1e-3, \
        f"Balance esperado {expected_balance:.4f}, got {gs.portfolio.get()['balance_eur']:.4f}"
    print(f"CASE buy-slippage OK: ciclo -> PnL neto {s['pnl_eur']:+.4f} = {expected:.4f}, balance {gs.portfolio.get()['balance_eur']:.4f}")

    # Tras el cierre la posicion debe eliminarse del portafolio
    assert "PAIR" not in gs.portfolio.get()["positions"], \
        "Posicion debe eliminarse al cerrarse el ciclo"
    print("CASE buy-slippage OK: posicion removida al cerrar")


async def case_sell_slippage():
    """Ciclo corto con slippage favorable en la venta.

    Sell limite 103 se llena a 104 (mejora de 1). Buy derivado (target 102) se llena
    a 102 exacto. PnL REAL = (104-102)*1 - 2 fees = 2 - 2 fees.
    """
    from strategies.grid_strategy import GridStrategy

    r = FakeRedis()
    gs = GridStrategy(r, FakePortfolio())
    gs._global_state = {"enabled": True, "total_pnl_eur": 0.0, "total_fees_eur": 0.0, "total_grid_trades": 0}
    gs._state["PAIR"] = make_grid_state()

    sell_init = {
        "id": 0, "price": 103.0, "side": "sell", "amount": 1.0,
        "value_eur": 103.0, "entry_price": 103.0, "status": "open",
    }
    gs._state["PAIR"]["levels"].append(sell_init)

    # Apertura SELL con slippage: limite 103, fill a 104
    await gs._handle_fill("PAIR", sell_init, 104.0)
    s = gs._state["PAIR"]
    sell_fee = fee_eur(104.0)
    assert round4(s["pnl_eur"]) == round4(-sell_fee), \
        f"Apertura SELL debe ser solo -fee ({-sell_fee:.4f}), got {s['pnl_eur']:.4f}"

    pos = gs.portfolio.get()["positions"]
    assert "PAIR" in pos and pos["PAIR"]["position_type"] == "short", \
        f"Posicion short debe estar abierta tras SELL: {pos}"
    print(f"CASE sell-slippage OK: posicion abierta short @ {pos['PAIR']['entry_price']}")

    # Cierre BUY: derivado target 102, fill a 102
    buy_der = gs._state["PAIR"]["levels"][-1]
    assert isinstance(buy_der["id"], str)
    assert buy_der["price"] == 102.0
    await gs._handle_fill("PAIR", buy_der, 102.0)
    s = gs._state["PAIR"]
    buy_fee = fee_eur(102.0)

    expected = (104.0 - 102.0) - sell_fee - buy_fee
    assert abs(s["pnl_eur"] - expected) < 1e-6, \
        f"Ciclo corto PnL esperado {expected:.4f}, got {s['pnl_eur']:.4f}"
    assert abs(gs.portfolio.get()["balance_eur"] - (100.0 + expected)) < 1e-3
    assert "PAIR" not in gs.portfolio.get()["positions"], \
        "Posicion short debe eliminarse al cerrarse el ciclo"
    print(f"CASE sell-slippage OK: ciclo corto -> PnL neto {s['pnl_eur']:+.4f} = {expected:.4f}")


async def case_no_slippage_matches_existing():
    """Sin slippage (fills exactos al limite), los resultados deben coincidir con el
    test original test_grid_pnl.py (epsilon=0)."""
    from strategies.grid_strategy import GridStrategy

    r = FakeRedis()
    gs = GridStrategy(r, FakePortfolio())
    gs._global_state = {"enabled": True, "total_pnl_eur": 0.0, "total_fees_eur": 0.0, "total_grid_trades": 0}
    gs._state["PAIR"] = make_grid_state()

    buy_init = {
        "id": 0, "price": 99.0, "side": "buy", "amount": 1.0,
        "value_eur": 99.0, "entry_price": 99.0, "status": "open",
    }
    gs._state["PAIR"]["levels"].append(buy_init)
    await gs._handle_fill("PAIR", buy_init, 99.0)
    buy_fee = fee_eur(99.0)
    assert round4(gs._state["PAIR"]["pnl_eur"]) == round4(-buy_fee)

    sell_der = gs._state["PAIR"]["levels"][-1]
    await gs._handle_fill("PAIR", sell_der, 100.0)
    sell_fee = fee_eur(100.0)
    expected = (100.0 - 99.0) - buy_fee - sell_fee
    assert abs(gs._state["PAIR"]["pnl_eur"] - expected) < 1e-6
    assert abs(gs.portfolio.get()["balance_eur"] - (100.0 + expected)) < 1e-3
    print(f"CASE no-slippage OK: coincide con test original -> PnL neto {gs._state['PAIR']['pnl_eur']:+.4f} = {expected:.4f}")


async def main():
    await case_buy_slippage()
    await case_sell_slippage()
    await case_no_slippage_matches_existing()
    print("ALL GRID PNL SLIPPAGE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
