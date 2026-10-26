"""Notificador abstracto para el grid."""
from typing import Protocol


class Notifier(Protocol):
    async def notify_warning(self, message: str, warn_key: str = "") -> None:
        ...

    async def notify_grid_cycle(self, *args, **kwargs) -> None:
        ...


class NoopNotifier:
    async def notify_warning(self, message: str, warn_key: str = "") -> None:
        return None

    async def notify_grid_cycle(self, *args, **kwargs) -> None:
        return None