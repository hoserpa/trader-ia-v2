"""Capa de broker: ejecucion de ordenes con la misma interfaz en modo demo y real.

Principio de diseno (requisito del usuario):
  - Los datos de MERCADO son siempre reales (precios, fees, soporte de margen).
  - La EJECUCION de ordenes es real en modo real (ccxt -> Kraken) y se SIMULA
    localmente en modo demo, replicando exactamente como responde/calcula Kraken,
    sin tocar saldos ni posiciones reales.

RealBroker y DemoBroker comparten el mismo contrato, de modo que la logica de
negocio (grid, señales) es identica en ambos modos y solo cambia el backend inyectado.
"""
import asyncio
from datetime import datetime
from loguru import logger
from config import config
from trading.portfolio import Portfolio
from trading.risk_manager import RiskManager


class ExecutionResult:
    """Resultado normalizado de una orden, identico en demo y real."""

    def __init__(self, **kwargs):
        self.trade_id = kwargs.get("trade_id")
        self.order_id = kwargs.get("order_id")
        self.pair = kwargs.get("pair")
        self.side = kwargs.get("side")
        self.position_type = kwargs.get("position_type")
        self.price = kwargs.get("price")
        self.entry_price = kwargs.get("entry_price")
        self.amount_crypto = kwargs.get("amount_crypto")
        self.amount_eur = kwargs.get("amount_eur")
        self.fee_eur = kwargs.get("fee_eur", 0.0)
        self.pnl_eur = kwargs.get("pnl_eur")
        self.status = kwargs.get("status", "closed")
        self.mode = kwargs.get("mode")
        self.leverage = kwargs.get("leverage")
        self.reason = kwargs.get("reason")

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


class ExecutionBroker:
    """Contrato base de broker. Subclases implementan la ejecucion (real o simulada)."""

    def __init__(self, portfolio: Portfolio, risk: RiskManager):
        self.portfolio = portfolio
        self.risk = risk
        self.mode = "base"
        self.margin_supported: dict = {}

    # -- Cursores -----------------------------------------------------------
    def has_short_support(self, pair: str) -> bool:
        """Devuelve si el par puede abrir shorts (con margen real).

        En modo DEMO se simula localmente (sin riesgo real), por lo que se permite
        simular shorts siempre que la API real del par lo soporte (short_ok), sin
        depender de allow_short/margin_enabled (que rigen solo la ejecucion REAL).

        En modo REAL se exige ademas allow_short + margin_enabled, para no abrir
        cortos que la API real no pudiera ejecutar (proteccion: no vender sin poseer).
        """
        if not config.trading.is_demo():
            if not config.exchange.allow_short or not config.exchange.margin_enabled:
                return False
        info = self.margin_supported.get(pair, {})
        if info.get("in_markets") is False:
            return False
        return bool(info.get("short_ok"))

    def set_margin_support(self, support: dict) -> None:
        self.margin_supported = support or {}

    def maximum_leverage(self, pair: str, position_type: str = "long") -> int:
        info = self.margin_supported.get(pair, {})
        if position_type == "short":
            return info.get("short_leverage") or 0
        return info.get("long_leverage") or 0

    def effective_leverage(self, pair: str, position_type: str) -> int:
        """Leverage a usar: el configurado, limitado al maximo real del par."""
        desired = config.exchange.margin_leverage
        if config.exchange.margin_enabled:
            max_lev = self.maximum_leverage(pair, position_type)
            if max_lev:
                return min(desired, max_lev)
        return 1

    def _reject(self, reason: str) -> ExecutionResult:
        return ExecutionResult(status="rejected", reason=reason)


