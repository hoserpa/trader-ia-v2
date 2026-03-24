"""Estado y cálculos del portafolio."""
import json
from datetime import datetime
import redis
from loguru import logger
from config import config

REDIS_PORTFOLIO_KEY = "portfolio:state"


class Portfolio:
    """Gestiona el estado en memoria del portafolio (sincronizado con Redis)."""

    def __init__(self, redis_client: redis.Redis, initial_balance: float = None):
        self.redis = redis_client
        self._state = self._load_or_init(initial_balance or config.trading.demo_initial_balance)

    def _load_or_init(self, initial_balance: float) -> dict:
        raw = self.redis.get(REDIS_PORTFOLIO_KEY)
        if raw:
            return json.loads(raw)
        state = {
            "balance_eur": initial_balance,
            "initial_balance_eur": initial_balance,
            "positions": {},
            "total_value_eur": initial_balance,
            "total_pnl_eur": 0.0,
            "total_pnl_pct": 0.0,
            "created_at": datetime.utcnow().isoformat(),
        }
        self._save(state)
        return state

    def _save(self, state: dict) -> None:
        self.redis.set(REDIS_PORTFOLIO_KEY, json.dumps(state))
        self._state = state

    def get(self) -> dict:
        return self._state.copy()

    def update_balance(self, delta_eur: float) -> None:
        self._state["balance_eur"] = round(self._state["balance_eur"] + delta_eur, 4)
        self._save(self._state)

    def add_position(self, pair: str, position_data: dict) -> None:
        self._state["positions"][pair] = position_data
        self._save(self._state)

    def remove_position(self, pair: str) -> None:
        self._state["positions"].pop(pair, None)
        self._save(self._state)

    def update_valuations(self, current_prices: dict) -> dict:
        """Recalcula el valor total del portafolio con precios actuales."""
        crypto_value = 0.0
        for pair, pos in self._state["positions"].items():
            price = current_prices.get(pair, pos.get("entry_price", 0))
            pos["current_price"] = price
            pos["current_value_eur"] = pos["amount_crypto"] * price
            pos["pnl_eur"] = pos["current_value_eur"] - pos["amount_eur_invested"]
            pos["pnl_pct"] = pos["pnl_eur"] / pos["amount_eur_invested"] * 100 if pos["amount_eur_invested"] > 0 else 0
            crypto_value += pos["current_value_eur"]

        total = self._state["balance_eur"] + crypto_value
        initial = self._state["initial_balance_eur"]
        self._state["total_value_eur"] = round(total, 4)
        self._state["total_pnl_eur"] = round(total - initial, 4)
        self._state["total_pnl_pct"] = round((total - initial) / initial * 100, 4) if initial > 0 else 0
        self._save(self._state)
        return self._state
