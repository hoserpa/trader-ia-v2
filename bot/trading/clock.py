"""Reloj inyectable para el grid (produccion vs backtest)."""
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SimClock:
    def __init__(self, start):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta):
        self._now = self._now + delta

    def set(self, value):
        self._now = value