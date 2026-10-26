"""Arnes de backtest que reutiliza el codigo del grid."""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

import fakeredis.aioredis as fakeredis
import pandas as pd

from config import config
from database.init_db import get_session_factory
from strategies.grid_strategy import GridStrategy
from tools.backtest.data import load_candles
from trading.clock import SimClock
from trading.portfolio import Portfolio


def _load_data(start, end, resolution):
    data = {}
    for pair in config.grid.pairs:
        path = f"data/history/{pair.replace('/', '_')}_1m.csv.gz"
        df = load_candles(path, start=start, end=end, resolution=resolution)
        data[pair] = df
    return data


async def run_backtest(config_overrides=None, start=None, end=None, resolution="5m",
                       fill_model="close", initial_balance=250.0, data=None):
    config_overrides = config_overrides or {}
    for k, v in config_overrides.items():
        if hasattr(config.grid, k):
            setattr(config.grid, k, v)
        elif hasattr(config.trading, k):
            setattr(config.trading, k, v)
        elif hasattr(config.exchange, k):
            setattr(config.exchange, k, v)

    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "bt.db")
    os.environ["SQLITE_DB_PATH"] = db_path
    config.database.sqlite_path = db_path

    import database.init_db as dbi

    dbi._engine = None
    dbi._session_factory = None
    dbi._session_factory_path = None

    redis = fakeredis.FakeRedis(decode_responses=True)
    portfolio = Portfolio(redis)
    await portfolio.initialize(initial_balance)

    if data is None:
        data = _load_data(start, end, resolution)

    pairs = config.grid.pairs
    # aligned timestamps
    frames = {p: data[p].copy() for p in pairs}
    for p in pairs:
        frames[p]["ts"] = pd.to_datetime(frames[p]["ts"], utc=True)
    # common index
    common = frames[pairs[0]]["ts"].tolist()
    for p in pairs[1:]:
        common = [t for t in common if t in set(frames[p]["ts"].tolist())]
    # sort
    common = sorted(set(common))

    # initial price
    first_ts = common[0]
    for p in pairs:
        row = frames[p][frames[p]["ts"] == first_ts].iloc[0]
        await redis.set(f"price:{p}", str(float(row["close"])))

    clock = SimClock(first_ts.to_pydatetime() if hasattr(first_ts, "to_pydatetime") else first_ts)
    grid = GridStrategy(redis, portfolio, clock=clock)
    grid._running = True
    grid.set_margin_support({p: {"in_markets": True, "short_ok": True} for p in pairs})
    await grid.start()

    equity = []
    for ts in common:
        for p in pairs:
            row = frames[p][frames[p]["ts"] == ts]
            if row.empty:
                continue
            close = float(row.iloc[0]["close"])
            await redis.set(f"price:{p}", str(close))
        if hasattr(ts, "to_pydatetime"):
            clock.set(ts.to_pydatetime())
        else:
            clock.set(ts)
        await grid.check_orders()
        st = portfolio.get()
        equity.append(st.get("total_value_eur", st.get("balance_eur", initial_balance)))

    # collect trades
    from database.models import Trade
    from database.crud import _build_round_trips

    factory = get_session_factory()
    db = factory()
    try:
        trades = db.query(Trade).order_by(Trade.id).all()
        closed, opens = _build_round_trips(trades)
    finally:
        db.close()

    return {
        "equity": equity,
        "trades": closed + opens,
        "n_trades": len(trades),
        "closed": len(closed),
        "opens": len(opens),
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--resolution", default="5m")
    args = ap.parse_args()
    res = asyncio.run(run_backtest(start=args.start, end=args.end, resolution=args.resolution))
    print("steps", len(res["equity"]), "trades", res["n_trades"], "final", res["equity"][-1])
