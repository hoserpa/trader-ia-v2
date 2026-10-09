"""Abstracción de broker para soportar modos demo y real."""
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
    fee_cost: float = 0.0
    order_id: Optional[str] = None
    error: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class BaseBroker:
    async def get_symbol(self, pair: str) -> str:
        return config.exchange.get_symbol(pair)

    async def cancel_all_orders(self, pair: str) -> bool:
        return True

    async def cancel_orders_for_pair(self, pair: str) -> bool:
        return True

    async def fetch_open_orders(self, pair: str) -> List[Dict[str, Any]]:
        return []

    async def fetch_positions(self, pair: Optional[str] = None) -> List[Dict[str, Any]]:
        return []

    async def fetch_balance(self) -> Dict[str, Any]:
        return {}

    async def fetch_ticker(self, pair: str) -> Dict[str, Any]:
        return {}

    async def create_limit_buy(self, pair: str, amount_crypto: float, price: float) -> ExecutionResult:
        return ExecutionResult(success=True)

    async def create_limit_sell(self, pair: str, amount_crypto: float, price: float) -> ExecutionResult:
        return ExecutionResult(success=True)

    async def create_market_buy(self, pair: str, amount_eur_or_crypto: float, use_eur: bool = True) -> ExecutionResult:
        return ExecutionResult(success=True)

    async def create_market_sell(self, pair: str, amount_crypto: float) -> ExecutionResult:
        return ExecutionResult(success=True)

    async def close_short_market(self, pair: str, amount_crypto: float, reference_price: float) -> ExecutionResult:
        return ExecutionResult(success=True)

    async def close_long_market(self, pair: str, amount_crypto: float, reference_price: float) -> ExecutionResult:
        return ExecutionResult(success=True)

    async def close_all_exposure(self, pair: str, current_price: float) -> ExecutionResult:
        return ExecutionResult(success=True)

    async def flatten_all(self, pair: str, current_price: float = 0.0) -> ExecutionResult:
        return ExecutionResult(success=True)

    async def flatten_all(self, pair: str, current_price: float = 0.0) -> ExecutionResult:
        return ExecutionResult(success=True)



class DemoBroker(BaseBroker):
    def __init__(self):
        logger.debug("DemoBroker inicializado (sin ejecución externa)")


class RealBroker(BaseBroker):
    def __init__(self):
        self._exchange = None
        self._init_exchange()

    def _init_exchange(self):
        try:
            api_key = config.exchange.api_key
            api_secret = config.exchange.api_secret
            if not api_key or not api_secret:
                logger.error("RealBroker: KRAKEN_API_KEY/SECRET vacíos")
                self._exchange = None
                return
            exchange_class = getattr(ccxt, "kraken", None)
            if exchange_class is None:
                logger.error("RealBroker: ccxt.kraken no disponible")
                self._exchange = None
                return
            self._exchange = exchange_class(
                {
                    "apiKey": api_key,
                    "secret": api_secret,
                    "enableRateLimit": True,
                }
            )
            logger.info("RealBroker inicializado (ccxt.kraken)")
        except Exception as e:
            logger.exception(f"RealBroker init error: {e}")
            self._exchange = None

    @property
    def exchange(self):
        return self._exchange

    async def _ensure(self) -> bool:
        return bool(self._exchange)

    async def cancel_all_orders(self, pair: str) -> bool:
        if not await self._ensure():
            logger.warning(f"[REAL-BROKER] cancel_all_orders({pair}): exchange no inicializado")
            return False
        try:
            symbol = await self.get_symbol(pair)
            await self._exchange.cancel_all_orders(symbol)
            return True
        except Exception as e:
            logger.error(f"[REAL-BROKER] cancel_all_orders error: {e}")
            return False

    async def cancel_orders_for_pair(self, pair: str) -> bool:
        return await self.cancel_all_orders(pair)

    async def fetch_open_orders(self, pair: str) -> List[Dict[str, Any]]:
        if not await self._ensure():
            return []
        try:
            symbol = await self.get_symbol(pair)
            orders = await self._exchange.fetch_open_orders(symbol)
            return orders if orders else []
        except Exception as e:
            logger.error(f"[REAL-BROKER] fetch_open_orders error: {e}")
            return []

    async def fetch_positions(self, pair: Optional[str] = None) -> List[Dict[str, Any]]:
        if not await self._ensure():
            return []
        try:
            if hasattr(self._exchange, "fetch_positions"):
                positions = await self._exchange.fetch_positions(pair)
                return positions if positions else []
            return []
        except Exception as e:
            logger.error(f"[REAL-BROKER] fetch_positions error: {e}")
            return []

    async def fetch_balance(self) -> Dict[str, Any]:
        if not await self._ensure():
            return {}
        try:
            balance = await self._exchange.fetch_balance()
            return balance if balance else {}
        except Exception as e:
            logger.error(f"[REAL-BROKER] fetch_balance error: {e}")
            return {}

    async def fetch_ticker(self, pair: str) -> Dict[str, Any]:
        if not await self._ensure():
            return {}
        try:
            symbol = await self.get_symbol(pair)
            ticker = await self._exchange.fetch_ticker(symbol)
            return ticker if ticker else {}
        except Exception as e:
            logger.error(f"[REAL-BROKER] fetch_ticker error: {e}")
            return {}



def build_broker():
    mode = (config.trading.mode or "demo").lower()
    if mode == "real":
        return RealBroker()
    return DemoBroker()
