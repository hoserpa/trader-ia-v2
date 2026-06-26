"""Orquestador principal del ciclo de análisis y trading."""
import asyncio
import json
import os
from datetime import datetime, date


class DateTimeEncoder(json.JSONEncoder):
    """JSON encoder que serializa datetime/date a string ISO."""
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat() + "Z"
        return super().default(obj)


def _json_dumps(obj):
    return json.dumps(obj, cls=DateTimeEncoder)

import pandas as pd
from loguru import logger
import redis.asyncio as aioredis
import ccxt
from config import config
from data.collector import DataCollector
from indicators.technical import calculate_indicators, get_atr, get_current_price
from indicators.features import FeatureBuilder
from model.predictor import ModelPredictor
from trading.risk_manager import RiskManager
from trading.portfolio import Portfolio
from trading.demo_trader import DemoTrader
from trading.real_trader import RealTrader
from strategies.grid_strategy import GridStrategy
from notifications.telegram import TelegramNotifier
from database.crud import (
    save_decision, save_portfolio_snapshot, get_open_position_by_pair, get_open_position_by_pair_dict, get_open_positions
)
from database.init_db import SessionLocal


class RetryableError(Exception):
    """Error transitorio que no debería marcar el bot como fallido (timeout, rate limit, etc)."""
    pass


def _is_retryable_error(e: Exception) -> bool:
    """Determina si un error es transitorio y no debe marcar el bot como error."""
    error_msg = str(e).lower()
    retryable_patterns = [
        "timeout",
        "timed out",
        "rate limit",
        "too many requests",
        "429",
        "503",
        "502",
        "504",
        "connection",
        "network",
        "econnreset",
        "econnrefused",
        "etimedout",
        "temporary failure",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "fetch failed",
        "none from fetch",
    ]
    if isinstance(e, ccxt.NetworkError):
        return True
    if isinstance(e, ccxt.ExchangeError):
        if any(p in error_msg for p in ["rate limit", "too many requests", "429"]):
            return True
    return any(p in error_msg for p in retryable_patterns)


