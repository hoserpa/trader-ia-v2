"""Interfaces abstractas para datos y ejecución.

MarketDataProvider: fuente de OHLCV diarios.
ExecutionProvider: envío de órdenes y consulta de estado.

Todas las implementaciones concretas deben implementar estos Protocol.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional, Protocol, runtime_checkable

import pandas as pd


# ---------------------------------------------------------------------------
# MarketDataProvider
# ---------------------------------------------------------------------------

@runtime_checkable
class MarketDataProvider(Protocol):
    """Provee datos OHLCV diarios de mercado."""

    def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """Retorna DataFrame con columnas: open, high, low, close, volume.
        Índice: DatetimeIndex (fechas). Fechas ordenadas, sin duplicados.
        """
        ...

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Retorna el precio de cierre más reciente disponible."""
        ...


# ---------------------------------------------------------------------------
# ExecutionProvider
# ---------------------------------------------------------------------------

class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    status: OrderStatus = OrderStatus.PENDING
    fill_price: Optional[float] = None
    fill_time: Optional[datetime] = None
    commission: float = 0.0
    slippage: float = 0.0
    reason: str = ""
    strategy: str = "trend_following_v1"


@dataclass
class Position:
    symbol: str
    quantity: float
    entry_price: float
    entry_time: datetime
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    stop_loss: float = 0.0
    trailing_stop: float = 0.0
    highest_close: float = 0.0


@dataclass
class Account:
    equity: float
    cash: float
    buying_power: float
    positions_value: float


@runtime_checkable
class ExecutionProvider(Protocol):
    """Provee ejecución de órdenes (paper o real)."""

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        price: float,
        reason: str = "",
    ) -> Order:
        """Envía una orden. Retorna Order con status actualizado."""
        ...

    def get_positions(self) -> list[Position]:
        """Retorna posiciones abiertas actuales."""
        ...

    def get_account(self) -> Account:
        """Retorna estado de la cuenta."""
        ...

    def get_position(self, symbol: str) -> Optional[Position]:
        """Retorna posición abierta para un símbolo, o None."""
        ...
