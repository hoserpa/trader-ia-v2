"""FastAPI backend — REST API y WebSocket para el dashboard."""
import json
import secrets
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import redis.asyncio as aioredis
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from config import config
from api.routers import portfolio, trades, market, bot, logs

app = FastAPI(title="Crypto Trader Bot API", version="1.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")

app.mount("/static", StaticFiles(directory=os.path.join(frontend_path, "css")), name="static")

@app.get("/")
async def root():
    return FileResponse(os.path.join(frontend_path, "index.html"))

security = HTTPBasic()


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username.encode(), config.api.username.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), config.api.password.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            headers={"WWW-Authenticate": "Basic"})
    return credentials.username


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


app.include_router(portfolio.router, prefix="/portfolio", tags=["Portfolio"], dependencies=[Depends(verify_credentials)])
app.include_router(trades.router, prefix="/trades", tags=["Trades"], dependencies=[Depends(verify_credentials)])
app.include_router(market.router, prefix="/market", tags=["Market"], dependencies=[Depends(verify_credentials)])
app.include_router(bot.router, prefix="/bot", tags=["Bot"], dependencies=[Depends(verify_credentials)])
app.include_router(logs.router, prefix="/logs", tags=["Logs"], dependencies=[Depends(verify_credentials)])

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


@app.get("/api/config/public")
async def public_config():
    return {"username": config.api.username}
