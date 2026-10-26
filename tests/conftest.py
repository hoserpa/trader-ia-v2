"""Fixtures compartidas de los tests del grid.

Las variables de entorno se fijan ANTES de importar `config` o `database.*`
porque `bot/database/init_db.py` crea `SessionLocal` al importarse y
`bot/config.py` construye `config` al importarse.
"""
import os
import sys
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="grid_tests_")

os.environ["TRADING_MODE"] = "demo"
os.environ["DEMO_INITIAL_BALANCE"] = "1000"
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP_DIR, "test.db").replace("\\", "/")
os.environ["TRADING_PAIRS"] = "BTC/EUR"
os.environ["GRID_ENABLED"] = "true"
os.environ["GRID_PAIRS"] = "BTC/EUR"
os.environ["GRID_LEVERAGE"] = "1"
os.environ["GRID_LEVELS"] = "5"
os.environ["GRID_RANGE_PCT"] = "0.05"
os.environ["GRID_MIN_LOT_VALUE_EUR"] = "1"
os.environ["GRID_CAPITAL_PCT"] = "0.9"
os.environ["GRID_REBALANCE_THRESHOLD"] = "0.08"
os.environ["GRID_STOP_LOSS_PCT"] = "0.05"
os.environ["GRID_POLL_INTERVAL"] = "15"
os.environ["EXCHANGE_MARGIN_ENABLED"] = "true"
os.environ["EXCHANGE_MARGIN_LEVERAGE"] = "2"
os.environ["EXCHANGE_ALLOW_SHORT"] = "true"
os.environ["TELEGRAM_ENABLED"] = "false"

_BOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot")
if _BOT_DIR not in sys.path:
    sys.path.insert(0, _BOT_DIR)

# Artefactos locales obsoletos: el módulo trend_following ya no existe.
collect_ignore = [
    "test_trend_analytics.py",
    "test_trend_backtest.py",
    "test_trend_corrections.py",
    "test_trend_fase12.py",
    "test_trend_fase13.py",
    "test_trend_fase14.py",
    "test_trend_fase15.py",
    "test_trend_fase16.py",
    "test_trend_fase17.py",
    "test_trend_fase18.py",
    "test_trend_fase19.py",
    "test_trend_fase20.py",
    "test_trend_fase21.py",
    "test_trend_fase22.py",
    "test_trend_fase23.py",
    "test_trend_idempotency.py",
    "test_trend_indicators.py",
    "test_trend_portfolio.py",
    "test_trend_signals.py",
    "test_trend_sizing.py",
    "check_trades.py",
    "telegram_md_test.py",
    "telegram_test.py",
    "verify_deploy.py",
]

import fakeredis.aioredis
import pytest
import pytest_asyncio

from config import config
from strategies.grid_strategy import GridStrategy
from trading.portfolio import Portfolio


@pytest.fixture(autouse=True)
def clean_db():
    """Base SQLite temporal limpia para cada test."""
    from database.init_db import get_engine
    from database.models import Base

    engine = get_engine()
    Base.metadata.create_all(engine)
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    engine.dispose()


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def portfolio(redis_client):
    port = Portfolio(redis_client)
    await port.initialize(1000.0)
    return port


@pytest_asyncio.fixture
async def grid(redis_client, portfolio, monkeypatch):
    monkeypatch.setattr(config.grid, "pairs", ["BTC/EUR"])
    monkeypatch.setattr(config.trading, "pairs", ["BTC/EUR"])
    strategy = GridStrategy(redis_client, portfolio)
    strategy.set_margin_support(
        {"BTC/EUR": {"in_markets": True, "short_ok": True}}
    )
    strategy._running = True
    return strategy


async def set_price(redis_client, pair: str, price: float) -> None:
    await redis_client.set(f"price:{pair}", str(price))
