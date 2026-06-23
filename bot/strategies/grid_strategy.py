"""Estrategia de grid trading en futuros Binance para capturar volatilidad lateral.

Opera independientemente del ML: coloca órdenes limit en niveles equidistantes.
Cada fill genera la orden opuesta en el nivel adyacente, capturando el spread.
"""
import json
from datetime import datetime
from typing import Optional
from loguru import logger
from config import config
from trading.portfolio import Portfolio

REDIS_GRID_STATE_KEY = "grid:state:{pair}"
REDIS_GRID_GLOBAL_KEY = "grid:global"


class GridStrategy:
    """Grid trading en futuros (2-3x leverage)."""

    def __init__(self, redis_client, portfolio: Portfolio):
        self.redis = redis_client
        self.portfolio = portfolio
        self._running = False
        self._state: dict[str, dict] = {}
        self._global_state: dict = {}

    async def start(self):
        """Inicia grid en todos los pares configurados."""
        self._running = True
        self._global_state = {
            "enabled": True,
            "pairs": config.grid.pairs,
            "total_pnl_eur": 0.0,
            "total_grid_trades": 0,
            "started_at": datetime.utcnow().isoformat() + "Z",
        }

        port_state = self.portfolio.get()
        total_balance = port_state.get("total_value_eur", port_state.get("balance_eur", config.trading.demo_initial_balance))
        total_grid_capital = total_balance * config.grid.capital_pct
        capital_per_pair = total_grid_capital / max(len(config.grid.pairs), 1)

        for pair in config.grid.pairs:
            price = await self._get_price(pair)
            if not price:
                logger.warning(f"Grid: no hay precio para {pair}, saltando")
                continue
            await self._init_pair_grid(pair, price, capital_per_pair)
            levels = len(self._state[pair]["levels"])
            logger.info(f"Grid iniciado {pair}: {levels} niveles, centro={price:.2f}€, capital={capital_per_pair:.2f}€")

        await self._save_global_state()
        logger.info(f"Grid activo en {len(config.grid.pairs)} pares, capital total={total_grid_capital:.2f}€")

    async def stop(self):
        """Detiene todos los grids y cancela órdenes."""
        self._running = False
        for pair in list(self._state.keys()):
            self._state[pair]["levels"] = []
            await self._save_pair_state(pair)
        self._global_state["enabled"] = False
        await self._save_global_state()
        logger.info("Grid detenido")

    async def check_orders(self):
        """Verifica fills, recoloca órdenes e inicializa pares pendientes."""
        if not self._running or not config.grid.enabled:
            return

        port_state = self.portfolio.get()
        total_balance = port_state.get("total_value_eur", port_state.get("balance_eur", config.trading.demo_initial_balance))
        capital_per_pair = total_balance * config.grid.capital_pct / max(len(config.grid.pairs), 1)

        for pair in config.grid.pairs:
            if pair not in self._state:
                price = await self._get_price(pair)
                if price:
                    await self._init_pair_grid(pair, price, capital_per_pair)
                    logger.info(f"Grid {pair}: inicializado tardíamente @ {price:.2f}€")
                continue

            try:
                current_price = await self._get_price(pair)
                if not current_price:
                    continue

                self._state[pair]["current_price"] = current_price
                levels = self._state[pair]["levels"]
                filled_any = False

                for level in levels:
                    if level["status"] != "open":
                        continue
                    if level["side"] == "buy" and current_price <= level["price"]:
                        await self._handle_fill(pair, level, current_price)
                        filled_any = True
                    elif level["side"] == "sell" and current_price >= level["price"]:
                        await self._handle_fill(pair, level, current_price)
                        filled_any = True

                if filled_any:
                    await self._save_pair_state(pair)

                await self._check_rebalance(pair, current_price)

            except Exception as e:
                logger.error(f"Grid error en {pair}: {e}")

        await self._check_global_stop_loss()

    async def _get_price(self, pair: str) -> Optional[float]:
        """Obtiene precio actual desde Redis."""
        raw = await self.redis.get(f"price:{pair}")
        return float(raw) if raw else None

    async def _init_pair_grid(self, pair: str, current_price: float, capital_per_pair: float):
        """Calcula niveles iniciales y los coloca en el grid."""
        range_amount = current_price * config.grid.range_pct
        lower = current_price - range_amount
        upper = current_price + range_amount
        n_levels = config.grid.levels_per_pair
        spacing = (upper - lower) / max(n_levels - 1, 1)
        level_value_eur = capital_per_pair / n_levels

        levels = []
        for i in range(n_levels):
            level_price = lower + (i * spacing)
            side = "buy" if level_price < current_price else "sell"
            amount = (level_value_eur * config.grid.leverage) / level_price
            levels.append({
                "id": i,
                "price": round(level_price, 8),
                "side": side,
                "amount": round(amount, 8),
                "value_eur": round(level_value_eur, 2),
                "order_id": f"{pair}_{i}",
                "status": "open",
                "filled_at": None,
                "filled_price": None,
            })

        self._state[pair] = {
            "pair": pair,
            "current_price": current_price,
            "center_price": current_price,
            "lower": round(lower, 8),
            "upper": round(upper, 8),
            "spacing": round(spacing, 8),
            "leverage": config.grid.leverage,
            "levels": levels,
            "pnl_eur": 0.0,
            "pnl_pct": 0.0,
            "total_grid_trades": 0,
            "funding_paid": 0.0,
            "started_at": datetime.utcnow().isoformat() + "Z",
        }

        await self._save_pair_state(pair)

    async def _handle_fill(self, pair: str, level: dict, fill_price: float):
        """Marca orden como llena y coloca la opuesta en el nivel siguiente."""
        level["status"] = "filled"
        level["filled_at"] = datetime.utcnow().isoformat() + "Z"
        level["filled_price"] = fill_price

        spacing = self._state[pair]["spacing"]
        pnl = 0.0

        if level["side"] == "buy":
            sell_price = level["price"] + spacing
            new_level = {
                "id": f"{level['id']}_sell_{len(self._state[pair]['levels'])}",
                "price": round(sell_price, 8),
                "side": "sell",
                "amount": level["amount"],
                "value_eur": round(level["amount"] * sell_price, 2),
                "order_id": f"{pair}_sell_{datetime.utcnow().timestamp()}",
                "status": "open",
                "filled_at": None,
                "filled_price": None,
            }
            self._state[pair]["levels"].append(new_level)
            pnl = (sell_price - level["price"]) * level["amount"]
            logger.info(f"Grid {pair}: BUY {level['price']:.2f}€ → SELL {sell_price:.2f}€ (PnL={pnl:.4f}€)")
        else:
            buy_price = level["price"] - spacing
            new_level = {
                "id": f"{level['id']}_buy_{len(self._state[pair]['levels'])}",
                "price": round(buy_price, 8),
                "side": "buy",
                "amount": level["amount"],
                "value_eur": round(level["amount"] * buy_price, 2),
                "order_id": f"{pair}_buy_{datetime.utcnow().timestamp()}",
                "status": "open",
                "filled_at": None,
                "filled_price": None,
            }
            self._state[pair]["levels"].append(new_level)
            pnl = (level["price"] - buy_price) * level["amount"]
            logger.info(f"Grid {pair}: SELL {level['price']:.2f}€ → BUY {buy_price:.2f}€ (PnL={pnl:.4f}€)")

        self._state[pair]["pnl_eur"] += pnl
        self._state[pair]["total_grid_trades"] += 1
        total_capital_used = sum(l["value_eur"] for l in self._state[pair]["levels"] if l["status"] == "open")
        total_capital_used = max(total_capital_used, 1)
        self._state[pair]["pnl_pct"] = self._state[pair]["pnl_eur"] / total_capital_used * 100

        self._global_state["total_pnl_eur"] = self._global_state.get("total_pnl_eur", 0) + pnl
        self._global_state["total_grid_trades"] = self._global_state.get("total_grid_trades", 0) + 1
        await self._save_global_state()

    async def _check_rebalance(self, pair: str, current_price: float):
        """Recentra el grid si el precio se desvió del centro más del umbral."""
        center = self._state[pair]["center_price"]
        deviation = abs(current_price - center) / center if center > 0 else 0
        threshold = config.grid.rebalance_threshold

        if deviation > threshold:
            logger.info(f"Grid {pair}: precio desviado {deviation:.1%} > {threshold:.0%}, recalculando...")
            pnl = self._state[pair]["pnl_eur"]
            trades_count = self._state[pair]["total_grid_trades"]
            port_state = self.portfolio.get()
            total_balance = port_state.get("total_value_eur", port_state.get("balance_eur", config.trading.demo_initial_balance))
            capital_per_pair = total_balance * config.grid.capital_pct / max(len(config.grid.pairs), 1)

            await self._init_pair_grid(pair, current_price, capital_per_pair)

            self._state[pair]["pnl_eur"] = pnl
            self._state[pair]["total_grid_trades"] = trades_count
            logger.info(f"Grid {pair}: recalculado. Nuevo centro: {current_price:.2f}€")

    async def _check_global_stop_loss(self):
        """Stop loss global del grid."""
        total_pnl = self._global_state.get("total_pnl_eur", 0)
        port_state = self.portfolio.get()
        total_balance = port_state.get("total_value_eur", port_state.get("balance_eur", config.trading.demo_initial_balance))
        total_invested = total_balance * config.grid.capital_pct
        if total_invested > 0:
            pnl_pct = total_pnl / total_invested
            if pnl_pct < -config.grid.stop_loss_pct:
                logger.warning(f"Grid SL activado: PnL={pnl_pct:.1%} < -{config.grid.stop_loss_pct:.0%}")
                await self.stop()

    async def _save_pair_state(self, pair: str):
        await self.redis.set(REDIS_GRID_STATE_KEY.format(pair=pair), json.dumps(self._state[pair], default=str))

    async def _save_global_state(self):
        await self.redis.set(REDIS_GRID_GLOBAL_KEY, json.dumps(self._global_state, default=str))

    async def load_state(self):
        """Recupera estado desde Redis (útil tras reinicio del contenedor)."""
        for pair in config.grid.pairs:
            raw = await self.redis.get(REDIS_GRID_STATE_KEY.format(pair=pair))
            if raw:
                self._state[pair] = json.loads(raw)

        raw = await self.redis.get(REDIS_GRID_GLOBAL_KEY)
        if raw:
            self._global_state = json.loads(raw)
            self._running = self._global_state.get("enabled", False)

    def get_state(self) -> dict:
        """Estado completo del grid para API/frontend."""
        pairs = {}
        for pair, state in self._state.items():
            open_orders = [l for l in state["levels"] if l["status"] == "open"]
            filled_orders = [l for l in state["levels"] if l["status"] == "filled"]
            pairs[pair] = {
                "current_price": state.get("current_price", 0),
                "center_price": state["center_price"],
                "range_lower": state["lower"],
                "range_upper": state["upper"],
                "spacing_pct": round(state["spacing"] / max(state["center_price"], 0.001) * 100, 3),
                "leverage": state["leverage"],
                "open_orders": len(open_orders),
                "filled_orders": len(filled_orders),
                "pnl_eur": round(state["pnl_eur"], 2),
                "pnl_pct": round(state["pnl_pct"], 2),
                "total_grid_trades": state["total_grid_trades"],
                "funding_paid": state.get("funding_paid", 0),
            }

        return {
            "enabled": config.grid.enabled,
            "running": self._running,
            "pairs": pairs,
            "total_pnl_eur": round(self._global_state.get("total_pnl_eur", 0), 2),
            "total_grid_trades": self._global_state.get("total_grid_trades", 0),
            "started_at": self._global_state.get("started_at", ""),
            "config": {
                "leverage": config.grid.leverage,
                "levels_per_pair": config.grid.levels_per_pair,
                "capital_pct": config.grid.capital_pct,
                "range_pct": config.grid.range_pct,
                "rebalance_threshold": config.grid.rebalance_threshold,
                "stop_loss_pct": config.grid.stop_loss_pct,
                "poll_interval": config.grid.poll_interval,
            },
        }
