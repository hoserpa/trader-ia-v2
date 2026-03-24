"""Descarga de datos históricos para inicialización del bot."""
import asyncio
from datetime import datetime, timedelta
import ccxt.async_support as ccxt
import pandas as pd
from loguru import logger
from config import config
from database.crud import upsert_candles
from database.init_db import SessionLocal


async def fetch_and_store_historical(pair: str, days: int = 90) -> int:
    """Descarga el histórico de los últimos N días para un par.
    Retorna el número de velas almacenadas.
    """
    exchange = ccxt.coinbase({
        "apiKey": config.coinbase.api_key,
        "secret": config.coinbase.api_secret,
        "enableRateLimit": True,
    })

    since = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    timeframe = config.trading.timeframe
    all_candles = []
    limit = 300

    logger.info(f"Descargando histórico {pair} ({days} días, {timeframe})...")
    try:
        while True:
            ohlcv = await exchange.fetch_ohlcv(pair, timeframe=timeframe, since=since, limit=limit)
            if not ohlcv:
                break
            all_candles.extend(ohlcv)
            since = ohlcv[-1][0] + 1
            if len(ohlcv) < limit:
                break
            await asyncio.sleep(0.5)
    finally:
        await exchange.close()

    if not all_candles:
        logger.warning(f"No se obtuvieron datos históricos para {pair}")
        return 0

    candles_data = [{
        "pair": pair,
        "timeframe": timeframe,
        "timestamp": datetime.utcfromtimestamp(row[0] / 1000),
        "open": row[1], "high": row[2], "low": row[3],
        "close": row[4], "volume": row[5],
    } for row in all_candles]

    db = SessionLocal()
    try:
        inserted = upsert_candles(db, candles_data)
    finally:
        db.close()

    logger.info(f"Histórico {pair}: {inserted} velas nuevas almacenadas ({len(all_candles)} descargadas)")
    return inserted


async def initialize_historical_data(days: int = 90) -> None:
    """Inicializa el histórico para todos los pares configurados."""
    tasks = [fetch_and_store_historical(pair, days) for pair in config.trading.pairs]
    await asyncio.gather(*tasks)
