"""Regresión de liquidaciones fantasma al recentrar el grid.

Una orden de apertura resting (nunca ejecutada) no es una posición: al
rebalancear debe cancelarse sin PnL ni trade. Solo se liquidan las
contra-órdenes de cierre de piernas ya ejecutadas, y a comisión taker.
"""
from datetime import datetime, timezone

import pytest

from config import config
from database.init_db import SessionLocal
from database.models import Trade


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _level(
    level_id,
    side,
    price,
    *,
    is_opening,
    status="open",
    entry_price=None,
    cycle_id=None,
    filled_at=None,
    amount=1.0,
    opened_at=None,
):
    return {
        "id": level_id,
        "is_opening": is_opening,
        "side": side,
        "price": price,
        "entry_price": price if entry_price is None else entry_price,
        "amount": amount,
        "value_eur": round(price * amount, 2),
        "status": status,
        "filled_at": filled_at,
        "filled_price": price if status == "filled" else None,
        "cycle_id": cycle_id,
        "opened_at": opened_at,
    }


def _set_state(grid, levels, *, price=100.0):
    grid._state["BTC/EUR"] = {
        "pair": "BTC/EUR",
        "current_price": price,
        "center_price": 100.0,
        "lower": 95.0,
        "upper": 105.0,
        "spacing": 1.0,
        "leverage": 1,
        "levels": levels,
        "pnl_eur": 0.0,
        "pnl_pct": 0.0,
        "fees_eur": 0.0,
        "margin_fees_eur": 0.0,
        "total_grid_trades": 0,
        "started_at": _ts(),
    }


def _rebalance_trades():
    with SessionLocal() as db:
        return db.query(Trade).filter(Trade.reason == "rebalance").all()


async def test_ciclo_completo_no_genera_trade_fantasma(grid, portfolio):
    """Tras cerrar el ciclo, la siguiente apertura resting se cancela sin PnL."""
    opened = _ts()
    _set_state(grid, [
        _level(0, "sell", 101, is_opening=True, status="filled",
               cycle_id="c1", filled_at=opened, opened_at=opened),
        _level("0_buy_1", "buy", 100, is_opening=False, status="filled",
               entry_price=101, cycle_id="c1", filled_at=_ts(), opened_at=opened),
        # Contra-orden de apertura creada al cerrar: nunca se ejecutó.
        _level("0_buy_1_sell_2", "sell", 101, is_opening=True,
               entry_price=100, cycle_id="c1"),
    ])

    balance_before = portfolio.get()["balance_eur"]
    liquidated = await grid._liquidate_pair_positions("BTC/EUR", 109.0)

    assert liquidated == 0
    assert _rebalance_trades() == []
    assert portfolio.get()["balance_eur"] == balance_before
    assert grid._state["BTC/EUR"]["levels"][2]["status"] == "canceled"


async def test_long_vivo_liquida_con_taker_fee(grid, portfolio):
    opened = _ts()
    _set_state(grid, [
        _level(0, "buy", 100, is_opening=True, status="filled",
               cycle_id="c1", filled_at=opened, opened_at=opened),
        _level("0_sell_1", "sell", 101, is_opening=False,
               entry_price=100, cycle_id="c1", opened_at=opened),
    ])

    liquidated = await grid._liquidate_pair_positions("BTC/EUR", 91.0)

    expected_fee = round(91.0 * 1.0 * config.exchange.taker_fee, 4)
    expected_pnl = round((91.0 - 100.0) * 1.0 - expected_fee, 4)
    trades = _rebalance_trades()
    assert liquidated == 1
    assert len(trades) == 1
    assert trades[0].side == "SELL"
    assert trades[0].pnl_eur == pytest.approx(expected_pnl)
    assert trades[0].fee_eur == pytest.approx(expected_fee)
    assert expected_pnl < 0
    assert portfolio.get()["balance_eur"] == pytest.approx(1000.0 + expected_pnl)


async def test_short_vivo_liquida_en_positivo(grid, portfolio):
    opened = _ts()
    _set_state(grid, [
        _level(0, "sell", 101, is_opening=True, status="filled",
               cycle_id="c1", filled_at=opened, opened_at=opened),
        _level("0_buy_1", "buy", 100, is_opening=False,
               entry_price=101, cycle_id="c1", opened_at=opened),
    ])

    liquidated = await grid._liquidate_pair_positions("BTC/EUR", 91.0)

    expected_fee = round(91.0 * 1.0 * config.exchange.taker_fee, 4)
    expected_pnl = round((101.0 - 91.0) * 1.0 - expected_fee, 4)
    trades = _rebalance_trades()
    assert liquidated == 1
    assert len(trades) == 1
    assert trades[0].side == "BUY"
    assert trades[0].pnl_eur == pytest.approx(expected_pnl)
    assert expected_pnl > 0


async def test_aperturas_resting_sin_cycle_id_se_cancelan(grid, portfolio):
    _set_state(grid, [
        _level(0, "buy", 98, is_opening=True),
        _level(1, "buy", 99, is_opening=True),
        _level(2, "sell", 101, is_opening=True),
    ])

    liquidated = await grid._liquidate_pair_positions("BTC/EUR", 109.0)

    assert liquidated == 0
    assert _rebalance_trades() == []
    assert [lvl["status"] for lvl in grid._state["BTC/EUR"]["levels"]] == [
        "canceled", "canceled", "canceled",
    ]
