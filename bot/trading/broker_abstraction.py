"""Abstraccion de broker para ejecutar ordenes en modo demo y real.

En modo demo el grid contabiliza los fills localmente y nunca llama al broker
para ejecutar: DemoBroker es un no-op. En modo real (RealBroker) las ordenes se
envian a Kraken via ccxt y el grid reconcilia con los fills reales.

Fail-closed: cualquier metodo de ejecucion sin implementar devuelve
`success=False`, de modo que un modo real con broker incompleto aborte en vez de
asumir un fill inexistente.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger

from config import config

try:
    import ccxt.async_support as ccxt  # type: ignore
except Exception:  # pragma: no cover
    import ccxt  # type: ignore


@dataclass
class ExecutionResult:
    success: bool
    filled_amount: float = 0.0
    average_price: float = 0.0
    fee_eur: float = 0.0
    order_id: Optional[str] = None
    status: Optional[str] = None
    error: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class BaseBroker:
    """Contrato de broker. Los metodos de ejecucion fallan explicitamente."""

    mode = "base"

    async def get_symbol(self, pair: str) -> str:
        return pair

    async def cancel_order(self, pair: str, order_id: str) -> bool:
        return False

    async def cancel_orders_for_pair(self, pair: str) -> bool:
        return False

    async def fetch_open_orders(self, pair: str) -> List[Dict[str, Any]]:
        return []

    async def fetch_order(self, pair: str, order_id: str) -> Optional[Dict[str, Any]]:
        return None

    async def fetch_order_result(self, pair: str, order_id: str) -> Optional["ExecutionResult"]:
        return None

    async def fetch_ticker(self, pair: str) -> Dict[str, Any]:
        return {}

    def _reject(self, reason: str) -> ExecutionResult:
        return ExecutionResult(success=False, error=reason)

    async def create_limit_buy(
        self, pair: str, amount_crypto: float, price: float, margin: bool = False
    ) -> ExecutionResult:
        return self._reject("broker de ejecucion no implementado")

    async def create_limit_sell(
        self, pair: str, amount_crypto: float, price: float, margin: bool = False
    ) -> ExecutionResult:
        return self._reject("broker de ejecucion no implementado")

    async def create_market_buy(
        self, pair: str, amount_crypto: float, margin: bool = False
    ) -> ExecutionResult:
        return self._reject("broker de ejecucion no implementado")

    async def create_market_sell(
        self, pair: str, amount_crypto: float, margin: bool = False
    ) -> ExecutionResult:
        return self._reject("broker de ejecucion no implementado")

    async def close_short_market(
        self, pair: str, amount_crypto: float, reference_price: float = 0.0
    ) -> ExecutionResult:
        return self._reject("broker de ejecucion no implementado")

    async def close_long_market(
        self, pair: str, amount_crypto: float, reference_price: float = 0.0
    ) -> ExecutionResult:
        return self._reject("broker de ejecucion no implementado")

    async def close(self) -> None:
        pass


class DemoBroker(BaseBroker):
    mode = "demo"

    def __init__(self):
        logger.debug("DemoBroker inicializado (sin ejecucion externa)")


class RealBroker(BaseBroker):
    """Ejecuta ordenes reales en Kraken via ccxt.

    AVISO: opera con dinero real. Validar en testnet antes de usar en produccion.
    """

    mode = "real"

    def __init__(self):
        self._exchange = self._build_exchange()

    def _build_exchange(self):
        api_key = config.exchange.api_key
        api_secret = config.exchange.api_secret
        if not api_key or not api_secret:
            logger.error(
                "RealBroker: KRAKEN_API_KEY/SECRET vacios; ejecucion real deshabilitada"
            )
            return None
        name = (config.exchange.name or "kraken").lower()
        exchange_class = getattr(ccxt, name, None)
        if exchange_class is None:
            logger.error(f"RealBroker: ccxt.{name} no disponible")
            return None
        try:
            exchange = exchange_class(
                {"apiKey": api_key, "secret": api_secret, "enableRateLimit": True}
            )
            logger.info(f"RealBroker inicializado (ccxt.{name})")
            return exchange
        except Exception as e:
            logger.exception(f"RealBroker init error: {e}")
            return None

    @property
    def exchange(self):
        return self._exchange

    def _params(self, margin: bool) -> Dict[str, Any]:
        """Parametros de la orden: leverage solo en piernas de margen (corto)."""
        if margin and config.exchange.margin_enabled:
            return {"leverage": config.exchange.margin_leverage}
        return {}

    def _normalize(self, order: Optional[Dict[str, Any]], fallback_price: float = 0.0) -> ExecutionResult:
        if not order:
            return self._reject("respuesta vacia del exchange")
        filled = float(order.get("filled") or 0.0)
        avg = float(order.get("average") or order.get("price") or fallback_price or 0.0)
        fee = order.get("fee") or {}
        fee_eur = float(fee.get("cost") or 0.0)
        if not fee_eur and filled and avg:
            fee_eur = filled * avg * config.exchange.taker_fee
        status = order.get("status")
        success = order.get("id") is not None or filled > 0
        return ExecutionResult(
            success=success,
            filled_amount=filled,
            average_price=avg,
            fee_eur=round(fee_eur, 8),
            order_id=str(order["id"]) if order.get("id") else None,
            status=status,
            raw=order,
        )

    def _amount(self, symbol: str, amount: float) -> float:
        try:
            return float(self._exchange.amount_to_precision(symbol, amount))
        except Exception:
            return amount

    def _price(self, symbol: str, price: float) -> float:
        try:
            return float(self._exchange.price_to_precision(symbol, price))
        except Exception:
            return price

    async def _create(
        self,
        pair: str,
        order_type: str,
        side: str,
        amount: float,
        price: Optional[float],
        margin: bool = False,
    ) -> ExecutionResult:
        if not self._exchange:
            return self._reject("exchange no inicializado")
        symbol = await self.get_symbol(pair)
        try:
            order = await self._exchange.create_order(
                symbol,
                order_type,
                side,
                self._amount(symbol, amount),
                self._price(symbol, price) if price else None,
                self._params(margin),
            )
            return self._normalize(order, price or 0.0)
        except Exception as e:
            logger.error(f"[REAL-BROKER] create {order_type} {side} {pair}: {e}")
            return self._reject(str(e))

    async def create_limit_buy(
        self, pair: str, amount_crypto: float, price: float, margin: bool = False
    ) -> ExecutionResult:
        return await self._create(pair, "limit", "buy", amount_crypto, price, margin)

    async def create_limit_sell(
        self, pair: str, amount_crypto: float, price: float, margin: bool = False
    ) -> ExecutionResult:
        return await self._create(pair, "limit", "sell", amount_crypto, price, margin)

    async def create_market_buy(
        self, pair: str, amount_crypto: float, margin: bool = False
    ) -> ExecutionResult:
        return await self._create(pair, "market", "buy", amount_crypto, None, margin)

    async def create_market_sell(
        self, pair: str, amount_crypto: float, margin: bool = False
    ) -> ExecutionResult:
        return await self._create(pair, "market", "sell", amount_crypto, None, margin)

    async def close_short_market(
        self, pair: str, amount_crypto: float, reference_price: float = 0.0
    ) -> ExecutionResult:
        """Cerrar un corto = comprar la base prestada (con margen)."""
        return await self.create_market_buy(pair, amount_crypto, margin=True)

    async def close_long_market(
        self, pair: str, amount_crypto: float, reference_price: float = 0.0
    ) -> ExecutionResult:
        return await self.create_market_sell(pair, amount_crypto, margin=False)

    async def cancel_order(self, pair: str, order_id: str) -> bool:
        if not self._exchange or not order_id:
            return False
        try:
            symbol = await self.get_symbol(pair)
            await self._exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            logger.error(f"[REAL-BROKER] cancel_order {order_id} {pair}: {e}")
            return False

    async def cancel_orders_for_pair(self, pair: str) -> bool:
        if not self._exchange:
            return False
        orders = await self.fetch_open_orders(pair)
        ok = True
        for order in orders or []:
            order_id = order.get("id")
            if order_id:
                ok = await self.cancel_order(pair, order_id) and ok
        return ok

    async def fetch_open_orders(self, pair: str) -> List[Dict[str, Any]]:
        if not self._exchange:
            return []
        try:
            symbol = await self.get_symbol(pair)
            return await self._exchange.fetch_open_orders(symbol) or []
        except Exception as e:
            logger.error(f"[REAL-BROKER] fetch_open_orders {pair}: {e}")
            return []

    async def fetch_order(self, pair: str, order_id: str) -> Optional[Dict[str, Any]]:
        if not self._exchange or not order_id:
            return None
        try:
            symbol = await self.get_symbol(pair)
            return await self._exchange.fetch_order(order_id, symbol)
        except Exception as e:
            logger.error(f"[REAL-BROKER] fetch_order {order_id} {pair}: {e}")
            return None

    async def fetch_order_result(self, pair: str, order_id: str) -> Optional[ExecutionResult]:
        order = await self.fetch_order(pair, order_id)
        if order is None:
            return None
        return self._normalize(order)

    async def fetch_balance(self) -> Dict[str, Any]:
        if not self._exchange:
            return {}
        try:
            return await self._exchange.fetch_balance() or {}
        except Exception as e:
            logger.error(f"[REAL-BROKER] fetch_balance: {e}")
            return {}

    async def fetch_ticker(self, pair: str) -> Dict[str, Any]:
        if not self._exchange:
            return {}
        try:
            symbol = await self.get_symbol(pair)
            return await self._exchange.fetch_ticker(symbol) or {}
        except Exception as e:
            logger.error(f"[REAL-BROKER] fetch_ticker {pair}: {e}")
            return {}

    async def close(self) -> None:
        if self._exchange:
            try:
                await self._exchange.close()
            except Exception:
                pass


def build_broker():
    mode = (config.trading.mode or "demo").lower()
    if mode == "real":
        return RealBroker()
    return DemoBroker()
