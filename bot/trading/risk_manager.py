"""Gestión de riesgo: filtro final antes de ejecutar cualquier operación."""
from datetime import datetime
from loguru import logger
from config import config
from database.crud import count_trades_today, get_open_positions
from database.init_db import SessionLocal


class RiskManager:
    """Evalúa si una señal del modelo puede convertirse en operación real.
    Aplica todas las reglas de gestión de riesgo configuradas.
    """

    def can_buy(
        self,
        pair: str,
        signal: dict,
        portfolio: dict,
        current_price: float,
        atr: float,
    ) -> tuple[bool, str, float]:
        """Evalúa si se puede abrir una posición de compra.
        Retorna: (puede_comprar, razón, importe_eur_a_invertir)
        """
        if signal.get("signal") != "BUY":
            return False, f"Señal no es BUY: {signal.get('signal')}", 0.0

        if signal.get("confidence", 0) < config.risk.min_confidence_threshold:
            return False, f"Confianza insuficiente: {signal['confidence']:.2%} < {config.risk.min_confidence_threshold:.2%}", 0.0

        db = SessionLocal()
        try:
            open_positions = get_open_positions(db)
        finally:
            db.close()

        pairs_with_positions = [p.pair for p in open_positions]
        if pair in pairs_with_positions:
            return False, f"Ya existe posición abierta en {pair}", 0.0

        if len(open_positions) >= config.risk.max_open_positions:
            return False, f"Máximo de posiciones abiertas alcanzado ({config.risk.max_open_positions})", 0.0

        db = SessionLocal()
        try:
            trades_today = count_trades_today(db)
        finally:
            db.close()

        if trades_today >= config.risk.max_daily_trades:
            return False, f"Máximo de trades diarios alcanzado ({config.risk.max_daily_trades})", 0.0

        atr_pct = atr / current_price if current_price > 0 else 0
        if atr_pct > config.risk.high_volatility_atr_threshold:
            return False, f"Alta volatilidad (ATR%={atr_pct:.2%} > {config.risk.high_volatility_atr_threshold:.2%})", 0.0

        balance_eur = portfolio.get("balance_eur", 0)
        if balance_eur < config.risk.min_trade_eur:
            return False, f"Balance EUR insuficiente: {balance_eur:.2f} < {config.risk.min_trade_eur}", 0.0

        total_value = portfolio.get("total_value_eur", balance_eur)
        crypto_value = total_value - balance_eur
        if total_value > 0 and crypto_value / total_value >= config.risk.max_portfolio_in_crypto_pct:
            return False, f"Exposición en crypto al máximo: {crypto_value/total_value:.0%}", 0.0

        amount_eur = self.calculate_position_size(total_value, atr, current_price, balance_eur)
        if amount_eur < config.risk.min_trade_eur:
            return False, f"Tamaño de posición calculado demasiado pequeño: {amount_eur:.2f}€", 0.0

        logger.info(f"✅ Risk OK para compra {pair}: {amount_eur:.2f}€ | confianza={signal['confidence']:.2%}")
        return True, "OK", amount_eur

    def can_sell(
        self,
        pair: str,
        position: dict,
        signal: dict,
        current_price: float,
    ) -> tuple[bool, str]:
        """Evalúa si se debe cerrar una posición.
        Retorna: (debe_vender, razón)
        """
        if current_price <= position["stop_loss_price"]:
            loss_pct = (current_price - position["entry_price"]) / position["entry_price"] * 100
            return True, f"stop_loss (precio={current_price:.2f} <= SL={position['stop_loss_price']:.2f}, pérdida={loss_pct:.2f}%)"

        if current_price >= position["take_profit_price"]:
            gain_pct = (current_price - position["entry_price"]) / position["entry_price"] * 100
            return True, f"take_profit (precio={current_price:.2f} >= TP={position['take_profit_price']:.2f}, ganancia={gain_pct:.2f}%)"

        if signal.get("signal") == "SELL" and signal.get("confidence", 0) >= config.risk.min_confidence_threshold:
            return True, f"signal (SELL con confianza={signal['confidence']:.2%})"

        return False, "mantener posición"

    def calculate_position_size(
        self,
        portfolio_value: float,
        atr: float,
        current_price: float,
        available_balance: float,
    ) -> float:
        """Calcula el importe en EUR a invertir usando sizing basado en riesgo.
        Fórmula: risk_amount / (ATR_pct * stop_loss_multiplier)
        """
        risk_amount = portfolio_value * config.risk.max_risk_per_trade_pct

        if atr > 0 and current_price > 0:
            atr_pct = atr / current_price
            stop_distance_pct = atr_pct * config.risk.stop_loss_atr_multiplier
            if stop_distance_pct > 0:
                position_size = risk_amount / stop_distance_pct
            else:
                position_size = risk_amount * 5
        else:
            position_size = risk_amount * 5

        max_position = portfolio_value * 0.20
        position_size = min(position_size, max_position)

        position_size = min(position_size, available_balance * 0.95)

        return round(position_size, 2)

    def calculate_stop_loss(self, entry_price: float, atr: float) -> float:
        return round(entry_price - (atr * config.risk.stop_loss_atr_multiplier), 8)

    def calculate_take_profit(self, entry_price: float, atr: float) -> float:
        return round(entry_price + (atr * config.risk.take_profit_atr_multiplier), 8)
