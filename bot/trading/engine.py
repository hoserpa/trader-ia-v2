"""Orquestador principal del ciclo de análisis y trading."""
import asyncio
import json
from datetime import datetime
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
from notifications.telegram import TelegramNotifier
from database.crud import (
    save_decision, save_portfolio_snapshot, get_open_position_by_pair, get_open_positions
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
        self._running = False
        self._status = "stopped"
        self._consecutive_errors = 0
        self._peak_portfolio = 0

    async def start(self) -> None:
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
        await asyncio.gather(
            self.collector.start(),
            self._analysis_loop(),
        )

    async def stop(self) -> None:
        self._running = False
        self._status = "stopped"
        await self.collector.stop()
        await self.telegram.notify_bot_stopped()
        await self._publish_status()
        logger.info("Motor de trading detenido.")

    async def _analysis_loop(self) -> None:
        """Ciclo periódico de análisis y decisión."""
        await asyncio.sleep(30)

        while self._running:
            start_time = asyncio.get_event_loop().time()
            cycle_had_error = False

            for pair in config.trading.pairs:
                try:
                    await self._analyze_pair(pair)
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

            await self._save_portfolio_snapshot()
            await self._publish_status()

            await self._check_drawdown()

            self.predictor.reload_if_updated()

            elapsed = asyncio.get_event_loop().time() - start_time
            sleep_time = max(0, config.trading.analysis_interval - elapsed)
            logger.debug(f"Ciclo completado en {elapsed:.1f}s. Siguiente en {sleep_time:.0f}s.")
            await asyncio.sleep(sleep_time)

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

    async def _analyze_pair(self, pair: str) -> None:
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

        candles_with_indicators = calculate_indicators(candles)
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

        await self.redis.publish("bot:live_updates", json.dumps({
            "type": "signal",
            "data": {**signal, "pair": pair, "price": current_price, "atr": atr,
                     "atr_pct": atr / current_price if current_price > 0 else 0}
        }))

        db = SessionLocal()
        try:
            open_position = get_open_position_by_pair(db, pair)

            executed = False
            rejection_reason = None

            if open_position:
                should_sell, sell_reason = self.risk_manager.can_sell(pair, open_position, signal, current_price)
                if should_sell:
                    trade = await self.trader.execute_sell(pair, open_position, current_price, sell_reason)
                    if trade:
                        executed = True
                        await self.redis.publish("bot:live_updates", json.dumps({"type": "trade_executed", "data": trade}))
                        await self.telegram.notify_position_closed(trade, trade.get("pnl_eur", 0), open_position)
            else:
                portfolio_state = self.portfolio.get()
                prices = {p: await self.collector.get_current_price(p) or 0 for p in config.trading.pairs}
                portfolio_state = await self.portfolio.update_valuations(prices)

                can_buy, reason, amount_eur = self.risk_manager.can_buy(
                    pair, signal, portfolio_state, current_price, atr
                )
                if can_buy:
                    trade = await self.trader.execute_buy(pair, amount_eur, current_price, atr)

                    if trade:
                        executed = True
                        await self.redis.publish("bot:live_updates", json.dumps({"type": "trade_executed", "data": trade}))
                        await self.telegram.notify_trade(trade, signal)
                else:
                    rejection_reason = reason
                    if signal["signal"] != "HOLD":
                        logger.debug(f"  ↳ Señal {signal['signal']} rechazada: {reason}")

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
        finally:
            db.close()

    async def _save_portfolio_snapshot(self) -> None:
        """Guarda snapshot del portafolio actual en DB."""
        prices = {}
        for pair in config.trading.pairs:
            price = await self.collector.get_current_price(pair)
            if price:
                prices[pair] = price
        state = await self.portfolio.update_valuations(prices)

        db = SessionLocal()
        try:
            save_portfolio_snapshot(db, state)
        finally:
            db.close()

        await self.redis.publish("bot:live_updates", json.dumps({"type": "portfolio_update", "data": state}))

    async def _publish_status(self) -> None:
        await self.redis.set("bot:status", json.dumps({
            "status": self._status,
            "mode": config.trading.mode,
            "pairs": config.trading.pairs,
            "model_loaded": self.predictor.is_model_loaded(),
            "last_update": datetime.utcnow().isoformat(),
        }))
        await self.redis.publish("bot:live_updates", json.dumps({
            "type": "bot_status",
            "data": {"status": self._status, "mode": config.trading.mode}
        }))
