"""Contabilidad unificada: balance, margen, vinculo apertura-cierre y migraciones."""
import importlib
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text

from config import config
from database.crud import _build_round_trips, create_trade, get_stats_summary
from database.init_db import SessionLocal
from database.models import Trade
from trading.accounting import (
    margin_short_open,
    trade_ledger_delta,
    trade_margin_fee,
)


def test_trade_ledger_delta_cierre_y_aperturas():
    assert trade_ledger_delta("BUY", 2.5, 0.1, 0.9, 1.0, 100.0, 0.0002) == 2.5
    assert trade_ledger_delta("SELL", None, 0.1, 0.02, 1.0, 100.0, 0.0002) == pytest.approx(-0.12)
    assert trade_ledger_delta("BUY", None, 0.1, None, 1.0, 100.0, 0.0002) == pytest.approx(-0.1)


def test_fila_antigua_sin_margin_fee_usa_fallback():
    fee_open = 0.1
    fallback = margin_short_open(100.0, 0.0002)
    assert trade_margin_fee("SELL", None, None, 1.0, 100.0, 0.0002) == fallback
    assert trade_ledger_delta(
        "SELL", None, fee_open, None, 1.0, 100.0, 0.0002
    ) == pytest.approx(-fee_open - fallback)


def _trade(db, **kwargs):
    data = {
        "pair": "BTC/EUR",
        "amount_crypto": 1.0,
        "amount_eur": 100.0,
        "price": 100.0,
        "fee_eur": 0.0,
        "mode": "demo",
        "reason": "grid",
        "timestamp": datetime.utcnow(),
    }
    data.update(kwargs)
    return create_trade(db, data)


def test_stats_cuadra_con_short_y_comision_de_margen():
    with SessionLocal() as db:
        opening = _trade(
            db,
            side="SELL",
            price=100.0,
            fee_eur=0.1,
            margin_fee_eur=0.02,
            pnl_eur=None,
            cycle_id="short-1",
        )
        closing = _trade(
            db,
            side="BUY",
            price=90.0,
            entry_price=100.0,
            fee_eur=0.09,
            margin_fee_eur=0.0,
            pnl_eur=9.91,
            open_trade_id=opening.id,
            cycle_id="short-1",
        )
        summary = get_stats_summary(db)
        closed, open_rows = _build_round_trips([opening, closing])

    expected = round(9.91 - 0.1 - 0.02, 4)
    assert summary["total_pnl_eur"] == pytest.approx(expected)
    assert summary["total_margin_fees_eur"] == pytest.approx(0.02)
    assert summary["total_fees_eur"] == pytest.approx(0.19)

    assert len(closed) == 1 and not open_rows
    assert closed[0]["entry_price"] == 100.0
    assert closed[0]["pnl_eur"] == pytest.approx(9.81)
    assert round(
        sum(r["pnl_eur"] for r in closed)
        - sum(r["entry_fee"] for r in open_rows)
        - summary["total_margin_fees_eur"],
        4,
    ) == summary["total_pnl_eur"]


def test_open_trade_id_empareja_exacto_sin_heuristica():
    with SessionLocal() as db:
        first = _trade(db, side="BUY", fee_eur=0.01, pnl_eur=None, cycle_id="A")
        second = _trade(db, side="BUY", fee_eur=0.02, pnl_eur=None, cycle_id="B")
        close_second = _trade(
            db,
            side="SELL",
            price=103.0,
            entry_price=100.0,
            fee_eur=0.01,
            pnl_eur=2.99,
            open_trade_id=second.id,
            cycle_id="B",
        )
        close_first = _trade(
            db,
            side="SELL",
            price=103.0,
            entry_price=100.0,
            fee_eur=0.01,
            pnl_eur=2.99,
            open_trade_id=first.id,
            cycle_id="A",
        )
        closed, open_rows = _build_round_trips(
            [first, second, close_second, close_first]
        )

    assert not open_rows
    by_cycle = {row["id"]: row for row in closed}
    assert by_cycle["A"]["entry_fee"] == 0.01
    assert by_cycle["B"]["entry_fee"] == 0.02


def test_filas_antiguas_siguen_emparejando_por_precio():
    with SessionLocal() as db:
        opening = _trade(db, side="BUY", fee_eur=0.01, pnl_eur=None, cycle_id="old")
        closing = _trade(
            db,
            side="SELL",
            price=103.0,
            entry_price=None,
            fee_eur=0.01,
            pnl_eur=2.99,
            open_trade_id=None,
            cycle_id="old",
        )
        closed, open_rows = _build_round_trips([opening, closing])

    assert not open_rows
    assert closed[0]["entry_price"] == 100.0
    assert closed[0]["entry_fee"] == 0.01


