"""Motor de trading simulado (modo demo)."""
from datetime import datetime
from loguru import logger
from config import config
from database import crud
from database.init_db import SessionLocal
from trading.portfolio import Portfolio
from trading.risk_manager import RiskManager


class DemoTrader:
    """Ejecuta operaciones de compra/venta simuladas con datos reales del mercado."""

    def __init__(self, portfolio: Portfolio, risk_manager: RiskManager):
        self.portfolio = portfolio
        self.risk = risk_manager

    async def execute_buy(self, pair: str, amount_eur: float, current_price: float, atr: float, db=None) -> dict:
        """Simula una compra. Retorna el dict del trade creado."""
        fee = amount_eur * config.exchange.taker_fee
        net_eur = amount_eur - fee
        amount_crypto = net_eur / current_price
        stop_loss = self.risk.calculate_stop_loss(current_price, atr)
        take_profit = self.risk.calculate_take_profit(current_price, atr)

        await self.portfolio.update_balance(-amount_eur)
        await self.portfolio.add_position(pair, {
            "amount_crypto": amount_crypto,
            "entry_price": current_price,
            "amount_eur_invested": net_eur,
            "stop_loss_price": stop_loss,
            "take_profit_price": take_profit,
            "entry_timestamp": datetime.utcnow().isoformat() + "Z",
        })

        if db is None:
            db = SessionLocal()
            close_db = True
        else:
            close_db = False

        try:
            position = crud.create_position(db, {
                "pair": pair,
                "amount_crypto": amount_crypto,
                "entry_price": current_price,
                "stop_loss_price": stop_loss,
                "take_profit_price": take_profit,
                "amount_eur_invested": net_eur,
            })
            trade = crud.create_trade(db, {
                "position_id": position.id,
                "pair": pair,
                "side": "buy",
                "amount_crypto": amount_crypto,
                "amount_eur": amount_eur,
                "price": current_price,
                "fee_eur": fee,
                "mode": "demo",
            })
        finally:
            if close_db:
                db.close()

        result = {
            "trade_id": trade.id,
            "position_id": position.id,
            "pair": pair,
            "side": "buy",
            "amount_eur": amount_eur,
            "amount_crypto": amount_crypto,
            "price": current_price,
            "entry_price": current_price,
            "fee_eur": fee,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "mode": "demo",
        }
        logger.info(f"🟢 [DEMO] COMPRA {pair}: {amount_crypto:.8f} @ {current_price:.2f}€ (inv={amount_eur:.2f}€, SL={stop_loss:.2f}, TP={take_profit:.2f})")
        return result

    async def execute_partial_sell(self, pair: str, position, current_price: float, fraction: float, reason: str, db=None) -> dict:
        """Vende una fracción de la posición. fraction=0.5 = vender 50%."""
        if isinstance(position, dict):
            amount_crypto = position["amount_crypto"] * fraction
            full_amount = position["amount_crypto"]
            amount_eur_invested = position["amount_eur_invested"]
            entry_price = position["entry_price"]
            position_id = position["id"]
        else:
            amount_crypto = position.amount_crypto * fraction
            full_amount = position.amount_crypto
            amount_eur_invested = position.amount_eur_invested
            entry_price = position.entry_price
            position_id = position.id

        gross_eur = amount_crypto * current_price
        fee = gross_eur * config.exchange.taker_fee
        net_eur = gross_eur - fee
        invested_part = amount_eur_invested * fraction
        pnl_eur = net_eur - invested_part

        await self.portfolio.update_balance(net_eur)
        remaining_crypto = full_amount - amount_crypto
        remaining_invested = amount_eur_invested - invested_part

        if remaining_crypto > 0.00000001:
            await self.portfolio.add_position(pair, {
                "amount_crypto": remaining_crypto,
                "entry_price": entry_price,
                "amount_eur_invested": remaining_invested,
                "stop_loss_price": position.get("stop_loss_price", 0),
                "take_profit_price": position.get("take_profit_price", 0),
                "entry_timestamp": position.get("entry_timestamp", datetime.utcnow().isoformat() + "Z"),
                "trailing_stop_price": position.get("trailing_stop_price"),
            })
        else:
            await self.portfolio.remove_position(pair)

        if db is None:
            db = SessionLocal()
            close_db = True
        else:
            close_db = False

        try:
            trade = crud.create_trade(db, {
                "position_id": position_id,
                "pair": pair,
                "side": "sell",
                "amount_crypto": amount_crypto,
                "amount_eur": gross_eur,
                "price": current_price,
                "fee_eur": fee,
                "mode": "demo",
            })
        finally:
            if close_db:
                db.close()

        emoji = "🔴" if pnl_eur < 0 else "💚"
        logger.info(f"{emoji} [DEMO] VENTA PARCIAL {pair}: {amount_crypto:.8f} @ {current_price:.2f}€ | PnL={pnl_eur:+.2f}€ | restante={remaining_crypto:.8f} | razón={reason}")
        return {
            "trade_id": trade.id,
            "pair": pair,
            "side": "sell",
            "amount_crypto": amount_crypto,
            "amount_eur": gross_eur,
            "price": current_price,
            "entry_price": entry_price,
            "fee_eur": fee,
            "pnl_eur": pnl_eur,
            "pnl_pct": pnl_eur / invested_part * 100 if invested_part > 0 else 0,
            "close_reason": reason,
            "partial": True,
            "remaining_crypto": remaining_crypto,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "mode": "demo",
        }

    async def execute_sell(self, pair: str, position, current_price: float, reason: str, db=None) -> dict:
        """Simula una venta/cierre de posición."""
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
            entry_timestamp = position.entry_timestamp.isoformat() + "Z" if hasattr(position.entry_timestamp, 'isoformat') else position.entry_timestamp
            position_id = position.id
        
        gross_eur = amount_crypto * current_price
        fee = gross_eur * config.exchange.taker_fee
        net_eur = gross_eur - fee
        pnl_eur = net_eur - amount_eur_invested

        await self.portfolio.update_balance(net_eur)
        await self.portfolio.remove_position(pair)

        if db is None:
            db = SessionLocal()
            close_db = True
        else:
            close_db = False

        try:
            closed = crud.close_position(db, position_id, current_price, reason)
            trade = crud.create_trade(db, {
                "position_id": position_id,
                "pair": pair,
                "side": "sell",
                "amount_crypto": amount_crypto,
                "amount_eur": gross_eur,
                "price": current_price,
                "fee_eur": fee,
                "mode": "demo",
            })
        finally:
            if close_db:
                db.close()

        emoji = "🔴" if pnl_eur < 0 else "💚"
        logger.info(f"{emoji} [DEMO] VENTA {pair}: {amount_crypto:.8f} @ {current_price:.2f}€ | PnL={pnl_eur:+.2f}€ | razón={reason}")
        return {
            "trade_id": trade.id,
            "pair": pair,
            "side": "sell",
            "amount_crypto": amount_crypto,
            "amount_eur": gross_eur,
            "price": current_price,
            "entry_price": entry_price,
            "entry_timestamp": entry_timestamp,
            "fee_eur": fee,
            "pnl_eur": pnl_eur,
            "pnl_pct": pnl_eur / amount_eur_invested * 100,
            "close_reason": reason,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "mode": "demo",
        }

    async def execute_short(self, pair: str, amount_eur: float, current_price: float, atr: float, db=None) -> dict:
        """Simula una venta corta (short). Retorna el dict del trade."""
        gross_eur = amount_eur
        fee = gross_eur * config.exchange.taker_fee
        net_eur = gross_eur - fee
        amount_crypto = net_eur / current_price
        stop_loss = self.risk.calculate_stop_loss(current_price, atr, position_type="short")
        take_profit = self.risk.calculate_take_profit(current_price, atr, position_type="short")

        await self.portfolio.update_balance(net_eur)
        await self.portfolio.add_position(pair, {
            "amount_crypto": amount_crypto,
            "entry_price": current_price,
            "amount_eur_invested": net_eur,
            "stop_loss_price": stop_loss,
            "take_profit_price": take_profit,
            "entry_timestamp": datetime.utcnow().isoformat() + "Z",
            "position_type": "short",
        })

        if db is None:
            db = SessionLocal()
            close_db = True
        else:
            close_db = False

        try:
            position = crud.create_position(db, {
                "pair": pair,
                "amount_crypto": amount_crypto,
                "entry_price": current_price,
                "stop_loss_price": stop_loss,
                "take_profit_price": take_profit,
                "amount_eur_invested": net_eur,
                "position_type": "short",
            })
            trade = crud.create_trade(db, {
                "position_id": position.id,
                "pair": pair,
                "side": "short",
                "amount_crypto": amount_crypto,
                "amount_eur": amount_eur,
                "price": current_price,
                "fee_eur": fee,
                "mode": "demo",
            })
        finally:
            if close_db:
                db.close()

        result = {
            "trade_id": trade.id,
            "position_id": position.id,
            "pair": pair,
            "side": "short",
            "amount_eur": amount_eur,
            "amount_crypto": amount_crypto,
            "price": current_price,
            "entry_price": current_price,
            "fee_eur": fee,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "mode": "demo",
        }
        logger.info(f"🔴 [DEMO] SHORT {pair}: {amount_crypto:.8f} @ {current_price:.2f}€ (recibido={net_eur:.2f}€, SL={stop_loss:.2f}, TP={take_profit:.2f})")
        return result

    async def execute_buy_to_close(self, pair: str, position, current_price: float, reason: str, db=None) -> dict:
        """Simula el cierre de un short (compra para cubrir)."""
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
            entry_timestamp = position.entry_timestamp.isoformat() + "Z" if hasattr(position.entry_timestamp, 'isoformat') else position.entry_timestamp
            position_id = position.id

        gross_eur = amount_crypto * current_price
        fee = gross_eur * config.exchange.taker_fee
        total_cost = gross_eur + fee
        pnl_eur = amount_eur_invested - total_cost

        await self.portfolio.update_balance(-total_cost)
        await self.portfolio.remove_position(pair)

        if db is None:
            db = SessionLocal()
            close_db = True
        else:
            close_db = False

        try:
            closed = crud.close_position(db, position_id, current_price, reason)
            trade = crud.create_trade(db, {
                "position_id": position_id,
                "pair": pair,
                "side": "buy_to_close",
                "amount_crypto": amount_crypto,
                "amount_eur": gross_eur,
                "price": current_price,
                "fee_eur": fee,
                "mode": "demo",
            })
        finally:
            if close_db:
                db.close()

        emoji = "💚" if pnl_eur >= 0 else "🔴"
        logger.info(f"{emoji} [DEMO] BUY_TO_COVER {pair}: {amount_crypto:.8f} @ {current_price:.2f}€ | PnL={pnl_eur:+.2f}€ | razón={reason}")
        return {
            "trade_id": trade.id,
            "pair": pair,
            "side": "buy_to_close",
            "amount_crypto": amount_crypto,
            "amount_eur": gross_eur,
            "price": current_price,
            "entry_price": entry_price,
            "entry_timestamp": entry_timestamp,
            "fee_eur": fee,
            "pnl_eur": pnl_eur,
            "pnl_pct": pnl_eur / amount_eur_invested * 100 if amount_eur_invested > 0 else 0,
            "close_reason": reason,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "mode": "demo",
        }

    async def execute_partial_buy_to_close(self, pair: str, position, current_price: float, fraction: float, reason: str, db=None) -> dict:
        """Compra (cubre) una fracción de un short. fraction=0.5 = cubrir 50%."""
        if isinstance(position, dict):
            amount_crypto = position["amount_crypto"] * fraction
            full_amount = position["amount_crypto"]
            amount_eur_invested = position["amount_eur_invested"]
            entry_price = position["entry_price"]
            position_id = position["id"]
        else:
            amount_crypto = position.amount_crypto * fraction
            full_amount = position.amount_crypto
            amount_eur_invested = position.amount_eur_invested
            entry_price = position.entry_price
            position_id = position.id

        gross_eur = amount_crypto * current_price
        fee = gross_eur * config.exchange.taker_fee
        total_cost = gross_eur + fee
        invested_part = amount_eur_invested * fraction
        pnl_eur = invested_part - total_cost

        await self.portfolio.update_balance(-total_cost)
        remaining_crypto = full_amount - amount_crypto
        remaining_invested = amount_eur_invested - invested_part

        if remaining_crypto > 0.00000001:
            await self.portfolio.add_position(pair, {
                "amount_crypto": remaining_crypto,
                "entry_price": entry_price,
                "amount_eur_invested": remaining_invested,
                "stop_loss_price": position.get("stop_loss_price", 0),
                "take_profit_price": position.get("take_profit_price", 0),
                "entry_timestamp": position.get("entry_timestamp", datetime.utcnow().isoformat() + "Z"),
                "trailing_stop_price": position.get("trailing_stop_price"),
                "position_type": "short",
            })
        else:
            await self.portfolio.remove_position(pair)

        if db is None:
            db = SessionLocal()
            close_db = True
        else:
            close_db = False

        try:
            trade = crud.create_trade(db, {
                "position_id": position_id,
                "pair": pair,
                "side": "buy_to_close",
                "amount_crypto": amount_crypto,
                "amount_eur": gross_eur,
                "price": current_price,
                "fee_eur": fee,
                "mode": "demo",
            })
        finally:
            if close_db:
                db.close()

        emoji = "💚" if pnl_eur >= 0 else "🔴"
        logger.info(f"{emoji} [DEMO] COBERTURA PARCIAL {pair}: {amount_crypto:.8f} @ {current_price:.2f}€ | PnL={pnl_eur:+.2f}€ | restante={remaining_crypto:.8f} | razón={reason}")
        return {
            "trade_id": trade.id,
            "pair": pair,
            "side": "buy_to_close",
            "amount_crypto": amount_crypto,
            "amount_eur": gross_eur,
            "price": current_price,
            "entry_price": entry_price,
            "fee_eur": fee,
            "pnl_eur": pnl_eur,
            "pnl_pct": pnl_eur / invested_part * 100 if invested_part > 0 else 0,
            "close_reason": reason,
            "partial": True,
            "remaining_crypto": remaining_crypto,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "mode": "demo",
        }
