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

    async def execute_buy(self, pair: str, amount_eur: float, current_price: float, atr: float) -> dict:
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
            "entry_timestamp": datetime.utcnow().isoformat(),
        })

        db = SessionLocal()
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
            "timestamp": datetime.utcnow().isoformat(),
            "mode": "demo",
        }
        logger.info(f"🟢 [DEMO] COMPRA {pair}: {amount_crypto:.8f} @ {current_price:.2f}€ (inv={amount_eur:.2f}€, SL={stop_loss:.2f}, TP={take_profit:.2f})")
        return result

    async def execute_sell(self, pair: str, position, current_price: float, reason: str) -> dict:
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
            entry_timestamp = position.entry_timestamp
            position_id = position.id
        
        gross_eur = amount_crypto * current_price
        fee = gross_eur * config.exchange.taker_fee
        net_eur = gross_eur - fee
        pnl_eur = net_eur - amount_eur_invested

        await self.portfolio.update_balance(net_eur)
        await self.portfolio.remove_position(pair)

        db = SessionLocal()
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
            "timestamp": datetime.utcnow().isoformat(),
            "mode": "demo",
        }
