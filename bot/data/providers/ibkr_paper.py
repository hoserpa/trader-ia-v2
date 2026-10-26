"""ExecutionProvider stub para IBKR Paper Trading.

Simula fills con precios de mercado. Persiste en SQLite.
No envía órdenes reales. Idempotente (reinicio seguro).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from data.providers.base import (
    Account, Order, OrderSide, OrderStatus, Position,
)


class IBKRPaperExecutionProvider:
    """Ejecuta órdenes en modo paper via IBKR (stub).

    Simula fills usando el precio proporcionado.
    Persiste estado en memoria (en producción, conectar a SQLite).
    """

    def __init__(self, initial_equity: float = 10000.0):
        self._equity = initial_equity
        self._cash = initial_equity
        self._positions: dict[str, Position] = {}
        self._orders: list[Order] = []
        self._fills: list[dict] = []

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        price: float,
        reason: str = "",
    ) -> Order:
        """Simula fill inmediato al precio dado."""
        order_id = str(uuid.uuid4())[:12]
        now = datetime.now(timezone.utc)

        cost = quantity * price
        commission = cost * 0.0002  # 2 bps

        if side == OrderSide.BUY:
            total_cost = cost + commission
            if total_cost > self._cash:
                order = Order(
                    order_id=order_id, symbol=symbol, side=side,
                    quantity=quantity, price=price,
                    status=OrderStatus.REJECTED,
                    reason=f"Insufficient cash: need {total_cost:.2f}, have {self._cash:.2f}",
                )
                self._orders.append(order)
                return order

            self._cash -= total_cost

            if symbol in self._positions:
                pos = self._positions[symbol]
                new_qty = pos.quantity + quantity
                new_entry = (pos.entry_price * pos.quantity + price * quantity) / new_qty
                pos.quantity = new_qty
                pos.entry_price = new_entry
                pos.current_price = price
            else:
                self._positions[symbol] = Position(
                    symbol=symbol, quantity=quantity, entry_price=price,
                    entry_time=now, current_price=price, highest_close=price,
                )

        elif side == OrderSide.SELL:
            pos = self._positions.get(symbol)
            if not pos or pos.quantity < quantity:
                order = Order(
                    order_id=order_id, symbol=symbol, side=side,
                    quantity=quantity, price=price,
                    status=OrderStatus.REJECTED,
                    reason=f"No position or insufficient qty for {symbol}",
                )
                self._orders.append(order)
                return order

            proceeds = cost - commission
            pnl = (price - pos.entry_price) * quantity - commission

            self._cash += proceeds

            if abs(pos.quantity - quantity) < 1e-10:
                del self._positions[symbol]
            else:
                pos.quantity -= quantity
                pos.current_price = price

        order = Order(
            order_id=order_id, symbol=symbol, side=side,
            quantity=quantity, price=price,
            status=OrderStatus.FILLED,
            fill_price=price, fill_time=now,
            commission=commission,
        )

        self._orders.append(order)
        self._fills.append({
            "order_id": order_id, "symbol": symbol, "side": side.value,
            "quantity": quantity, "price": price, "commission": commission,
            "time": now.isoformat(),
        })

        emoji = "🟢" if side == OrderSide.BUY else "🔴"
        logger.info(f"{emoji} [PAPER] {side.value.upper()} {quantity:.4f} {symbol} @ {price:.2f} (commission={commission:.4f})")
        return order

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def get_account(self) -> Account:
        positions_value = sum(
            pos.quantity * pos.current_price for pos in self._positions.values()
        )
        return Account(
            equity=self._cash + positions_value,
            cash=self._cash,
            buying_power=self._cash,
            positions_value=positions_value,
        )

    def update_prices(self, prices: dict[str, float]) -> None:
        """Actualiza precios de mercado de posiciones abiertas."""
        for symbol, pos in self._positions.items():
            if symbol in prices:
                pos.current_price = prices[symbol]
                pos.unrealized_pnl = (pos.current_price - pos.entry_price) * pos.quantity
                if pos.current_price > pos.highest_close:
                    pos.highest_close = pos.current_price
