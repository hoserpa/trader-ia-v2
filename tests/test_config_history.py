"""Tests de instrumentacion de configuracion y rebalanceos."""
import hashlib
import json

from config import config
from database.crud import create_config_history, create_rebalance_event, get_config_history, get_rebalance_events
from database.init_db import SessionLocal
from database.models import ConfigHistory, RebalanceEvent
from strategies.grid_strategy import GridStrategy


def test_config_history_solo_registra_cambios():
    with SessionLocal() as db:
        db.query(ConfigHistory).delete()
        db.commit()
        create_config_history(db, {
            "config_hash": "a",
            "config_json": "{}",
            "source": "startup",
        })
        create_config_history(db, {
            "config_hash": "a",
            "config_json": "{}",
            "source": "startup",
        })
        assert db.query(ConfigHistory).count() == 2


def test_rebalance_event_persiste():
    with SessionLocal() as db:
        db.query(RebalanceEvent).delete()
        db.commit()
        ev = create_rebalance_event(db, {
            "pair": "BTC/EUR",
            "price": 100.0,
            "positions_closed": 2,
            "resting_cancelled": 1,
            "realized_pnl_eur": 1.5,
        })
        assert ev.id is not None
        assert get_rebalance_events(db)[0].pair == "BTC/EUR"


def test_config_hash_estable_y_sin_secretos():
    import asyncio
    redis = None
    import fakeredis.aioredis as f
    r = f.FakeRedis(decode_responses=True)
    from trading.portfolio import Portfolio
    import asyncio
    port = Portfolio(r)
    asyncio.run(port.initialize(100.0))
    g = GridStrategy(r, port)
    h1 = g._compute_config_hash()
    h2 = g._compute_config_hash()
    assert h1 == h2
    assert len(h1) == 64
    dumped = json.dumps(g._effective_config_dict())
    assert "api_key" not in dumped
    assert "api_secret" not in dumped
