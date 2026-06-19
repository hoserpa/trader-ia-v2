"""Gestión de riesgo: filtro final antes de ejecutar cualquier operación."""
from datetime import datetime, timezone
from typing import Optional
from loguru import logger
from config import config
from database.crud import count_trades_today, get_open_positions
from database.init_db import SessionLocal


class RiskManager:
    """Evalúa si una señal del modelo puede convertirse en operación real.
    Aplica todas las reglas de gestión de riesgo configuradas.
    """

    def _hours_since(self, entry_timestamp) -> float:
        if entry_timestamp is None:
            return 0.0
        if isinstance(entry_timestamp, str):
            try:
                ts = datetime.fromisoformat(entry_timestamp.replace("Z", "+00:00"))
            except ValueError:
                return 0.0
        else:
            ts = entry_timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - ts).total_seconds() / 3600.0

    def can_buy(
        self,
        pair: str,
        signal: dict,
        portfolio: dict,
        current_price: float,
        atr: float,
        candles_with_indicators=None,
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

        if atr_pct < 0.002:
            return False, f"Volatilidad muy baja (ATR%={atr_pct:.2%} < 0.2%), sin señal", 0.0

        balance_eur = portfolio.get("balance_eur", 0)
        if balance_eur < config.risk.min_trade_eur:
            return False, f"Balance EUR insuficiente: {balance_eur:.2f} < {config.risk.min_trade_eur}", 0.0

        total_value = portfolio.get("total_value_eur", balance_eur)
        crypto_value = total_value - balance_eur
        if total_value > 0 and crypto_value / total_value >= config.risk.max_portfolio_in_crypto_pct:
            return False, f"Exposición en crypto al máximo: {crypto_value/total_value:.0%}", 0.0

        if candles_with_indicators is not None:
            passed, reason = self._check_technical_filters("BUY", candles_with_indicators)
            if not passed:
                return False, f"Filtro técnico rechazó BUY: {reason}", 0.0

        amount_eur = self.calculate_position_size(total_value, atr, current_price, balance_eur, signal)
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
        atr: float = None,
        candles_with_indicators=None,
    ) -> tuple[bool, str]:
        """Evalúa si se debe cerrar una posición.
        Retorna: (debe_vender, razón)
        """
        if current_price <= 0 or position.get("entry_price", 0) <= 0:
            return False, "precio inválido"

        entry_price = position["entry_price"]
        sl_price = position.get("stop_loss_price")
        tp_price = position.get("take_profit_price")
        entry_timestamp = position.get("entry_timestamp")

        pnl_pct = (current_price - entry_price) / entry_price * 100
        atr_used = atr or 0

        if sl_price and current_price <= sl_price:
            return True, f"stop_loss (precio={current_price:.2f} <= SL={sl_price:.2f}, pérdida={pnl_pct:.2f}%)"

        if tp_price and current_price >= tp_price:
            return True, f"take_profit (precio={current_price:.2f} >= TP={tp_price:.2f}, ganancia={pnl_pct:.2f}%)"

        trailing_stop = position.get("trailing_stop_price")
        if trailing_stop and current_price <= trailing_stop:
            return True, f"trailing_stop (precio={current_price:.2f} <= trailing={trailing_stop:.2f}, ganancia={pnl_pct:.2f}%)"

        hours_open = self._hours_since(entry_timestamp)
        if hours_open > config.risk.max_position_hours:
            return True, f"force_close (horas={hours_open:.1f} > max={config.risk.max_position_hours}h, PnL={pnl_pct:.2f}%)"

        if signal.get("signal") == "SELL" and signal.get("confidence", 0) >= config.risk.min_confidence_threshold:
            return True, f"model_signal (SELL con confianza={signal['confidence']:.2%})"

        if signal.get("signal") == "HOLD" and signal.get("confidence", 0) > 0.95:
            confidence = signal.get("confidence", 0)
            if candles_with_indicators is not None:
                passed, reason = self._check_technical_filters("SELL", candles_with_indicators)
                if passed:
                    return True, f"model_hold_with_technical_sell (confianza HOLD={confidence:.2%}, técnicos confirman SELL)"

        return False, "mantener posición"

    def calculate_trailing_stop(self, entry_price: float, current_price: float, atr: float) -> Optional[float]:
        """Calcula trailing stop si la posición está en ganancia suficiente."""
        pnl_pct = (current_price - entry_price) / entry_price
        activation = config.risk.trailing_stop_activation_pct

        if pnl_pct >= activation and atr > 0:
            trail_distance = atr * config.risk.trailing_stop_distance_atr
            proposed = current_price - trail_distance
            return round(proposed, 8)

        return None

    def should_take_partial_profit(
        self, entry_price: float, current_price: float, atr: float
    ) -> tuple[bool, float]:
        """Determina si se debe tomar ganancia parcial.
        Retorna: (tomar_ganancia, precio_objetivo)
        """
        risk_per_unit = atr * config.risk.stop_loss_atr_multiplier
        if risk_per_unit <= 0:
            return False, 0.0

        r_multiple = (current_price - entry_price) / risk_per_unit
        target_r = config.risk.partial_exit_r_multiple

        if r_multiple >= target_r:
            target_price = entry_price + (risk_per_unit * target_r)
            return True, round(target_price, 8)

        return False, 0.0

    def calculate_position_size(
        self,
        portfolio_value: float,
        atr: float,
        current_price: float,
        available_balance: float,
        signal: dict = None,
    ) -> float:
        """Calcula el importe en EUR a invertir usando sizing basado en riesgo.
        Si hay señal con confianza, escala el tamaño.
        """
        confidence = signal.get("confidence", 0) if signal else 0.5
        min_conf = config.risk.min_confidence_threshold
        max_conf = 0.30
        conf_multiplier = max(0.5, min(1.5, (confidence - min_conf) / (max_conf - min_conf + 1e-10)))

        base_risk = config.risk.max_risk_per_trade_pct
        risk_amount = portfolio_value * base_risk * conf_multiplier

        risk_amount = min(risk_amount, portfolio_value * 0.03)

        if atr > 0 and current_price > 0:
            atr_pct = atr / current_price
            stop_distance_pct = atr_pct * config.risk.stop_loss_atr_multiplier
            if stop_distance_pct > 0:
                position_size = risk_amount / stop_distance_pct
            else:
                position_size = risk_amount * 3
        else:
            position_size = risk_amount * 3

        max_position = portfolio_value * 0.10
        position_size = min(position_size, max_position)
        position_size = min(position_size, available_balance * 0.95)

        return round(position_size, 2)

    def calculate_stop_loss(self, entry_price: float, atr: float) -> float:
        return round(entry_price - (atr * config.risk.stop_loss_atr_multiplier), 8)

    def calculate_take_profit(self, entry_price: float, atr: float) -> float:
        return round(entry_price + (atr * config.risk.take_profit_atr_multiplier), 8)

    def _check_technical_filters(self, direction: str, candles: "pd.DataFrame") -> tuple[bool, str]:
        """Evalúa filtros técnicos para confirmar dirección."""
        import pandas as pd
        last = candles.iloc[-1]

        if "rsi_14" not in candles.columns or pd.isna(last.get("rsi_14")):
            return True, "sin RSI disponible"

        rsi = last["rsi_14"]

        if "ema_21" not in candles.columns or pd.isna(last.get("ema_21")):
            return True, "sin EMA disponible"

        price = last["close"]
        ema21 = last["ema_21"]
        macd_hist = last.get("macd_hist")
        bb_pct = last.get("bb_pct_b")

        if direction == "BUY":
            if rsi > config.risk.rsi_overbought:
                return False, f"RSI={rsi:.1f} > {config.risk.rsi_overbought} (sobrecompra)"
            if price < ema21:
                return False, f"precio={price:.2f} < EMA21={ema21:.2f} (downtrend)"
            if macd_hist is not None and not pd.isna(macd_hist) and macd_hist <= 0:
                return False, f"MACD hist={macd_hist:.4f} <= 0 (sin momentum alcista)"
            if bb_pct is not None and not pd.isna(bb_pct) and bb_pct > 0.95:
                return False, f"BB%={bb_pct:.2f} > 0.95 (precio en parte superior de bandas)"
            return True, "filtros OK"

        if direction == "SELL":
            if rsi < config.risk.rsi_oversold:
                return False, f"RSI={rsi:.1f} < {config.risk.rsi_oversold} (sobreventa)"
            if price > ema21:
                return False, f"precio={price:.2f} > EMA21={ema21:.2f} (uptrend)"
            if macd_hist is not None and not pd.isna(macd_hist) and macd_hist >= 0:
                return False, f"MACD hist={macd_hist:.4f} >= 0 (sin momentum bajista)"
            if bb_pct is not None and not pd.isna(bb_pct) and bb_pct < 0.05:
                return False, f"BB%={bb_pct:.2f} < 0.05 (precio en parte inferior de bandas)"
            return True, "filtros OK"

        return True, "sin dirección especificada"
