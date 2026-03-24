"""WebSocket que hace streaming en tiempo real de eventos del bot."""
import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

router = APIRouter()
active_connections: list[WebSocket] = []


@router.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    logger.info(f"WebSocket cliente conectado. Total: {len(active_connections)}")

    from api.main import get_redis
    redis = get_redis()
    pubsub = None

    try:
        bot_status = await redis.get("bot:status")
        if bot_status:
            await websocket.send_text(json.dumps({"type": "bot_status", "data": json.loads(bot_status)}))

        portfolio = await redis.get("portfolio:state")
        if portfolio:
            await websocket.send_text(json.dumps({"type": "portfolio_update", "data": json.loads(portfolio)}))

        pubsub = redis.pubsub()
        await pubsub.subscribe("bot:live_updates", "price_update")

        async def reader():
            async for message in pubsub.listen():
                if message["type"] == "message":
                    await websocket.send_text(message["data"])

        async def ping_loop():
            while True:
                await asyncio.sleep(30)
                await websocket.send_text(json.dumps({"type": "ping"}))

        await asyncio.gather(reader(), ping_loop())

    except WebSocketDisconnect:
        active_connections.remove(websocket)
        logger.info(f"WebSocket cliente desconectado. Total: {len(active_connections)}")
    except Exception as e:
        logger.error(f"Error en WebSocket: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)
    finally:
        if pubsub:
            await pubsub.unsubscribe()
            await pubsub.close()
