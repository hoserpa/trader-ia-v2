"""Arnes de backtest que reutiliza el codigo del grid."""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

import fakeredis.aioredis as fakeredis

from config import config
from strategies.grid_strategy import GridStrategy
from trading.clock import SimClock
from trading.portfolio import Portfolio


async def run_backtest(config_overrides=None, start=None, end=None, resolution="5m",
                       fill_model="close", initial_balance=250.0, data=None):
    config_overrides = config_overrides or {}
    # apply overrides
    for k, v in config_overrides.items():
        if hasattr(config.grid, k):
            setattr(config.grid, k, v)
        elif hasattr(config.trading, k):
            setattr(config.trading, k, v)
        elif hasattr(config.exchange, k):
            setattr(config.exchange, k, v)

    tmpdir = tempfile.mkdtemp()
    os.environ["SQLITE_DB_PATH"] = os.path.join(tmpdir, "bt.db")
    config.database.sqlite_path = os.path.join(tmpdir, "bt.db")

    # reset db path cache
    import database.init_db as dbi

    dbi._session_factory = None
    dbi._session_factory_path = None
    dbi._engine = None

    redis = fakeredis.FakeRedis(decode_responses=True)
    portfolio = Portfolio(redis)
    await portfolio.initialize(initial_balance)

    clock = SimClock(start or datetime.now(timezone.utc))
    grid = GridStrategy(redis, portfolio, clock=clock)
    grid._running = True
    # margin support: assume shorts supported
    grid.set_margin_support({p: {"in_markets": True, "short_ok": True} for p in config.grid.pairs})

    # prices series: data is dict pair -> list of (ts, close)
    # Build unified timeline
    # For simplicity, assume data provided: dict pair -> list of closes aligned
    pairs = config.grid.pairs
    n = min(len(data[p]) for p in pairs)
    equity = []
    for i in range(n):
        prices = {}
        for p in pairs:
            ts, close = data[p][i]
            prices[p] = close
            await redis.set(f"price:{p}", str(close))
        clock.set(ts if isinstance(ts, datetime) else datetime.fromtimestamp(ts, tz=timezone.utc))
        await grid.check_orders()
        st = portfolio.get()
        equity.append(st.get("total_value_eur", st.get("balance_eur", initial_balance)))
    return {"equity": equity, "trades": [], "rebalances": []}


if __name__ == "__main__":
    print("use as module")
PY