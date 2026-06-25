"""Recolección de datos de mercado en tiempo real via WebSocket y REST."""
import asyncio
import json
from datetime import datetime
from typing import Optional
import ccxt.async_support as ccxt
import pandas as pd
import redis.asyncio as aioredis
import websockets
from loguru import logger
from config import config
from database.crud import upsert_candles
from database.init_db import SessionLocal

KRAKEN_WS_URL = "wss://ws.kraken.com"


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
        self._futures_exchange = None
        self._running = False
        self._reconnect_delay = 5
        self._max_reconnect_delay = 300

    def _build_exchange(self):
        exchange_id = config.exchange.name.lower()
        params = {
            "enableRateLimit": True,
        }
        if config.trading.mode == "real":
            params["apiKey"] = config.exchange.api_key
            params["secret"] = config.exchange.api_secret

        exchange = getattr(ccxt, exchange_id)(params)
        return exchange

    def _build_futures_exchange(self):
        """Construye una instancia ccxt para futuros."""
        exchange_id = "krakenfutures"
        params = {
            "enableRateLimit": True,
        }
        if config.trading.mode == "real":
            params["apiKey"] = config.exchange.api_key
            params["secret"] = config.exchange.api_secret
        else:
            params["apiKey"] = config.exchange.api_key or "demo"
            params["secret"] = config.exchange.api_secret or "demo"

        return getattr(ccxt, exchange_id)(params)

    async def get_futures_exchange(self):
        """Retorna (creando si es necesario) la instancia de futuros."""
        if self._futures_exchange is None:
            self._futures_exchange = self._build_futures_exchange()
            try:
                await self._futures_exchange.load_markets()
                logger.info("Exchange de futuros inicializado")
            except Exception as e:
                logger.error(f"Error cargando mercados de futuros: {e}")
        return self._futures_exchange

    async def start(self) -> None:
        self._running = True
        logger.info(f"Iniciando recolección de datos para pares: {config.trading.pairs}")

        try:
            await self.exchange.load_markets()
        except Exception as e:
            logger.error(f"Error cargando mercados: {e}")

        await asyncio.gather(
            self._run_kraken_ws(),
            self._run_ohlcv_loop(),
        )

    async def _kraken_pair(self, pair: str) -> str:
        """Convierte BTC/EUR a XBT/EUR (formato Kraken WebSocket)."""
        base, quote = pair.split("/")
        if base == "BTC":
            base = "XBT"
        return f"{base}/{quote}"

    async def _run_kraken_ws(self) -> None:
        """Conecta al WebSocket público de Kraken para tickers en tiempo real.
        Usa la API directamente (no ccxt) para evitar dependencia de ccxt.pro.
        Si falla, hace fallback a polling REST.
        """
        ws_failures = 0
        MAX_WS_RETRIES = 5
        delay = self._reconnect_delay

        while self._running:
            try:
                async with websockets.connect(KRAKEN_WS_URL) as ws:
                    ws_failures = 0
                    delay = self._reconnect_delay

                    kraken_pairs = [await self._kraken_pair(p) for p in config.trading.pairs]
                    subscribe = {
                        "event": "subscribe",
                        "pair": kraken_pairs,
                        "subscription": {"name": "ticker"},
                    }
                    await ws.send(json.dumps(subscribe))
                    logger.info(f"Kraken WS conectado, suscrito a {kraken_pairs}")

                    async def _heartbeat():
                        while self._running:
                            await asyncio.sleep(20)
                            try:
                                await ws.send(json.dumps({"event": "ping"}))
                            except Exception:
                                break

                    hb_task = asyncio.create_task(_heartbeat())

                    try:
                        async for message in ws:
                            data = json.loads(message)
                            if isinstance(data, list) and len(data) >= 4:
                                _, ticker_data, channel_name, pair_raw = data
                                if channel_name == "ticker":
                                    pair = "BTC/" + pair_raw[4:] if pair_raw.startswith("XBT/") else pair_raw
                                    last_price = float(ticker_data.get("c", [0])[0])
                                    if last_price and last_price > 0:
                                        await self.redis.set(
                                            self.REDIS_PRICE_KEY.format(pair=pair),
                                            str(last_price),
                                            ex=60,
                                        )
                                        await self.redis.publish(
                                            "price_update",
                                            json.dumps({"pair": pair, "price": last_price, "timestamp": datetime.utcnow().isoformat() + "Z"}),
                                        )
                            elif isinstance(data, dict):
                                if data.get("event") == "heartbeat":
                                    continue
                                if data.get("event") == "systemStatus" and data.get("status") == "online":
                                    logger.info("Kraken WS: sistema online")
                                if data.get("event") == "subscriptionStatus" and data.get("status") == "subscribed":
                                    logger.info(f"Kraken WS: suscrito a {data.get('pair')} ({data.get('subscription', {}).get('name')})")
                    except websockets.ConnectionClosed:
                        logger.warning("Kraken WS: conexión cerrada, reconectando...")
                    except Exception as e:
                        logger.warning(f"Kraken WS: error en mensaje: {e}")
                    finally:
                        hb_task.cancel()
                        try:
                            await hb_task
                        except asyncio.CancelledError:
                            pass

            except Exception as e:
                ws_failures += 1
                logger.warning(f"Kraken WS error ({ws_failures}/{MAX_WS_RETRIES}): {e}. Reconectando en {delay}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._max_reconnect_delay)

            if ws_failures >= MAX_WS_RETRIES and self._running:
                logger.warning("Kraken WS: máximo de reintentos, cambiando a polling REST")
                await self._run_polling_loop()
                break

    async def _run_polling_loop(self) -> None:
        """Fallback: consulta precios por REST cada 30s."""
        poll_interval = 30
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
                                json.dumps({"pair": pair, "price": price, "timestamp": datetime.utcnow().isoformat() + "Z"}),
                            )
                    except Exception as e:
                        logger.warning(f"Error consultando precio {pair}: {e}")
            except Exception as e:
                logger.error(f"Error en polling loop: {e}")
            await asyncio.sleep(poll_interval)

    async def stop(self) -> None:
        self._running = False
        await self.exchange.close()
        logger.info("DataCollector detenido.")

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
            json.dumps({"pair": pair, "timestamp": latest["timestamp"].isoformat() + "Z", "close": latest["close"]}),
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
        df["timestamp"] = pd.to_datetime(df["timestamp"].str.rstrip("Z"))
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
