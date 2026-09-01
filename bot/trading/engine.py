"""Orquestador principal del trading bot (grid-only)."""
import asyncio
import json
import os
from datetime import datetime, date, timezone


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat() + "Z"
        return super().default(obj)


def _json_dumps(obj):
    return json.dumps(obj, cls=DateTimeEncoder)


from loguru import logger
import redis.asyncio as aioredis
import ccxt
from config import config
from data.collector import DataCollector
from indicators.technical import calculate_indicators, get_atr
from trading.portfolio import Portfolio
from trading.risk_manager import RiskManager
from trading.broker import build_broker
from trading import market_data
from trading.demo_trader import DemoTrader
from trading.real_trader import RealTrader
from strategies.grid_strategy import GridStrategy
from config_service import apply_overrides
from notifications.telegram import TelegramNotifier
from database.crud import save_portfolio_snapshot, get_stats_summary, get_open_positions
from database.init_db import SessionLocal


class RetryableError(Exception):
    pass


def _is_retryable_error(e: Exception) -> bool:
    error_msg = str(e).lower()
    retryable_patterns = [
        "timeout", "timed out", "rate limit", "too many requests",
        "429", "503", "502", "504", "connection", "network",
        "econnreset", "econnrefused", "etimedout", "temporary failure",
        "service unavailable", "bad gateway", "gateway timeout",
        "fetch failed", "none from fetch",
    ]
    if isinstance(e, ccxt.NetworkError):
        return True
    if isinstance(e, ccxt.ExchangeError):
        if any(p in error_msg for p in ["rate limit", "too many requests", "429"]):
            return True
    return any(p in error_msg for p in retryable_patterns)


