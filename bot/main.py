"""Punto de entrada principal del bot."""
import asyncio
import sys
import os
import redis.asyncio as aioredis
from loguru import logger
from config import config
from database.init_db import init_db
from data.historical import initialize_historical_data
from trading.engine import TradingEngine
from scheduler.jobs import setup_scheduler


def setup_logging():
    logger.remove()
    logger.add(sys.stderr, level=config.log.level, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan> | {message}")
    os.makedirs(os.path.dirname(config.log.file), exist_ok=True)
    logger.add(config.log.file, level=config.log.level, rotation=f"{config.log.max_size} MB",
               retention=config.log.backup_count, serialize=True)


async def main():
    setup_logging()
    logger.info("=" * 60)
    logger.info(f"🤖 Crypto Trader Bot arrancando...")
    logger.info(f"   Modo: {config.trading.mode.upper()}")
    logger.info(f"   Pares: {', '.join(config.trading.pairs)}")
    logger.info(f"   Timeframe: {config.trading.timeframe}")
    logger.info("=" * 60)

    config.validate()

    init_db()

    redis_client = aioredis.from_url(
        f"redis://{config.database.redis_host}:{config.database.redis_port}/{config.database.redis_db}",
        decode_responses=True,
    )
    await redis_client.ping()
    logger.info("Redis conectado.")

    logger.info("Descargando datos históricos (puede tardar varios minutos)...")
    await initialize_historical_data(days=90)

    scheduler = setup_scheduler(redis_client)
    scheduler.start()
    logger.info("Scheduler iniciado.")

    engine = TradingEngine(redis_client)
    try:
        await engine.start()
    except KeyboardInterrupt:
        logger.info("Interrupción por teclado. Deteniendo bot...")
    finally:
        await engine.stop()
        scheduler.shutdown()
        await redis_client.close()
        logger.info("Bot detenido correctamente.")


if __name__ == "__main__":
    asyncio.run(main())
