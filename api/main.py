"""FastAPI backend — REST API y WebSocket para el dashboard."""
import json
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import redis.asyncio as aioredis
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from config import config
from api.routers import portfolio, trades, market, bot, logs, simulate

app = FastAPI(title="Crypto Trader Bot API", version="1.0.0")

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

redis_client: aioredis.Redis = None


@app.on_event("startup")
async def startup():
    global redis_client
    redis_client = aioredis.from_url(
        f"redis://{config.database.redis_host}:{config.database.redis_port}",
        decode_responses=True,
    )


@app.on_event("shutdown")
async def shutdown():
    await redis_client.close()


def get_redis():
    return redis_client


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