class TradingEngine:
    """Grid-only trading engine."""

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
        self.collector = DataCollector(redis_client)
        self.portfolio = Portfolio(redis_client)
        self.telegram = TelegramNotifier()
        self.risk = RiskManager()
        self.broker = build_broker(self.portfolio, self.risk)
        self.grid_strategy = GridStrategy(
            redis_client, self.portfolio, broker=self.broker, telegram=self.telegram
        )
        self._running = False
        self._status = "stopped"
        self._consecutive_errors = 0
        self._peak_portfolio = 0
        self._drawdown_notified = False
        self._lock_key = "bot:instance_lock"
        self._lock_value = ""
        self._lock_heartbeat_task: asyncio.Task | None = None
        self._atr_cache: dict[str, float] = {}
        self._last_snapshot = 0

    async def _acquire_instance_lock(self) -> bool:
        import socket
        pid = os.getpid()
        hostname = socket.gethostname()
        self._lock_value = f"{hostname}:{pid}"
        self._lock_key = "bot:instance_lock"
        acquired = await self.redis.setnx(self._lock_key, self._lock_value)
        if acquired:
            await self.redis.expire(self._lock_key, 30)
            logger.info(f"Instance lock adquirido ({self._lock_value})")
            return True
        existing = await self.redis.get(self._lock_key)
        logger.warning(f"Instance lock ocupado por {existing}. Saliendo.")
        return False

    async def _refresh_instance_lock(self) -> None:
        try:
            await self.redis.expire(self._lock_key, 30)
        except Exception:
            pass

    async def _release_instance_lock(self) -> None:
        await self.redis.delete(self._lock_key)

    async def start(self) -> None:
        if not await self._acquire_instance_lock():
            return

        self._status = "starting"
        await self._publish_status()
        logger.info("Iniciando motor de trading (grid-only)...")

        await apply_overrides(self.redis)
        logger.info("Config overrides aplicados (fees y parámetros en caliente).")

        await self.portfolio.initialize()
        self._running = True

        if config.trading.is_demo():
            logger.info("Modo DEMO activado.")
        else:
            logger.warning("Modo REAL activado.")

        await self._validate_margin_support()

        await self.telegram.notify_bot_started()

        self._status = "running"
        await self._publish_status()
        logger.info(f"Motor de trading iniciado. Grid poll: {config.grid.poll_interval}s")

        async def _lock_heartbeat():
            while self._running:
                await asyncio.sleep(15)
                await self._refresh_instance_lock()

        self._lock_heartbeat_task = asyncio.create_task(_lock_heartbeat())

        if config.grid.enabled:
            await self.grid_strategy.start()
            logger.info("Grid trading iniciado")

        tasks = [self.collector.start(), self._grid_loop(), self._monitoring_loop()]
        await asyncio.gather(*tasks)

    async def stop(self) -> None:
        self._running = False
        if self._lock_heartbeat_task:
            self._lock_heartbeat_task.cancel()
        self._status = "stopped"
        await self.collector.stop()
        if config.grid.enabled:
            await self.grid_strategy.stop()
        await self.telegram.notify_bot_stopped()
        await self._publish_status()
        await self._release_instance_lock()
        logger.info("Motor de trading detenido.")

    async def _validate_margin_support(self) -> None:
        """Consulta los markets reales de Kraken y determina el soporte de margen/short
        por par. Tanto en demo como en real se usa el dato real del mercado, para que el
        modo demo decida igual que el modo real (no abre shorts que la API no podria)."""
        pairs = list(dict.fromkeys(config.grid.pairs + config.trading.pairs))
        margin = {}
        status = "no consultado"
        try:
            margin = await self._fetch_real_margin_support(pairs)
            ok = sum(1 for v in margin.values() if v.get("in_markets"))
            yy = sum(1 for v in margin.values() if v.get("short_ok"))
            status = f"{ok}/{len(pairs)} pares, {yy} permiten short con margen"
        except Exception as e:
            logger.warning(f"No se pudo validar soporte de margen real: {e}")
            margin = {p: {
                "symbol": p, "in_markets": False, "long_leverage": None,
                "short_leverage": None, "margin_ok": False, "short_ok": False,
            } for p in pairs}

        self.broker.set_margin_support(margin)
        self.grid_strategy.set_margin_support(margin)
        self.margin_supported = margin

        for pair in pairs:
            info = margin.get(pair, {})
            logger.info(
                f"Margen {pair}: long_lev={info.get('long_leverage')}, "
                f"short_lev={info.get('short_leverage')}, "
                f"short_ok={info.get('short_ok')}"
            )
        logger.info(f"Validacion de margen real completada ({status})")

    async def _fetch_real_margin_support(self, pairs: list) -> dict:
        """Crear cliente kraken de solo lectura y consultar markets. No autentica."""
        import ccxt.async_support as ccxt
        exchange = getattr(ccxt, config.exchange.name.lower())({
            "enableRateLimit": True,
        })
        try:
            return await market_data.fetch_margin_support(exchange, pairs)
        finally:
            try:
                await exchange.close()
            except Exception:
                pass

    async def _grid_loop(self) -> None:
        """Ciclo principal: verifica fills del grid."""
        await asyncio.sleep(10)
        while self._running and config.grid.enabled:
            try:
                await self._update_atr_cache()
                await self.grid_strategy.check_orders()
            except Exception as e:
                logger.error(f"Error en grid loop: {e}")
            await asyncio.sleep(config.grid.poll_interval)

    async def _monitoring_loop(self) -> None:
        """Ciclo de monitoreo: snapshots, status, alerts."""
        await asyncio.sleep(30)
        while self._running:
            try:
                await self._save_portfolio_snapshot()
                await self._publish_status()
                await self._check_drawdown()
                await self._send_daily_summary_if_needed()
            except Exception as e:
                logger.warning(f"Error en monitoring loop: {e}")
            await asyncio.sleep(300)

    async def _update_atr_cache(self):
        """Actualiza cache de ATR para grid ATR-adaptive."""
        if not config.grid.atr_adaptive:
            return
        for pair in config.grid.pairs:
            try:
                candles = await self.collector.get_latest_candles(pair, limit=100)
                if candles is not None and len(candles) >= 20:
                    df = calculate_indicators(candles)
                    atr = get_atr(df)
                    if atr and atr > 0:
                        self._atr_cache[pair] = atr
                        self.grid_strategy.set_atr_cache(pair, atr)
            except Exception:
                pass

    async def _send_daily_summary_if_needed(self) -> None:
        today_key = "bot:last_summary_date"
        last_date = await self.redis.get(today_key)
        today_str = str(date.today())
        if last_date == today_str:
            return

        port_state = self.portfolio.get()
        prices = {}
        for pair in config.trading.pairs:
            price = await self.collector.get_current_price(pair)
            if price:
                prices[pair] = price

        portfolio_state = await self.portfolio.update_valuations(prices)
        grid_state = self.grid_strategy.get_state()

        with SessionLocal() as db:
            stats = get_stats_summary(db)
            open_positions = get_open_positions(db)
        portfolio_state["open_positions"] = len(open_positions)

        grid_summary = {
            "total_pnl_eur": grid_state.get("total_pnl_eur", 0),
            "total_grid_trades": grid_state.get("total_grid_trades", 0),
            "total_fees_eur": grid_state.get("total_fees_eur", 0),
        }

        await self.telegram.send_daily_summary(portfolio_state, stats, grid=grid_summary)
        await self.redis.set(today_key, today_str)

    async def _check_drawdown(self) -> None:
        port_state = self.portfolio.get()
        current_value = port_state.get("total_value_eur", 0)

        if current_value > self._peak_portfolio:
            self._peak_portfolio = current_value
            self._drawdown_notified = False

        if self._peak_portfolio > 0:
            drawdown = (self._peak_portfolio - current_value) / self._peak_portfolio
            if drawdown > 0.10:
                if not self._drawdown_notified:
                    await self.telegram.notify_warning(
                        f"Drawdown {drawdown*100:.1f}% · Peak {self._peak_portfolio:.2f}€ -> Now {current_value:.2f}€",
                        warn_key="drawdown",
                    )
                    self._drawdown_notified = True
            else:
                self._drawdown_notified = False

    async def _save_portfolio_snapshot(self) -> None:
        await self.portfolio.refresh_if_changed()
        prices = {}
        for pair in config.trading.pairs:
            price = await self.collector.get_current_price(pair)
            if price:
                prices[pair] = price

        state = await self.portfolio.update_valuations(prices)

        with SessionLocal() as db:
            save_portfolio_snapshot(db, state)

        await self.redis.publish(
            "bot:live_updates", _json_dumps({"type": "portfolio_update", "data": state})
        )

    async def _publish_status(self) -> None:
        grid_state = self.grid_strategy.get_state() if config.grid.enabled else {}
        await self.redis.set(
            "bot:status",
            _json_dumps(
                {
                    "status": self._status,
                    "mode": config.trading.mode,
                    "pairs": config.trading.pairs,
                    "grid_enabled": config.grid.enabled,
                    "grid_pnl": grid_state.get("total_pnl_eur", 0),
                    "grid_trades": grid_state.get("total_grid_trades", 0),
                    "last_update": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )
        await self.redis.publish(
            "bot:live_updates",
            _json_dumps(
                {
                    "type": "bot_status",
                    "data": {
                        "status": self._status,
                        "mode": config.trading.mode,
                        "grid_pnl": grid_state.get("total_pnl_eur", 0),
                    },
                }
            ),
        )
