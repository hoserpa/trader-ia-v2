from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import redis.asyncio as aioredis
import asyncio
import sys
import os
from loguru import logger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from config import config
from database.init_db import init_db
from data.historical import initialize_historical_data
from trading.engine import TradingEngine
from scheduler.jobs import setup_scheduler

redis_client: aioredis.Redis = None
_trading_engine: TradingEngine = None
_scheduler = None


def _setup_logging():
    logger.remove()
    logger.add(sys.stderr, level=config.log.level, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan> | {message}")
    os.makedirs(os.path.dirname(config.log.file), exist_ok=True)
    logger.add(config.log.file, level=config.log.level, rotation=f"{config.log.max_size} MB",
               retention=config.log.backup_count, serialize=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, _trading_engine, _scheduler

    _setup_logging()
    config.validate()

    init_db()

    redis_client = aioredis.from_url(
        f"redis://{config.database.redis_host}:{config.database.redis_port}/{config.database.redis_db}",
        decode_responses=True,
    )
    await redis_client.ping()

    await initialize_historical_data(days=90)

    _scheduler = setup_scheduler(redis_client)
    _scheduler.start()

    _trading_engine = TradingEngine(redis_client)
    engine_task = asyncio.create_task(_trading_engine.start())

    yield

    await _trading_engine.stop()
    engine_task.cancel()
    _scheduler.shutdown()
    await redis_client.close()


app = FastAPI(title="Crypto Trader Bot API", version="1.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=frontend_path), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join(frontend_path, "index.html"))


@app.get("/manifest.json")
async def manifest():
    return FileResponse(os.path.join(frontend_path, "manifest.json"), media_type="application/json")


@app.get("/icon.png")
async def icon():
    return FileResponse(os.path.join(frontend_path, "icon.png"), media_type="image/png")


def get_redis():
    return redis_client


from api.routers import portfolio, trades, market, bot, logs, simulate

app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])
app.include_router(trades.router, prefix="/api/trades", tags=["Trades"])
app.include_router(market.router, prefix="/api/market", tags=["Market"])
app.include_router(bot.router, prefix="/api/bot", tags=["Bot"])
app.include_router(logs.router, prefix="/api/logs", tags=["Logs"])
app.include_router(simulate.router, prefix="/api/simulate", tags=["Simulation"])

from api.websocket.live import router as ws_router
app.include_router(ws_router)


@app.get("/health")
async def health(redis: aioredis.Redis = Depends(get_redis)):
    try:
        await redis.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {"status": "ok", "redis": redis_ok, "version": "1.0.0"}
