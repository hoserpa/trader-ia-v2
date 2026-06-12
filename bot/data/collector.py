"""Recolección de datos de mercado en tiempo real via WebSocket y REST."""
import asyncio
import json
from datetime import datetime
from typing import Optional
import ccxt.async_support as ccxt
import pandas as pd
import redis.asyncio as aioredis
from loguru import logger
from config import config
from database.crud import upsert_candles
from database.init_db import SessionLocal


class DataCollector:
    """Gestiona la conexión con el exchange configurado.
    Almacena velas OHLCV en Redis (caché) y SQLite (histórico).
    Publica eventos en canal Redis 'new_candle' para el motor de trading.
    """

    REDIS_CANDLE_KEY = "candles:{pair}:{timeframe}"
    REDIS_PRICE_KEY = "price:{pair}"
    REDIS_CHANNEL = "new_candle"
    MAX_REDIS_CANDLES = 500

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
        self.exchange = self._build_exchange()
        self._running = False
        self._reconnect_delay = 5
        self._max_reconnect_delay = 300

    def _build_exchange(self):
        exchange_id = config.exchange.name.lower()
        params = {
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
                "loadMarkets": False,
            },
        }
        if config.trading.mode == "real":
            params["apiKey"] = config.exchange.api_key
            params["secret"] = config.exchange.api_secret

        exchange = getattr(ccxt, exchange_id)(params)

        # Inyectar markets manualmente para evitar que ccxt llame a
        # endpoints de margin/futures durante load_markets()
        self._inject_markets(exchange)
        return exchange

    def _inject_markets(self, exchange) -> None:
        """Crea entries de mercado para los pares configurados.
        Esto evita que fetch_ticker/fetch_ohlcv intenten load_markets()
        que dispararia requests a endpoints margin/futures no autorizados.
        """
        exchange.markets = {}
        for pair in config.trading.pairs:
            symbol = config.trading.get_symbol(pair)
            base = pair.split("/")[0]
            quote = pair.split("/")[1]
            exchange.markets[pair] = {
                "id": symbol,
                "symbol": pair,
                "base": base,
                "quote": quote,
                "active": True,
                "type": "spot",
                "spot": True,
                "margin": False,
                "contract": False,
                "precision": {"price": 8, "amount": 8},
                "limits": {
                    "amount": {"min": 0.001, "max": None},
                    "price": {"min": None, "max": None},
                    "cost": {"min": None, "max": None},
                },
                "info": {},
            }
        exchange.markets_by_id = {m["id"]: m for m in exchange.markets.values() if m.get("id")}
        exchange.symbols = list(exchange.markets.keys())
        exchange.ids = list(exchange.markets_by_id.keys())
        logger.info(f"Mercados inyectados para {len(exchange.markets)} pares: {config.trading.pairs}")

    async def start(self) -> None:
        self._running = True
        logger.info(f"Iniciando recolección de datos para pares: {config.trading.pairs}")
        
        if await self._check_websocket_support():
            logger.info("WebSocket soportado, usando streaming en tiempo real.")
            await asyncio.gather(
                self._run_websocket_loop(),
                self._run_ohlcv_loop(),
            )
        else:
            logger.info("WebSocket no disponible, usando polling REST.")
            await asyncio.gather(
                self._run_polling_loop(),
                self._run_ohlcv_loop(),
            )

    async def _check_websocket_support(self) -> bool:
        """Verifica si el exchange soporta watchTickers via has dict.
        No llama load_markets() — exchange.has es estático y no requiere
        requests a la API que puedan fallar por falta de permisos (margin/futures).
        """
        return bool(self.exchange.has.get('watchTickers', False))

    async def stop(self) -> None:
        self._running = False
        await self.exchange.close()
        logger.info("DataCollector detenido.")

    async def _run_websocket_loop(self) -> None:
        """Loop de reconexión del WebSocket de tickers.
        Si falla MAX_WS_RETRIES veces seguidas, cambia a polling REST.
        """
        ws_failures = 0
        MAX_WS_RETRIES = 3
        delay = self._reconnect_delay

        while self._running and ws_failures < MAX_WS_RETRIES:
            try:
                await self._watch_tickers()
                ws_failures = 0
                delay = self._reconnect_delay
            except Exception as e:
                ws_failures += 1
                logger.warning(f"WebSocket error ({ws_failures}/{MAX_WS_RETRIES}): {e}. Reconectando en {delay}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._max_reconnect_delay)

        if ws_failures >= MAX_WS_RETRIES and self._running:
            logger.warning(f"WebSocket: {MAX_WS_RETRIES} intentos fallidos, cambiando a polling REST")
            await self._run_polling_loop()

    async def _run_polling_loop(self) -> None:
        """Fallback: consulta precios por REST cada 60s."""
        poll_interval = 60
        while self._running:
            try:
                for pair in config.trading.pairs:
                    try:
                        symbol = config.trading.get_symbol(pair)
                        ticker = await self.exchange.fetch_ticker(symbol)
                        price = ticker.get("last")
                        if price:
                            await self.redis.set(
                                self.REDIS_PRICE_KEY.format(pair=pair),
                                str(price),
                                ex=60,
                            )
                            await self.redis.publish(
                                "price_update",
                                json.dumps({"pair": pair, "price": price, "timestamp": datetime.utcnow().isoformat()}),
                            )
                    except Exception as e:
                        logger.warning(f"Error consultando precio {pair}: {e}")
            except Exception as e:
                logger.error(f"Error en polling loop: {e}")
            await asyncio.sleep(poll_interval)

    async def _watch_tickers(self) -> None:
        """Suscripción a precios en tiempo real via ccxt WebSocket."""
        while self._running:
            tickers = await self.exchange.watch_tickers(config.trading.pairs)
            for pair, ticker in tickers.items():
                price = ticker.get("last")
                if price:
                    await self.redis.set(
                        self.REDIS_PRICE_KEY.format(pair=pair),
                        str(price),
                        ex=60,
                    )
                    await self.redis.publish(
                        "price_update",
                        json.dumps({"pair": pair, "price": price, "timestamp": datetime.utcnow().isoformat()}),
                    )

    async def _run_ohlcv_loop(self) -> None:
        """Descarga periódica de velas OHLCV (cada cierre de vela)."""
        interval_seconds = self._timeframe_to_seconds(config.trading.timeframe)
        while self._running:
            for pair in config.trading.pairs:
                try:
                    await self._fetch_and_store_ohlcv(pair)
                except Exception as e:
                    logger.error(f"Error obteniendo OHLCV {pair}: {e}")
            await asyncio.sleep(interval_seconds)

    async def _fetch_and_store_ohlcv(self, pair: str) -> None:
        symbol = config.trading.get_symbol(pair)
        ohlcv = await self.exchange.fetch_ohlcv(
            symbol, timeframe=config.trading.timeframe, limit=3
        )
        if not ohlcv:
            return

        candles_data = []
        for row in ohlcv:
            candles_data.append({
                "pair": pair,
                "timeframe": config.trading.timeframe,
                "timestamp": datetime.utcfromtimestamp(row[0] / 1000),
                "open": row[1], "high": row[2], "low": row[3],
                "close": row[4], "volume": row[5],
            })

        db = SessionLocal()
        try:
            upsert_candles(db, candles_data)
        finally:
            db.close()

        redis_key = self.REDIS_CANDLE_KEY.format(pair=pair, timeframe=config.trading.timeframe)
        pipe = self.redis.pipeline()
        for c in candles_data:
            pipe.rpush(redis_key, json.dumps({
                "timestamp": c["timestamp"].isoformat(),
                "open": c["open"], "high": c["high"],
                "low": c["low"], "close": c["close"], "volume": c["volume"],
            }))
        pipe.ltrim(redis_key, -self.MAX_REDIS_CANDLES, -1)
        await pipe.execute()

        latest = candles_data[-1]
        await self.redis.publish(
            self.REDIS_CHANNEL,
            json.dumps({"pair": pair, "timestamp": latest["timestamp"].isoformat(), "close": latest["close"]}),
        )
        logger.debug(f"OHLCV actualizado: {pair} | close={latest['close']}")

    async def get_latest_candles(self, pair: str, limit: int = 200) -> pd.DataFrame:
        """Obtiene las últimas N velas desde Redis."""
        redis_key = self.REDIS_CANDLE_KEY.format(pair=pair, timeframe=config.trading.timeframe)
        raw = await self.redis.lrange(redis_key, -limit, -1)
        if not raw:
            return pd.DataFrame()

        rows = [json.loads(r) for r in raw]
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        return df

    async def get_current_price(self, pair: str) -> Optional[float]:
        """Obtiene el precio actual desde Redis o fallback."""
        val = await self.redis.get(self.REDIS_PRICE_KEY.format(pair=pair))
        if val:
            return float(val)

        return None

    @staticmethod
    def _timeframe_to_seconds(tf: str) -> int:
        mapping = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
        return mapping.get(tf, 300)