def test_init_db_migra_columnas_y_es_idempotente(monkeypatch, tmp_path):
    path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, pair VARCHAR(20), side VARCHAR(13), "
            "amount_crypto FLOAT, amount_eur FLOAT, price FLOAT, fee_eur FLOAT, "
            "timestamp DATETIME, mode VARCHAR(4))"
        ))
    engine.dispose()

    monkeypatch.setattr(config.database, "sqlite_path", str(path))
    db_init = importlib.import_module("database.init_db")

    db_init.init_db()
    db_init.init_db()

    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(trades)")).fetchall()
        }
        indexes = {
            row[1] for row in conn.execute(text("PRAGMA index_list(trades)")).fetchall()
        }
    engine.dispose()

    assert {"margin_fee_eur", "open_trade_id"} <= columns
    assert "idx_trades_open_trade" in indexes


async def test_persist_grid_fill_guarda_margin_y_vinculo(grid):
    opening = {
        "id": 0,
        "is_opening": True,
        "side": "sell",
        "amount": 1.0,
        "price": 100.0,
        "entry_price": 100.0,
        "value_eur": 100.0,
        "cycle_id": "c1",
        "status": "open",
        "margin_fees_eur": 0.02,
        "order_id": "ord-open",
    }
    open_id = await grid._persist_grid_fill(
        "BTC/EUR", opening, 100.0, -0.1, 0.1, exchange_order_id="ord-open"
    )
    assert open_id

    closing = {
        "id": "0_sell_1",
        "is_opening": False,
        "side": "buy",
        "amount": 1.0,
        "price": 90.0,
        "entry_price": 100.0,
        "value_eur": 90.0,
        "cycle_id": "c1",
        "status": "filled",
        "margin_fees_eur": 0.03,
        "opened_by_trade_id": open_id,
        "order_id": "ord-close",
    }
    close_id = await grid._persist_grid_fill(
        "BTC/EUR", closing, 90.0, 9.8, 0.09, exchange_order_id="ord-close"
    )

    with SessionLocal() as db:
        open_row = db.query(Trade).filter_by(id=open_id).one()
        close_row = db.query(Trade).filter_by(id=close_id).one()

    assert open_row.margin_fee_eur == pytest.approx(0.02)
    assert open_row.open_trade_id is None
    assert open_row.exchange_order_id == "ord-open"
    assert close_row.margin_fee_eur == 0.0
    assert close_row.open_trade_id == open_id
    assert close_row.exchange_order_id == "ord-close"


async def test_ciclo_short_real_cuadra_stats_con_balance(grid, portfolio):
    level = {
        "id": 0,
        "is_opening": True,
        "side": "sell",
        "amount": 1.0,
        "price": 100.0,
        "entry_price": 100.0,
        "value_eur": 100.0,
        "cycle_id": "short-real",
        "status": "open",
        "filled_at": None,
    }
    grid._state["BTC/EUR"] = {
        "pair": "BTC/EUR",
        "spacing": 1.0,
        "levels": [level],
        "pnl_eur": 0.0,
        "fees_eur": 0.0,
        "margin_fees_eur": 0.0,
        "total_grid_trades": 0,
        "pnl_pct": 0.0,
        "current_price": 100.0,
    }

    await grid._handle_fill("BTC/EUR", level, 100.0)
    closing = grid._state["BTC/EUR"]["levels"][-1]
    await grid._handle_fill("BTC/EUR", closing, 90.0)

    with SessionLocal() as db:
        summary = get_stats_summary(db)
        opening = (
            db.query(Trade)
            .filter_by(cycle_id="short-real", pnl_eur=None)
            .one()
        )
        closed = (
            db.query(Trade)
            .filter_by(cycle_id="short-real", open_trade_id=opening.id)
            .one()
        )

    assert closed.open_trade_id == opening.id
    assert opening.margin_fee_eur == pytest.approx(
        margin_short_open(100.0, config.grid.margin_open_fee_pct)
    )
    assert summary["total_pnl_eur"] == pytest.approx(
        portfolio.get()["balance_eur"] - 1000.0, abs=1e-4
    )
    assert summary["total_margin_fees_eur"] == pytest.approx(
        opening.margin_fee_eur
    )


async def test_handle_fill_vincula_la_contraorden(grid):
    level = {
        "id": 0,
        "is_opening": True,
        "side": "buy",
        "amount": 1.0,
        "price": 100.0,
        "entry_price": 100.0,
        "value_eur": 100.0,
        "cycle_id": "c1",
        "status": "open",
        "filled_at": None,
    }
    grid._state["BTC/EUR"] = {
        "pair": "BTC/EUR",
        "spacing": 1.0,
        "levels": [level],
        "pnl_eur": 0.0,
        "fees_eur": 0.0,
        "margin_fees_eur": 0.0,
        "total_grid_trades": 0,
        "pnl_pct": 0.0,
        "current_price": 100.0,
    }

    await grid._handle_fill("BTC/EUR", level, 100.0)

    new_level = grid._state["BTC/EUR"]["levels"][-1]
    assert new_level["opened_by_trade_id"]
    with SessionLocal() as db:
        opening = db.query(Trade).filter_by(id=new_level["opened_by_trade_id"]).one()
    assert opening.side == "BUY"
    assert opening.pnl_eur is None
