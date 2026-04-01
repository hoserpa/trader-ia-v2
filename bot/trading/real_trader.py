"""Motor de trading real via Exchange API."""
import asyncio
from datetime import datetime
import ccxt.async_support as ccxt
from loguru import logger
from config import config
from database import crud
from database.init_db import SessionLocal
from trading.portfolio import Portfolio
from trading.risk_manager import RiskManager


class RealTrader:
    """Ejecuta operaciones reales en el exchange configurado.
    
    ADVERTENCIA: Opera con dinero real. Usar solo tras validación exhaustiva en demo.
    """

    MAX_RETRIES = 3
    RETRY_DELAY = 5

    def __init__(self, portfolio: Portfolio, risk_manager: RiskManager):
        self.portfolio = portfolio
        self.risk = risk_manager
        self._consecutive_errors = 0
        self._circuit_open = False
        exchange_id = config.exchange.name.lower()
        self.exchange = getattr(ccxt, exchange_id)({
            "apiKey": config.exchange.api_key,
            "secret": config.exchange.api_secret,
            "enableRateLimit": True,
        })

    def _check_circuit_breaker(self) -> None:
        if self._circuit_open:
            raise RuntimeError("Circuit breaker abierto. Bot pausado por errores consecutivos. Revisión manual requerida.")

    async def execute_buy(self, pair: str, amount_eur: float, current_price: float, atr: float) -> dict:
        self._check_circuit_breaker()
        amount_crypto = (amount_eur * (1 - config.exchange.taker_fee)) / current_price

        for attempt in range(self.MAX_RETRIES):
            try:
                order = await self.exchange.create_market_buy_order(
                    pair, amount_crypto, params={"quoteOrderQty": amount_eur}
                )
                self._consecutive_errors = 0
                filled_price = order.get("average", current_price)
                filled_amount = order.get("filled", amount_crypto)
                fee_eur = order.get("fee", {}).get("cost", amount_eur * config.exchange.taker_fee)

                stop_loss = self.risk.calculate_stop_loss(filled_price, atr)
                take_profit = self.risk.calculate_take_profit(filled_price, atr)

                db = SessionLocal()
                try:
                    position = crud.create_position(db, {
                        "pair": pair,
                        "amount_crypto": filled_amount,
                        "entry_price": filled_price,
                        "stop_loss_price": stop_loss,
                        "take_profit_price": take_profit,
                        "amount_eur_invested": amount_eur - fee_eur,
                    })
                    trade = crud.create_trade(db, {
                        "position_id": position.id,
                        "pair": pair,
                        "side": "buy",
                        "amount_crypto": filled_amount,
                        "amount_eur": amount_eur,
                        "price": filled_price,
                        "fee_eur": fee_eur,
                        "mode": "real",
                        "exchange_order_id": order.get("id"),
                    })
                finally:
                    db.close()

                logger.info(f"🟢 [REAL] COMPRA {pair}: {filled_amount:.8f} @ {filled_price:.2f}€")
                return {
                    "trade_id": trade.id,
                    "position_id": position.id,
                    "pair": pair,
                    "side": "buy",
                    "price": filled_price,
                    "entry_price": filled_price,
                    "amount_eur": amount_eur,
                    "amount_crypto": filled_amount,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "fee_eur": fee_eur,
                    "mode": "real",
                }

            except ccxt.InsufficientFunds as e:
                logger.error(f"Fondos insuficientes para compra {pair}: {e}")
                return None
            except Exception as e:
                self._consecutive_errors += 1
                logger.error(f"Error en compra {pair} (intento {attempt+1}): {e}")
                if self._consecutive_errors >= 3:
                    self._circuit_open = True
                    logger.critical("🔴 Circuit breaker activado. Bot detenido.")
                    raise RuntimeError("Circuit breaker activado") from e
                await asyncio.sleep(self.RETRY_DELAY)
        return None

    async def execute_sell(self, pair: str, position, current_price: float, reason: str) -> dict:
        self._check_circuit_breaker()
        
        if isinstance(position, dict):
            amount_crypto = position["amount_crypto"]
            amount_eur_invested = position["amount_eur_invested"]
            entry_price = position["entry_price"]
            entry_timestamp = position.get("entry_timestamp")
            position_id = position["id"]
        else:
            amount_crypto = position.amount_crypto
            amount_eur_invested = position.amount_eur_invested
            entry_price = position.entry_price
            entry_timestamp = position.entry_timestamp
            position_id = position.id
        
        for attempt in range(self.MAX_RETRIES):
            try:
                order = await self.exchange.create_market_sell_order(pair, amount_crypto)
                self._consecutive_errors = 0
                filled_price = order.get("average", current_price)
                gross_eur = amount_crypto * filled_price
                fee_eur = order.get("fee", {}).get("cost", gross_eur * config.exchange.taker_fee)
                pnl_eur = (gross_eur - fee_eur) - amount_eur_invested

                db = SessionLocal()
                try:
                    crud.close_position(db, position_id, filled_price, reason)
                    trade = crud.create_trade(db, {
                        "position_id": position_id,
                        "pair": pair,
                        "side": "sell",
                        "amount_crypto": amount_crypto,
                        "amount_eur": gross_eur,
                        "price": filled_price,
                        "fee_eur": fee_eur,
                        "mode": "real",
                        "exchange_order_id": order.get("id"),
                    })
                finally:
                    db.close()

                logger.info(f"{'💚' if pnl_eur >= 0 else '🔴'} [REAL] VENTA {pair} @ {filled_price:.2f}€ | PnL={pnl_eur:+.2f}€ | {reason}")
                return {
                    "trade_id": trade.id,
                    "pair": pair,
                    "side": "sell",
                    "amount_crypto": amount_crypto,
                    "amount_eur": gross_eur,
                    "price": filled_price,
                    "entry_price": entry_price,
                    "entry_timestamp": entry_timestamp,
                    "fee_eur": fee_eur,
                    "pnl_eur": pnl_eur,
                    "pnl_pct": pnl_eur / amount_eur_invested * 100,
                    "close_reason": reason,
                    "mode": "real",
                }
            except Exception as e:
                self._consecutive_errors += 1
                logger.error(f"Error en venta {pair} (intento {attempt+1}): {e}")
                await asyncio.sleep(self.RETRY_DELAY)
        return None

    async def close(self):
        await self.exchange.close()
