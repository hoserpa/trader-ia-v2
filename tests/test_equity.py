"""Patrimonio con no realizado y stop-loss global sobre patrimonio."""
from datetime import datetime, timedelta

import pytest

from config import config
from database.init_db import SessionLocal
from database.models import PortfolioSnapshot
from database.crud import calculate_max_drawdown_from_snapshots


def _state(grid, levels, current_price):
    grid._state["BTC/EUR"] = {
        "pair": "BTC/EUR",
        "current_price": current_price,
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
        "started_at": datetime.utcnow().isoformat(),
    }


def _held(side, entry, amount=1.0):
    return {
        "id": f"0_{'sell' if side == 'long' else 'buy'}_1",
        "is_opening": False,
        "side": "sell" if side == "long" else "buy",
        "amount": amount,
        "price": entry,
        "entry_price": entry,
        "value_eur": entry * amount,
        "status": "open",
        "filled_at": None,
    }


def _resting(side, price, amount=1.0):
    return {
        "id": 1,
        "is_opening": True,
        "side": side,
        "amount": amount,
        "price": price,
        "entry_price": price,
        "value_eur": price * amount,
        "status": "open",
        "filled_at": None,
    }


def test_equity_long_suma_no_realizado(grid, portfolio):
    _state(grid, [_held("long", 100.0)], 110.0)
    assert grid._equity_eur(portfolio.get()) == 1010.0


def test_equity_short_resta_no_realizado(grid, portfolio):
    _state(grid, [_held("short", 100.0)], 110.0)
    assert grid._equity_eur(portfolio.get()) == 990.0


def test_apertura_resting_no_aporta_equity(grid, portfolio):
    _state(grid, [_resting("buy", 90.0)], 110.0)
    assert grid._equity_eur(portfolio.get()) == 1000.0
    assert grid.open_positions_for_valuations()["BTC/EUR"] == []


async def test_update_valuations_usa_lado_mantenido(grid, portfolio):
    _state(grid, [_held("long", 100.0)], 110.0)
    state = await portfolio.update_valuations(
        {"BTC/EUR": 110.0}, grid.open_positions_for_valuations()
    )
    assert state["total_value_eur"] == 1010.0
    assert state["total_pnl_eur"] == 10.0

    _state(grid, [_held("short", 100.0)], 110.0)
    state = await portfolio.update_valuations(
        {"BTC/EUR": 110.0}, grid.open_positions_for_valuations()
    )
    assert state["total_value_eur"] == 990.0
    assert state["total_pnl_eur"] == -10.0


def test_max_drawdown_refleja_caida_latente():
    base = datetime.utcnow()
    with SessionLocal() as db:
        db.add_all([
            PortfolioSnapshot(
                timestamp=base,
                balance_eur=1000.0,
                total_value_eur=1000.0,
                total_pnl_eur=0.0,
                total_pnl_pct=0.0,
            ),
            PortfolioSnapshot(
                timestamp=base + timedelta(minutes=1),
                balance_eur=1000.0,
                total_value_eur=980.0,
                total_pnl_eur=-20.0,
                total_pnl_pct=-2.0,
            ),
        ])
        db.commit()
        drawdown = calculate_max_drawdown_from_snapshots(db)

    assert drawdown == pytest.approx(2.0)


async def test_check_orders_actualiza_patrimonio(grid, portfolio, redis_client):
    """La valoración periódica ve el no realizado sin esperar a un cierre."""
    _state(grid, [_held("long", 100.0)], 100.0)
    grid._state["BTC/EUR"]["levels"][0]["price"] = 120.0
    await redis_client.set("price:BTC/EUR", "105")

    await grid.check_orders()

    assert portfolio.get()["total_value_eur"] == pytest.approx(1005.0)
    assert portfolio.get()["total_pnl_eur"] == pytest.approx(5.0)


async def test_stop_loss_se_dispara_con_perdida_latente(grid, portfolio):
    _state(grid, [_held("long", 100.0, amount=1.0)], 40.0)
    await portfolio.update_valuations(
        {"BTC/EUR": 40.0}, grid.open_positions_for_valuations()
    )
    stopped = []

    async def fake_stop():
        stopped.append(True)

    grid.stop = fake_stop
    await grid._check_global_stop_loss()

    assert stopped == [True]


async def test_stop_loss_no_se_dispara_sin_perdida_suficiente(grid, portfolio):
    _state(grid, [_held("long", 100.0, amount=0.1)], 90.0)
    await portfolio.update_valuations(
        {"BTC/EUR": 90.0}, grid.open_positions_for_valuations()
    )
    stopped = []

    async def fake_stop():
        stopped.append(True)

    grid.stop = fake_stop
    await grid._check_global_stop_loss()

    assert stopped == []
