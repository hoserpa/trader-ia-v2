"""Abstraccion de broker para ejecutar ordenes en modo demo y real.

En modo demo el grid contabiliza los fills localmente y nunca llama al broker
para ejecutar: DemoBroker es un no-op. En modo real (RealBroker) las ordenes se
envian a Kraken via ccxt y el grid reconcilia con los fills reales.

Fail-closed: cualquier metodo de ejecucion sin implementar devuelve
`success=False`, de modo que un modo real con broker incompleto aborte en vez de
asumir un fill inexistente.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger

from config import config

ccxt = None  # type: ignore  # se importa de forma perezosa en RealBroker


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
        self._ccxt = None
        self._exchange = self._build_exchange()
        self._markets_loaded = False

    def _build_exchange(self):
        global ccxt
        try:
            import ccxt.async_support as _ccxt  # type: ignore

            self._ccxt = _ccxt
            ccxt = _ccxt
        except Exception as e:  # pragma: no cover
            logger.error(f"RealBroker: ccxt.async_support no disponible: {e}")
            return None
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

    async def _load_markets(self) -> None:
        if self._markets_loaded or not self._exchange:
            return
        await self._exchange.load_markets()
        self._markets_loaded = True

    def _params(self, margin: bool) -> Dict[str, Any]:
        """Parametros de la orden: leverage solo en piernas de margen (corto)."""
        if margin and not config.exchange.margin_enabled:
            return {}
        if margin and config.exchange.margin_enabled:
            return {"leverage": config.exchange.margin_leverage}
        return {}

    def _normalize(
        self,
        order: Optional[Dict[str, Any]],
        fallback_price: float = 0.0,
        pair: Optional[str] = None,
        order_type: str = "limit",
    ) -> ExecutionResult:
        if not order:
            return self._reject("respuesta vacia del exchange")
        filled = float(order.get("filled") or 0.0)
        avg = float(order.get("average") or order.get("price") or fallback_price or 0.0)
        fee = order.get("fee") or {}
        fees = order.get("fees") or []
        if not fee and fees:
            fee = fees[0]
        fee_cost = float(fee.get("cost") or 0.0)
        fee_currency = fee.get("currency")
        quote = (pair.split("/")[-1] if pair and "/" in pair else config.trading.base_currency)
        if fee_cost and fee_currency and str(fee_currency).upper() != str(quote).upper():
            if avg:
                fee_cost = fee_cost * avg
            else:
                fee_cost = 0.0
        if not fee_cost and filled and avg:
            rate = config.exchange.maker_fee if order_type == "limit" else config.exchange.taker_fee
            fee_cost = filled * avg * rate
        status = order.get("status")
        return ExecutionResult(
            success=order.get("id") is not None or filled > 0,
            filled_amount=filled,
            average_price=avg,
            fee_eur=round(fee_cost, 8),
            order_id=str(order["id"]) if order.get("id") else None,
            status=status,
            raw=order,
        )

    def _amount(self, symbol: str, amount: float) -> float:
        return float(self._exchange.amount_to_precision(symbol, amount))

    def _price(self, symbol: str, price: float) -> float:
        return float(self._exchange.price_to_precision(symbol, price))

    async def _await_fill(self, pair: str, order_id: str, timeout: float = 20.0) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(1)
            order = await self.fetch_order(pair, order_id)
            if not order:
                continue
            filled = float(order.get("filled") or 0.0)
            status = order.get("status")
            if status == "closed" or filled > 0:
                return order
        return await self.fetch_order(pair, order_id)

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
        if margin and not config.exchange.margin_enabled:
            return self._reject("margen no habilitado")
        await self._load_markets()
        symbol = await self.get_symbol(pair)
        client_id = str(uuid.uuid4())
        params = dict(self._params(margin))
        params["clientOrderId"] = client_id
        try:
            amount_q = self._amount(symbol, amount)
            price_q = self._price(symbol, price) if price else None
        except Exception as e:
            logger.error(f"[REAL-BROKER] precision {pair}: {e}")
            return self._reject(str(e))
        try:
            order = await self._exchange.create_order(
                symbol,
                order_type,
                side,
                amount_q,
                price_q,
                params,
            )
        except Exception as e:
            if isinstance(e, (getattr(self._ccxt, "RequestTimeout", Exception), getattr(self._ccxt, "NetworkError", Exception))):
                try:
                    for open_order in await self.fetch_open_orders(pair):
                        if str(open_order.get("clientOrderId") or open_order.get("info", {}).get("cl_ord_id")) == client_id:
                            result = self._normalize(open_order, price or 0.0, pair, order_type)
                            result.success = result.filled_amount > 0 if order_type == "market" else result.order_id is not None
                            return result
                except Exception:
                    pass
            logger.error(f"[REAL-BROKER] create {order_type} {side} {pair}: {e}")
            return self._reject(str(e))

        result = self._normalize(order, price or 0.0, pair, order_type)
        if order_type == "market" and result.status != "closed":
            filled_order = await self._await_fill(pair, result.order_id or "", 20.0)
            if filled_order:
                result = self._normalize(filled_order, price or 0.0, pair, order_type)
        if order_type == "market":
            result.success = result.filled_amount > 0
        else:
            result.success = result.order_id is not None
        return result

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