class TradingEngine:
    """Ciclo principal de análisis y ejecución de señales."""

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
        self.collector = DataCollector(redis_client)
        self.feature_builder = FeatureBuilder()
        self.predictor = ModelPredictor()
        self.risk_manager = RiskManager()
        self.portfolio = Portfolio(redis_client)
        self.telegram = TelegramNotifier()
        self.grid_strategy = GridStrategy(redis_client, self.portfolio)
        self._running = False
        self._status = "stopped"
        self._consecutive_errors = 0
        self._peak_portfolio = 0
        self._indicator_cache: dict[str, pd.DataFrame] = {}
        self._cached_candle_ts: dict[str, str] = {}
        self._lock_key = "bot:instance_lock"
        self._lock_value = ""
        self._lock_heartbeat_task: asyncio.Task | None = None

    async def _acquire_instance_lock(self) -> bool:
        """Intenta adquirir lock de instancia única via Redis SETNX.
        TTL corto (30s) + heartbeat para que el lock expire automáticamente
        si el contenedor muere.
        """
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
        logger.warning(f"Instance lock ocupado por {existing}. Saliendo para evitar duplicados.")
        return False

    async def _refresh_instance_lock(self) -> None:
        """Refresca el TTL del lock mientras el bot corre."""
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
        logger.info("Iniciando motor de trading...")

        await self.portfolio.initialize()
        self._running = True

        if config.trading.is_demo():
            self.trader = DemoTrader(self.portfolio, self.risk_manager)
            logger.info("Modo DEMO activado — no se realizarán operaciones reales.")
        else:
            self.trader = RealTrader(self.portfolio, self.risk_manager)
            logger.warning("⚠️  Modo REAL activado — se operará con dinero real.")

        await self.telegram.notify_bot_started()

        self._status = "running"
        await self._publish_status()
        logger.info(f"Motor de trading iniciado. Intervalo: {config.trading.analysis_interval}s")

        async def _lock_heartbeat():
            while self._running:
                await asyncio.sleep(15)
                await self._refresh_instance_lock()

        self._lock_heartbeat_task = asyncio.create_task(_lock_heartbeat())

        if config.grid.enabled:
            await self.grid_strategy.start()
            logger.info("Grid trading iniciado")

        tasks = [self.collector.start(), self._analysis_loop()]
        if config.grid.enabled:
            tasks.append(self._grid_loop())
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

    async def _analysis_loop(self) -> None:
        """Ciclo periódico de análisis y decisión."""
        await asyncio.sleep(30)

        while self._running:
            start_time = asyncio.get_event_loop().time()
            cycle_had_error = False
            db = SessionLocal()

            try:
                for pair in config.trading.pairs:
                    try:
                        await self._analyze_pair(pair, db)
                    except RetryableError as e:
                        logger.warning(f"⚠️ Error transitorio analizando {pair}: {e}")
                    except Exception as e:
                        cycle_had_error = True
                        if _is_retryable_error(e):
                            logger.warning(f"⚠️ Error de red/transitorio analizando {pair}: {e}")
                        else:
                            logger.error(f"❌ Error fatal analizando {pair}: {e}")
                            self._status = "error"

                if cycle_had_error:
                    self._consecutive_errors += 1
                    if self._consecutive_errors >= 3:
                        await self.telegram.notify_warning(
                            f"⚠️ *{self._consecutive_errors} errores consecutivos*\n"
                            f"El bot ha tenido errores en los últimos ciclos."
                        )
                else:
                    self._consecutive_errors = 0
                    if self._status == "error":
                        self._status = "running"
                        logger.info("✅ Bot recuperado de error")

                await self._save_portfolio_snapshot(db)
                await self._publish_status()

                await self._check_drawdown()

                self.predictor.reload_if_updated()
            finally:
                db.close()

            elapsed = asyncio.get_event_loop().time() - start_time
            sleep_time = max(0, config.trading.analysis_interval - elapsed)
            logger.debug(f"Ciclo completado en {elapsed:.1f}s. Siguiente en {sleep_time:.0f}s.")
            await asyncio.sleep(sleep_time)

    async def _grid_loop(self) -> None:
        """Ciclo periódico de verificación de órdenes grid."""
        await asyncio.sleep(10)
        while self._running and config.grid.enabled:
            try:
                await self.grid_strategy.check_orders()
            except Exception as e:
                logger.error(f"Error en grid loop: {e}")
            await asyncio.sleep(config.grid.poll_interval)

    async def _check_drawdown(self) -> None:
        """Notifica si el drawdown supera el umbral."""
        portfolio_state = self.portfolio.get()
        current_value = portfolio_state.get("total_value_eur", 0)
        
        if current_value > self._peak_portfolio:
            self._peak_portfolio = current_value
        
        if self._peak_portfolio > 0:
            drawdown = (self._peak_portfolio - current_value) / self._peak_portfolio
            if drawdown > 0.10:
                await self.telegram.notify_warning(
                    f"📉 *Alerta Drawdown*\n"
                    f"Current: `{current_value:.2f}€`\n"
                    f"Peak: `{self._peak_portfolio:.2f}€`\n"
                    f"Drawdown: `{drawdown*100:.1f}%`"
                )

    async def _analyze_pair(self, pair: str, db) -> None:
        """Análisis completo de un par: datos → indicadores → features → señal → ejecución."""
        try:
            candles = await self.collector.get_latest_candles(pair, limit=config.trading.candles_required)
        except Exception as e:
            if _is_retryable_error(e):
                raise RetryableError(f"Error obteniendo velas para {pair}: {e}")
            raise

        if candles.empty or len(candles) < 55:
            logger.debug(f"{pair}: datos insuficientes ({len(candles)} velas)")
            return

        last_ts = str(candles["timestamp"].iloc[-1])
        cached_ts = self._cached_candle_ts.get(pair)

        if cached_ts == last_ts and pair in self._indicator_cache:
            candles_with_indicators = self._indicator_cache[pair]
        else:
            candles_with_indicators = calculate_indicators(candles)
            self._indicator_cache[pair] = candles_with_indicators
            self._cached_candle_ts[pair] = last_ts

        atr = get_atr(candles_with_indicators)

        try:
            current_price = await self.collector.get_current_price(pair)
        except Exception as e:
            if _is_retryable_error(e):
                raise RetryableError(f"Error obteniendo precio para {pair}: {e}")
            raise

        current_price = current_price or get_current_price(candles_with_indicators)

        features = self.feature_builder.build_features(candles_with_indicators)
        if features is None:
            logger.warning(f"{pair}: features = None, no se puede predecir")
            return

        logger.debug(f"{pair}: features keys = {list(features.keys())[:10]}...")
        
        if not self.predictor.is_model_loaded():
            logger.warning(f"{pair}: modelo no cargado, usando señal HOLD")
            signal = {"signal": "HOLD", "confidence": 0.0, "probabilities": {"BUY": 0.0, "SELL": 0.0, "HOLD": 1.0}}
        else:
            signal = self.predictor.predict(features)
            if signal is None:
                logger.warning(f"{pair}: predict() devolvió None")
                return
        
        logger.info(f"📊 {pair} | precio={current_price:.2f}€ | señal={signal['signal']} ({signal['confidence']:.0%}) | probs={signal.get('probabilities', {})} | ATR={atr:.2f}")

        await self.redis.publish("bot:live_updates", _json_dumps({
            "type": "signal",
            "data": {**signal, "pair": pair, "price": current_price, "atr": atr,
                     "atr_pct": atr / current_price if current_price > 0 else 0}
        }))

        open_position = get_open_position_by_pair(db, pair)
        open_position_dict = get_open_position_by_pair_dict(db, pair)

        executed = False
        rejection_reason = None

        if open_position:
            port_pos = self.portfolio.get_position(pair)
            stored_trail = port_pos.get("trailing_stop_price") if port_pos else None
            if stored_trail is not None:
                open_position_dict["trailing_stop_price"] = stored_trail

            position_type = open_position_dict.get("position_type", "long")

            trailing_stop = self.risk_manager.calculate_trailing_stop(
                open_position_dict["entry_price"], current_price, atr, position_type=position_type
            )
            if trailing_stop is not None:
                if position_type == "short":
                    update_trail = stored_trail is None or trailing_stop < stored_trail
                else:
                    update_trail = stored_trail is None or trailing_stop > stored_trail
                if update_trail:
                    open_position_dict["trailing_stop_price"] = trailing_stop
                    await self.portfolio.update_position_meta(pair, "trailing_stop_price", trailing_stop)
                    logger.info(f"  ↳ Trailing stop actualizado: {trailing_stop:.2f}€ (anterior: {stored_trail})")

            take_partial, target_price = self.risk_manager.should_take_partial_profit(
                open_position_dict["entry_price"], current_price, atr, position_type=position_type
            )

            should_sell, sell_reason = self.risk_manager.can_sell(
                pair, open_position_dict, signal, current_price,
                atr=atr, candles_with_indicators=candles_with_indicators
            )
            if should_sell:
                if position_type == "short":
                    trade = await self.trader.execute_buy_to_close(pair, open_position_dict, current_price, sell_reason, db=db)
                else:
                    trade = await self.trader.execute_sell(pair, open_position_dict, current_price, sell_reason, db=db)
                if trade:
                    executed = True
                    self.risk_manager.record_close(pair)
                    await self.redis.publish("bot:live_updates", _json_dumps({"type": "trade_executed", "data": trade}))
                    await self.telegram.notify_position_closed(trade, trade.get("pnl_eur", 0), open_position_dict)
            elif take_partial:
                if position_type == "short":
                    logger.info(f"  ↳ Ganancia parcial short: tomando {config.risk.partial_exit_pct:.0%} en {pair}")
                    trade = await self.trader.execute_partial_buy_to_close(
                        pair, open_position_dict, current_price,
                        config.risk.partial_exit_pct,
                        f"partial_profit_{config.risk.partial_exit_r_multiple:.1f}R",
                        db=db
                    )
                else:
                    pnl_pct = (current_price - open_position_dict["entry_price"]) / open_position_dict["entry_price"] * 100
                    logger.info(f"  ↳ Ganancia parcial: {pnl_pct:.2f}% — tomando {config.risk.partial_exit_pct:.0%} en {pair}")
                    trade = await self.trader.execute_partial_sell(
                        pair, open_position_dict, current_price,
                        config.risk.partial_exit_pct,
                        f"partial_profit_{config.risk.partial_exit_r_multiple:.1f}R",
                        db=db
                    )
                if trade:
                    await self.redis.publish("bot:live_updates", _json_dumps({"type": "trade_executed", "data": trade}))
        else:
            await self.portfolio.refresh_if_changed()
            portfolio_state = self.portfolio.get()
            prices = {p: await self.collector.get_current_price(p) or 0 for p in config.trading.pairs}
            portfolio_state = await self.portfolio.update_valuations(prices)

            if signal.get("signal") == "SELL":
                can_short, reason, amount_eur = self.risk_manager.can_short(
                    pair, signal, portfolio_state, current_price, atr,
                    candles_with_indicators=candles_with_indicators,
                )
                if can_short:
                    trade = await self.trader.execute_short(pair, amount_eur, current_price, atr, db=db)
                    if trade:
                        executed = True
                        await self.redis.publish("bot:live_updates", _json_dumps({"type": "trade_executed", "data": trade}))
                        await self.telegram.notify_trade(trade, signal)
                else:
                    rejection_reason = reason
                    if signal["signal"] != "HOLD":
                        logger.info(f"  ↳ Señal SELL rechazada para short: {reason}")
            else:
                can_buy, reason, amount_eur = self.risk_manager.can_buy(
                    pair, signal, portfolio_state, current_price, atr,
                    candles_with_indicators=candles_with_indicators,
                )
                if can_buy:
                    trade = await self.trader.execute_buy(pair, amount_eur, current_price, atr, db=db)
                    if trade:
                        executed = True
                        await self.redis.publish("bot:live_updates", _json_dumps({"type": "trade_executed", "data": trade}))
                        await self.telegram.notify_trade(trade, signal)
                else:
                    rejection_reason = reason
                    if signal["signal"] != "HOLD":
                        logger.info(f"  ↳ Señal {signal['signal']} rechazada: {reason}")

        save_decision(db, {
            "pair": pair,
            "signal": signal["signal"],
            "confidence": signal["confidence"],
            "prob_buy": signal["probabilities"].get("BUY", 0),
            "prob_sell": signal["probabilities"].get("SELL", 0),
            "prob_hold": signal["probabilities"].get("HOLD", 1),
            "executed": executed,
            "rejection_reason": rejection_reason,
        })

    async def _save_portfolio_snapshot(self, db) -> None:
        """Guarda snapshot del portafolio actual en DB."""
        await self.portfolio.refresh_if_changed()
        prices = {}
        for pair in config.trading.pairs:
            price = await self.collector.get_current_price(pair)
            if price:
                prices[pair] = price
        state = await self.portfolio.update_valuations(prices)

        save_portfolio_snapshot(db, state)

        await self.redis.publish("bot:live_updates", _json_dumps({"type": "portfolio_update", "data": state}))

    async def _publish_status(self) -> None:
        await self.redis.set("bot:status", _json_dumps({
            "status": self._status,
            "mode": config.trading.mode,
            "pairs": config.trading.pairs,
            "model_loaded": self.predictor.is_model_loaded(),
            "last_update": datetime.utcnow().isoformat() + "Z",
        }))
        await self.redis.publish("bot:live_updates", _json_dumps({
            "type": "bot_status",
            "data": {"status": self._status, "mode": config.trading.mode}
        }))
