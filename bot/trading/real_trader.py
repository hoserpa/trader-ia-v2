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

    async def _log_real_balance(self) -> dict | None:
        """Consulta balance real en exchange y valida disponibilidad."""
        try:
            bal = await self.exchange.fetch_balance()
            free_eur = bal.get("free", {}).get("EUR", 0)
            total_eur = bal.get("total", {}).get("EUR", 0)
            logger.debug(f"Balance real: {free_eur:.2f}€ libre / {total_eur:.2f}€ total")
            return bal
        except Exception as e:
            logger.warning(f"No se pudo consultar balance real: {e}")
            return None

    async def _cancel_exchange_order(self, order_id: str, pair: str) -> bool:
        """Cancela una orden en el exchange. Retorna True si se canceló OK."""
        if not order_id:
            return False
        try:
            await self.exchange.cancel_order(order_id, pair)
            logger.info(f"Orden cancelada en exchange: {order_id}")
            return True
        except Exception as e:
            logger.warning(f"Error cancelando orden {order_id}: {e}")
            return False

    async def _place_stop_loss_order(
        self, pair: str, side: str, amount: float, stop_price: float, position_id: int, db
    ) -> str | None:
        """Coloca una orden stop-loss en el exchange.
        Para longs: side='sell' (vender si baja a stop_price).
        Para shorts: side='buy' (comprar si sube a stop_price).
        Retorna exchange_order_id o None.
        """
        if not config.risk.exchange_stop_loss:
            return None
        for attempt in range(self.MAX_RETRIES):
            try:
                order = await self.exchange.create_order(
                    config.trading.get_symbol(pair),
                    "stop-loss",
                    side,
                    amount,
                    stop_price,
                )
                order_id = order.get("id")
                if order_id:
                    crud.update_position_order_ids(db, position_id, sl_order_id=order_id)
                    logger.info(f"Stop-loss colocada en exchange: {side} {amount:.8f} @ {stop_price:.2f}€ (order_id={order_id})")
                    return order_id
            except Exception as e:
                logger.warning(f"Error colocando stop-loss (intento {attempt+1}): {e}")
                await asyncio.sleep(self.RETRY_DELAY)
        logger.error(f"No se pudo colocar stop-loss para posición {position_id}")
        return None

    async def _update_stop_loss_order(
        self, pair: str, side: str, old_order_id: str | None,
        new_amount: float, new_stop_price: float, position_id: int, db
    ) -> str | None:
        """Cancela stop-loss anterior y coloca uno nuevo con cantidad actualizada."""
        if old_order_id:
            await self._cancel_exchange_order(old_order_id, config.trading.get_symbol(pair))
        return await self._place_stop_loss_order(pair, side, new_amount, new_stop_price, position_id, db)

    async def _try_limit_order(self, pair: str, side: str, amount: float, price: float) -> dict | None:
        """Intenta una orden limit. Retorna el order dict si se llenó, None si timeout."""
        limit_price = price * (0.999 if side == "buy" else 1.001)
        limit_price = round(limit_price, 2)
        symbol = config.trading.get_symbol(pair)
        try:
            if side == "buy":
                order = await self.exchange.create_limit_buy_order(symbol, amount, limit_price)
            else:
                order = await self.exchange.create_limit_sell_order(symbol, amount, limit_price)
            logger.info(f"Orden limit {side} {amount:.8f} @ {limit_price:.2f}€")
            await asyncio.sleep(config.risk.limit_order_timeout)
            fetched = await self.exchange.fetch_order(order["id"], symbol)
            filled = fetched.get("filled", 0) or 0
            if filled > 0:
                logger.info(f"Orden limit llenada: {filled:.8f} @ {fetched.get('average', limit_price):.2f}€")
                return fetched
            logger.info(f"Orden limit no se llenó en {config.risk.limit_order_timeout}s, cancelando...")
            await self._cancel_exchange_order(order["id"], symbol)
            return None
        except Exception as e:
            logger.debug(f"Orden limit falló: {e}")
            return None

    async def execute_buy(self, pair: str, amount_eur: float, current_price: float, atr: float, db=None) -> dict:
        self._check_circuit_breaker()

        bal = await self._log_real_balance()
        if bal is not None:
            free_eur = bal.get("free", {}).get("EUR", 0)
            if free_eur < amount_eur * 1.02:
                logger.error(f"Balance EUR insuficiente en exchange: {free_eur:.2f}€ < {amount_eur:.2f}€")
                return None

        amount_crypto = (amount_eur * (1 - config.exchange.taker_fee)) / current_price
        symbol = config.trading.get_symbol(pair)
        fee_rate = config.exchange.maker_fee

        for attempt in range(self.MAX_RETRIES):
            try:
                limit_order = await self._try_limit_order(pair, "buy", amount_crypto, current_price)
                if limit_order:
                    order = limit_order
                    fee_rate = config.exchange.maker_fee
                else:
                    order = await self.exchange.create_market_buy_order(symbol, amount_crypto)
                    fee_rate = config.exchange.taker_fee

                self._consecutive_errors = 0
                filled_price = order.get("average", current_price)
                filled_amount = order.get("filled", amount_crypto)
                fee_eur = order.get("fee", {}).get("cost", amount_eur * fee_rate)

                stop_loss = self.risk.calculate_stop_loss(filled_price, atr)
                take_profit = self.risk.calculate_take_profit(filled_price, atr)

                if db is None:
                    db = SessionLocal()
                    close_db = True
                else:
                    close_db = False

                try:
                    position = crud.create_position(db, {
                        "pair": pair,
                        "amount_crypto": filled_amount,
                        "entry_price": filled_price,
                        "stop_loss_price": stop_loss,
                        "take_profit_price": take_profit,
                        "amount_eur_invested": amount_eur - fee_eur,
                    })

                    sl_order_id = await self._place_stop_loss_order(
                        symbol, "sell", filled_amount, stop_loss, position.id, db
                    )

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
                    if close_db:
                        db.close()

                logger.info(f"🟢 [REAL] COMPRA {pair}: {filled_amount:.8f} @ {filled_price:.2f}€ (fee={fee_rate*100:.2f}%)")
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
                    "stop_loss_order_id": sl_order_id,
                    "fee_eur": fee_eur,
                    "fee_rate": fee_rate,
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

    async def execute_partial_sell(self, pair: str, position, current_price: float, fraction: float, reason: str, db=None) -> dict:
        """Vende una fracción de la posición en el exchange real."""
        if isinstance(position, dict):
            amount_crypto = position["amount_crypto"] * fraction
            full_amount = position["amount_crypto"]
            amount_eur_invested = position["amount_eur_invested"]
            entry_price = position["entry_price"]
            position_id = position["id"]
            old_sl_order_id = position.get("stop_loss_order_id")
        else:
            amount_crypto = position.amount_crypto * fraction
            full_amount = position.amount_crypto
            amount_eur_invested = position.amount_eur_invested
            entry_price = position.entry_price
            position_id = position.id
            old_sl_order_id = getattr(position, "stop_loss_order_id", None)

        symbol = config.trading.get_symbol(pair)
        remaining_crypto = full_amount - amount_crypto

        for attempt in range(self.MAX_RETRIES):
            try:
                order = await self.exchange.create_market_sell_order(symbol, amount_crypto)
                self._consecutive_errors = 0
                filled_price = order.get("average", current_price)
                gross_eur = amount_crypto * filled_price
                fee_eur = order.get("fee", {}).get("cost", gross_eur * config.exchange.taker_fee)
                invested_part = amount_eur_invested * fraction
                pnl_eur = (gross_eur - fee_eur) - invested_part

                if db is None:
                    db = SessionLocal()
                    close_db = True
                else:
                    close_db = False

                try:
                    if remaining_crypto > 0.00000001:
                        from database.models import Position as PositionModel
                        pos = db.query(PositionModel).get(position_id)
                        if pos:
                            pos.amount_crypto = remaining_crypto
                            pos.amount_eur_invested = amount_eur_invested - invested_part
                            db.commit()
                            new_sl_price = self.risk.calculate_stop_loss(
                                pos.entry_price, filled_price * 0.02,
                                position_type=getattr(pos, "position_type", "long")
                            )
                            await self._update_stop_loss_order(
                                symbol, "sell", old_sl_order_id,
                                remaining_crypto, new_sl_price, position_id, db
                            )
                    else:
                        await self._cancel_exchange_order(old_sl_order_id, symbol)
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
                    if close_db:
                        db.close()

                logger.info(f"💚 [REAL] VENTA PARCIAL {pair} @ {filled_price:.2f}€ | PnL={pnl_eur:+.2f}€ | restante={remaining_crypto:.8f} | {reason}")
                return {
                    "trade_id": trade.id,
                    "pair": pair,
                    "side": "sell",
                    "amount_crypto": amount_crypto,
                    "amount_eur": gross_eur,
                    "price": filled_price,
                    "entry_price": entry_price,
                    "fee_eur": fee_eur,
                    "pnl_eur": pnl_eur,
                    "pnl_pct": pnl_eur / invested_part * 100 if invested_part > 0 else 0,
                    "close_reason": reason,
                    "partial": True,
                    "remaining_crypto": remaining_crypto,
                    "mode": "real",
                }
            except Exception as e:
                self._consecutive_errors += 1
                logger.error(f"Error en venta parcial {pair} (intento {attempt+1}): {e}")
                await asyncio.sleep(self.RETRY_DELAY)
        return None

    async def execute_sell(self, pair: str, position, current_price: float, reason: str, db=None) -> dict:
        self._check_circuit_breaker()

        if isinstance(position, dict):
            amount_crypto = position["amount_crypto"]
            amount_eur_invested = position["amount_eur_invested"]
            entry_price = position["entry_price"]
            entry_timestamp = position.get("entry_timestamp")
            position_id = position["id"]
            sl_order_id = position.get("stop_loss_order_id")
        else:
            amount_crypto = position.amount_crypto
            amount_eur_invested = position.amount_eur_invested
            entry_price = position.entry_price
            entry_timestamp = position.entry_timestamp.isoformat() + "Z" if hasattr(position.entry_timestamp, 'isoformat') else position.entry_timestamp
            position_id = position.id
            sl_order_id = getattr(position, "stop_loss_order_id", None)

        symbol = config.trading.get_symbol(pair)

        await self._cancel_exchange_order(sl_order_id, symbol)

        for attempt in range(self.MAX_RETRIES):
            try:
                order = await self.exchange.create_market_sell_order(symbol, amount_crypto)
                self._consecutive_errors = 0
                filled_price = order.get("average", current_price)
                gross_eur = amount_crypto * filled_price
                fee_eur = order.get("fee", {}).get("cost", gross_eur * config.exchange.taker_fee)
                pnl_eur = (gross_eur - fee_eur) - amount_eur_invested

                if db is None:
                    db = SessionLocal()
                    close_db = True
                else:
                    close_db = False

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
                    if close_db:
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

    async def execute_short(self, pair: str, amount_eur: float, current_price: float, atr: float, db=None) -> dict:
        """Ejecuta una venta corta real en el exchange."""
        self._check_circuit_breaker()

        bal = await self._log_real_balance()
        if bal is not None:
            free_eur = bal.get("free", {}).get("EUR", 0)
            if free_eur < amount_eur * 1.02:
                logger.error(f"Balance EUR insuficiente en exchange: {free_eur:.2f}€ < {amount_eur:.2f}€")
                return None

        amount_crypto = (amount_eur * (1 - config.exchange.taker_fee)) / current_price
        symbol = config.trading.get_symbol(pair)
        fee_rate = config.exchange.maker_fee

        for attempt in range(self.MAX_RETRIES):
            try:
                limit_order = await self._try_limit_order(pair, "sell", amount_crypto, current_price)
                if limit_order:
                    order = limit_order
                    fee_rate = config.exchange.maker_fee
                else:
                    order = await self.exchange.create_market_sell_order(symbol, amount_crypto)
                    fee_rate = config.exchange.taker_fee

                self._consecutive_errors = 0
                filled_price = order.get("average", current_price)
                filled_amount = order.get("filled", amount_crypto)
                fee_eur = order.get("fee", {}).get("cost", amount_eur * fee_rate)
                gross_eur = filled_amount * filled_price

                stop_loss = self.risk.calculate_stop_loss(filled_price, atr, position_type="short")
                take_profit = self.risk.calculate_take_profit(filled_price, atr, position_type="short")

                if db is None:
                    db = SessionLocal()
                    close_db = True
                else:
                    close_db = False

                try:
                    position = crud.create_position(db, {
                        "pair": pair,
                        "amount_crypto": filled_amount,
                        "entry_price": filled_price,
                        "stop_loss_price": stop_loss,
                        "take_profit_price": take_profit,
                        "amount_eur_invested": gross_eur - fee_eur,
                        "position_type": "short",
                    })

                    sl_order_id = await self._place_stop_loss_order(
                        symbol, "buy", filled_amount, stop_loss, position.id, db
                    )

                    trade = crud.create_trade(db, {
                        "position_id": position.id,
                        "pair": pair,
                        "side": "short",
                        "amount_crypto": filled_amount,
                        "amount_eur": gross_eur,
                        "price": filled_price,
                        "fee_eur": fee_eur,
                        "mode": "real",
                        "exchange_order_id": order.get("id"),
                    })
                finally:
                    if close_db:
                        db.close()

                logger.info(f"🔴 [REAL] SHORT {pair}: {filled_amount:.8f} @ {filled_price:.2f}€ (fee={fee_rate*100:.2f}%)")
                return {
                    "trade_id": trade.id,
                    "position_id": position.id,
                    "pair": pair,
                    "side": "short",
                    "price": filled_price,
                    "entry_price": filled_price,
                    "amount_eur": gross_eur,
                    "amount_crypto": filled_amount,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "stop_loss_order_id": sl_order_id,
                    "fee_eur": fee_eur,
                    "fee_rate": fee_rate,
                    "mode": "real",
                }

            except ccxt.InsufficientFunds as e:
                logger.error(f"Fondos insuficientes para short {pair}: {e}")
                return None
            except Exception as e:
                self._consecutive_errors += 1
                logger.error(f"Error en short {pair} (intento {attempt+1}): {e}")
                if self._consecutive_errors >= 3:
                    self._circuit_open = True
                    logger.critical("🔴 Circuit breaker activado. Bot detenido.")
                    raise RuntimeError("Circuit breaker activado") from e
                await asyncio.sleep(self.RETRY_DELAY)
        return None

    async def execute_buy_to_close(self, pair: str, position, current_price: float, reason: str, db=None) -> dict:
        """Compra para cubrir un short en el exchange real."""
        self._check_circuit_breaker()

        if isinstance(position, dict):
            amount_crypto = position["amount_crypto"]
            amount_eur_invested = position["amount_eur_invested"]
            entry_price = position["entry_price"]
            entry_timestamp = position.get("entry_timestamp")
            position_id = position["id"]
            sl_order_id = position.get("stop_loss_order_id")
        else:
            amount_crypto = position.amount_crypto
            amount_eur_invested = position.amount_eur_invested
            entry_price = position.entry_price
            entry_timestamp = position.entry_timestamp.isoformat() + "Z" if hasattr(position.entry_timestamp, 'isoformat') else position.entry_timestamp
            position_id = position.id
            sl_order_id = getattr(position, "stop_loss_order_id", None)

        symbol = config.trading.get_symbol(pair)

        await self._cancel_exchange_order(sl_order_id, symbol)

        for attempt in range(self.MAX_RETRIES):
            try:
                order = await self.exchange.create_market_buy_order(symbol, amount_crypto)
                self._consecutive_errors = 0
                filled_price = order.get("average", current_price)
                gross_eur = amount_crypto * filled_price
                fee_eur = order.get("fee", {}).get("cost", gross_eur * config.exchange.taker_fee)
                total_cost = gross_eur + fee_eur
                pnl_eur = amount_eur_invested - total_cost

                if db is None:
                    db = SessionLocal()
                    close_db = True
                else:
                    close_db = False

                try:
                    crud.close_position(db, position_id, filled_price, reason)
                    trade = crud.create_trade(db, {
                        "position_id": position_id,
                        "pair": pair,
                        "side": "buy_to_close",
                        "amount_crypto": amount_crypto,
                        "amount_eur": gross_eur,
                        "price": filled_price,
                        "fee_eur": fee_eur,
                        "mode": "real",
                        "exchange_order_id": order.get("id"),
                    })
                finally:
                    if close_db:
                        db.close()

                logger.info(f"{'💚' if pnl_eur >= 0 else '🔴'} [REAL] BUY_TO_COVER {pair} @ {filled_price:.2f}€ | PnL={pnl_eur:+.2f}€ | {reason}")
                return {
                    "trade_id": trade.id,
                    "pair": pair,
                    "side": "buy_to_close",
                    "amount_crypto": amount_crypto,
                    "amount_eur": gross_eur,
                    "price": filled_price,
                    "entry_price": entry_price,
                    "entry_timestamp": entry_timestamp,
                    "fee_eur": fee_eur,
                    "pnl_eur": pnl_eur,
                    "pnl_pct": pnl_eur / amount_eur_invested * 100 if amount_eur_invested > 0 else 0,
                    "close_reason": reason,
                    "mode": "real",
                }
            except Exception as e:
                self._consecutive_errors += 1
                logger.error(f"Error en buy_to_close {pair} (intento {attempt+1}): {e}")
                await asyncio.sleep(self.RETRY_DELAY)
        return None

    async def execute_partial_buy_to_close(self, pair: str, position, current_price: float, fraction: float, reason: str, db=None) -> dict:
        """Compra una fracción de un short para tomar ganancia parcial."""
        if isinstance(position, dict):
            amount_crypto = position["amount_crypto"] * fraction
            full_amount = position["amount_crypto"]
            amount_eur_invested = position["amount_eur_invested"]
            entry_price = position["entry_price"]
            position_id = position["id"]
            old_sl_order_id = position.get("stop_loss_order_id")
        else:
            amount_crypto = position.amount_crypto * fraction
            full_amount = position.amount_crypto
            amount_eur_invested = position.amount_eur_invested
            entry_price = position.entry_price
            position_id = position.id
            old_sl_order_id = getattr(position, "stop_loss_order_id", None)

        symbol = config.trading.get_symbol(pair)
        remaining_crypto = full_amount - amount_crypto

        for attempt in range(self.MAX_RETRIES):
            try:
                order = await self.exchange.create_market_buy_order(symbol, amount_crypto)
                self._consecutive_errors = 0
                filled_price = order.get("average", current_price)
                gross_eur = amount_crypto * filled_price
                fee_eur = order.get("fee", {}).get("cost", gross_eur * config.exchange.taker_fee)
                total_cost = gross_eur + fee_eur
                invested_part = amount_eur_invested * fraction
                pnl_eur = invested_part - total_cost

                if db is None:
                    db = SessionLocal()
                    close_db = True
                else:
                    close_db = False

                try:
                    if remaining_crypto > 0.00000001:
                        from database.models import Position as PositionModel
                        pos = db.query(PositionModel).get(position_id)
                        if pos:
                            pos.amount_crypto = remaining_crypto
                            pos.amount_eur_invested = amount_eur_invested - invested_part
                            db.commit()
                            new_sl_price = self.risk.calculate_stop_loss(
                                pos.entry_price, filled_price * 0.02,
                                position_type="short"
                            )
                            await self._update_stop_loss_order(
                                symbol, "buy", old_sl_order_id,
                                remaining_crypto, new_sl_price, position_id, db
                            )
                    else:
                        await self._cancel_exchange_order(old_sl_order_id, symbol)
                        crud.close_position(db, position_id, filled_price, reason)

                    trade = crud.create_trade(db, {
                        "position_id": position_id,
                        "pair": pair,
                        "side": "buy_to_close",
                        "amount_crypto": amount_crypto,
                        "amount_eur": gross_eur,
                        "price": filled_price,
                        "fee_eur": fee_eur,
                        "mode": "real",
                        "exchange_order_id": order.get("id"),
                    })
                finally:
                    if close_db:
                        db.close()

                logger.info(f"💚 [REAL] PARCIAL BUY_TO_COVER {pair} @ {filled_price:.2f}€ | PnL={pnl_eur:+.2f}€ | restante={remaining_crypto:.8f} | {reason}")
                return {
                    "trade_id": trade.id,
                    "pair": pair,
                    "side": "buy_to_close",
                    "amount_crypto": amount_crypto,
                    "amount_eur": gross_eur,
                    "price": filled_price,
                    "entry_price": entry_price,
                    "fee_eur": fee_eur,
                    "pnl_eur": pnl_eur,
                    "pnl_pct": pnl_eur / invested_part * 100 if invested_part > 0 else 0,
                    "close_reason": reason,
                    "partial": True,
                    "remaining_crypto": remaining_crypto,
                    "mode": "real",
                }
            except Exception as e:
                self._consecutive_errors += 1
                logger.error(f"Error en partial buy_to_close {pair} (intento {attempt+1}): {e}")
                await asyncio.sleep(self.RETRY_DELAY)
        return None

    async def close(self):
        await self.exchange.close()
