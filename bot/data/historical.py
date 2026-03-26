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
    exchange_id = config.exchange.name.lower()
    exchange = getattr(ccxt, exchange_id)({
        "apiKey": config.exchange.api_key,
        "secret": config.exchange.api_secret,
        "enableRateLimit": True,
        "timeout": 30000,
    })

    since = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    timeframe = config.trading.timeframe
    all_candles = []
    limit = 300

    logger.info(f"Descargando histórico {pair} ({days} días, {timeframe})...")
    try:
        await exchange.load_markets()
    except Exception as e:
        logger.error(f"No se pudo cargar mercados para {pair}: {e}")
        await exchange.close()
        return 0

    try:
        while True:
            try:
                ohlcv = await exchange.fetch_ohlcv(pair, timeframe=timeframe, since=since, limit=limit)
            except Exception as e:
                logger.warning(f"Error descargando {pair}, continuando: {e}")
                break
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
    """Inicializa el histórico solo si no existen datos suficientes."""
    from database.crud import get_candle_count
    
    required_candles = int((days * 24 * 60) / 5)
    
    for pair in config.trading.pairs:
        db = SessionLocal()
        try:
            existing_count = get_candle_count(db, pair, config.trading.timeframe)
        finally:
            db.close()
        
        if existing_count >= required_candles:
            logger.info(f"Datos históricos para {pair} ya existen ({existing_count} velas), omitiendo descarga.")
        else:
            logger.info(f"Datos insuficientes para {pair} ({existing_count} < {required_candles}), descargando...")
            await fetch_and_store_historical(pair, days)