class RealBroker(ExecutionBroker):
    """Ejecuta ordenes reales en Kraken via ccxt, con soporte de margen."""

    def __init__(self, portfolio: Portfolio, risk: RiskManager, exchange):
        super().__init__(portfolio, risk)
        self.mode = "real"
        self.exchange = exchange

    async def close(self) -> None:
        try:
            await self.exchange.close()
        except Exception as e:
            logger.debug(f"RealBroker.close: {e}")

    def _params(self, position_type: str = "long", reduce_only: bool = False) -> dict:
        params = {}
        lev = config.exchange.margin_enabled and config.exchange.margin_leverage
        if lev:
            params["leverage"] = lev
        if reduce_only:
            params["reduceOnly"] = True
        return params

    async def open_short(self, pair: str, amount_eur: float, price: float, atr: float):
        if not self.has_short_support(pair):
            return self._reject(
                f"Short {pair} no permitido: la API real no permite margen/corto en este par."
            )
        symbol = config.trading.get_symbol(pair)
        fee_rate = config.exchange.taker_fee
        amount_crypto = (amount_eur * (1 - fee_rate)) / price
        try:
            order = await self.exchange.create_market_sell_order(
                symbol, amount_crypto,
                self._params(position_type="short"),
            )
        except Exception as e:
            logger.error(f"RealBroker.open_short {pair}: {e}")
            return self._reject(str(e))
        filled = order.get("average", price)
        amount = order.get("filled", amount_crypto)
        fee = order.get("fee", {}).get("cost", amount_eur * fee_rate)
        logger.info(f"🔴 [REAL] SHORT {pair}: {amount:.8f} @ {filled:.2f}€ (fee={fee:.4f}€)")
        return ExecutionResult(
            order_id=order.get("id"), pair=pair, side="short",
            position_type="short", price=filled, entry_price=filled,
            amount_crypto=amount, amount_eur=round(amount_eur, 4),
            fee_eur=fee, status="closed", mode="real",
            leverage=self.effective_leverage(pair, "short"),
        )

    async def open_long(self, pair: str, amount_eur: float, price: float, atr: float):
        symbol = config.trading.get_symbol(pair)
        fee_rate = config.exchange.taker_fee
        amount_crypto = (amount_eur * (1 - fee_rate)) / price
        try:
            order = await self.exchange.create_market_buy_order(
                symbol, amount_crypto,
                self._params(position_type="long"),
            )
        except Exception as e:
            logger.error(f"RealBroker.open_long {pair}: {e}")
            return self._reject(str(e))
        filled = order.get("average", price)
        amount = order.get("filled", amount_crypto)
        fee = order.get("fee", {}).get("cost", amount_eur * fee_rate)
        logger.info(f"🟢 [REAL] LONG {pair}: {amount:.8f} @ {filled:.2f}€ (fee={fee:.4f}€)")
        return ExecutionResult(
            order_id=order.get("id"), pair=pair, side="buy",
            position_type="long", price=filled, entry_price=filled,
            amount_crypto=amount, amount_eur=round(amount_eur, 4),
            fee_eur=fee, status="closed", mode="real",
            leverage=self.effective_leverage(pair, "long"),
        )


class DemoBroker(ExecutionBroker):
    """Simula localmente la ejecucion replicando el calculo que haria Kraken,
    usando precio y fees reales del mercado. Nunca toca el portfolio real."""

    def __init__(self, portfolio: Portfolio, risk: RiskManager):
        super().__init__(portfolio, risk)
        self.mode = "demo"

    async def close(self) -> None:
        return None

    def _fee(self, amount_eur: float) -> float:
        return amount_eur * config.exchange.maker_fee

    async def open_short(self, pair: str, amount_eur: float, price: float, atr: float):
        if not self.has_short_support(pair):
            return self._reject(
                f"Short {pair} no permitido: la API real no permite margen/corto en este par. "
                "Modo demo replica fielmente el modo real y no abre shorts que real no podria."
            )
        lev = self.effective_leverage(pair, "short")
        fee = self._fee(amount_eur)
        net = amount_eur - fee
        amount_crypto = net / price
        # Simula el flujo de Kraken: recibe el proceeded neto del short.
        await self.portfolio.update_balance(net * lev)
        logger.info(
            f"🔴 [DEMO] SHORT {pair}: {amount_crypto:.8f} @ {price:.2f}€ "
            f"(fee={fee:.4f}€, lev={lev}x)"
        )
        return ExecutionResult(
            pair=pair, side="short", position_type="short",
            price=price, entry_price=price, amount_crypto=amount_crypto,
            amount_eur=round(amount_eur, 4), fee_eur=fee, status="closed",
            mode="demo", leverage=lev,
        )

    async def open_long(self, pair: str, amount_eur: float, price: float, atr: float):
        lev = self.effective_leverage(pair, "long")
        fee = self._fee(amount_eur)
        net = amount_eur - fee
        amount_crypto = net / price
        await self.portfolio.update_balance(-amount_eur)
        logger.info(
            f"🟢 [DEMO] LONG {pair}: {amount_crypto:.8f} @ {price:.2f}€ "
            f"(fee={fee:.4f}€, lev={lev}x)"
        )
        return ExecutionResult(
            pair=pair, side="buy", position_type="long",
            price=price, entry_price=price, amount_crypto=amount_crypto,
            amount_eur=round(amount_eur, 4), fee_eur=fee, status="closed",
            mode="demo", leverage=lev,
        )


def build_broker(portfolio: Portfolio, risk: RiskManager, exchange=None) -> ExecutionBroker:
    """Fabrica el broker segun el modo de trading.

    Args:
        portfolio: Portfolio.
        risk: RiskManager.
        exchange: instancia ccxt async (solo necesario en modo real).

    Returns:
        DemoBroker (modo demo) o RealBroker (modo real).
    """
    import ccxt.async_support as ccxt
    if config.trading.is_demo():
        broker = DemoBroker(portfolio, risk)
        logger.info("Broker: DEMO (ejecucion simulada local, mercado real)")
        return broker

    if exchange is None:
        exchange = getattr(ccxt, config.exchange.name.lower())({
            "apiKey": config.exchange.api_key,
            "secret": config.exchange.api_secret,
            "enableRateLimit": True,
        })
    broker = RealBroker(portfolio, risk, exchange)
    logger.info("Broker: REAL (ccxt -> Kraken)")
    return broker
