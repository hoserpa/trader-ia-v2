"""Estrategia de grid trading para capturar volatilidad lateral.

Opera independientemente del ML: coloca órdenes limit en niveles equidistantes.
Cada fill genera la orden opuesta en el nivel adyacente, capturando el spread.

Soporta modo ATR-adaptive donde el rango y spacing se ajustan automaticamente
a la volatilidad actual del mercado.
"""
import json
from datetime import datetime, timezone
from typing import Optional
from loguru import logger
from config import config
from trading.portfolio import Portfolio

REDIS_GRID_STATE_KEY = "grid:state:{pair}"
REDIS_GRID_GLOBAL_KEY = "grid:global"

MAX_FILLED_HISTORY = 50


class GridStrategy:
    """Grid trading con soporte ATR-adaptive."""

    def __init__(self, redis_client, portfolio: Portfolio):
        self.redis = redis_client
        self.portfolio = portfolio
        self._running = False
        self._state: dict[str, dict] = {}
        self._global_state: dict = {}

    async def start(self):
        """Inicia grid en todos los pares configurados."""
        await self.load_state()

        has_levels = any(self._state.get(p, {}).get("levels") for p in config.grid.pairs)
        if self._running and self._state and has_levels:
            logger.info("Grid: estado recuperado desde Redis, manteniendo niveles existentes")
            await self._save_global_state()
            return

        self._running = True
        self._global_state = {
            "enabled": True,
            "pairs": config.grid.pairs,
            "total_pnl_eur": 0.0,
            "total_grid_trades": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        port_state = self.portfolio.get()
        total_balance = port_state.get(
            "total_value_eur",
            port_state.get("balance_eur", config.trading.demo_initial_balance),
        )
        total_grid_capital = total_balance * config.grid.capital_pct
        capital_per_pair = total_grid_capital / max(len(config.grid.pairs), 1)

        for pair in config.grid.pairs:
            price = await self._get_price(pair)
            if not price:
                logger.warning(f"Grid: no hay precio para {pair}, saltando")
                continue
            await self._init_pair_grid(pair, price, capital_per_pair)
            levels = len(self._state[pair]["levels"])
            logger.info(
                f"Grid iniciado {pair}: {levels} niveles, "
                f"centro={price:.2f}e, capital={capital_per_pair:.2f}e"
            )

        await self._save_global_state()
        logger.info(
            f"Grid activo en {len(self._state)} pares, capital total={total_grid_capital:.2f}e"
        )

    async def stop(self):
        """Detiene todos los grids."""
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
        total_balance = port_state.get(
            "total_value_eur",
            port_state.get("balance_eur", config.trading.demo_initial_balance),
        )
        capital_per_pair = (
            total_balance * config.grid.capital_pct / max(len(config.grid.pairs), 1)
        )

        for pair in config.grid.pairs:
            needs_init = pair not in self._state or not self._state[pair].get("levels")
            if needs_init:
                price = await self._get_price(pair)
                if price:
                    await self._init_pair_grid(pair, price, capital_per_pair)
                    logger.info(
                        f"Grid {pair}: inicializado (tardío o levels vacíos) @ {price:.2f}e"
                    )
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
                    self._cleanup_filled_levels(pair)
                    await self._save_pair_state(pair)

                await self._check_rebalance(pair, current_price)

            except Exception as e:
                logger.error(f"Grid error en {pair}: {e}")

        await self._check_global_stop_loss()

    async def _get_price(self, pair: str) -> Optional[float]:
        """Obtiene precio actual desde Redis con check de staleness."""
        raw = await self.redis.get(f"price:{pair}")
        if not raw:
            return None

        try:
            price = float(raw)
        except (ValueError, TypeError):
            return None

        if price <= 0:
            return None

        price_key = f"price_ts:{pair}"
        ts_raw = await self.redis.get(price_key)
        if ts_raw:
            try:
                ts = float(ts_raw)
                age = datetime.now(timezone.utc).timestamp() - ts
                if age > config.grid.price_stale_sec:
                    logger.warning(
                        f"Grid {pair}: precio stale ({age:.0f}s > {config.grid.price_stale_sec}s), saltando"
                    )
                    return None
            except (ValueError, TypeError):
                pass

        return price

    def _get_atr(self, pair: str) -> Optional[float]:
        """Obtiene ATR actual desde cache del engine si esta disponible."""
        try:
            from indicators.technical import calculate_indicators, get_atr
            cache_key = pair
            if hasattr(self, "_atr_cache") and cache_key in self._atr_cache:
                return self._atr_cache[cache_key]
        except ImportError:
            pass
        return None

    def set_atr_cache(self, pair: str, atr: float):
        """Cache de ATR actualizado por el engine."""
        if not hasattr(self, "_atr_cache"):
            self._atr_cache = {}
        self._atr_cache[pair] = atr

    async def _init_pair_grid(
        self, pair: str, current_price: float, capital_per_pair: float
    ):
        """Calcula niveles iniciales. ATR-adaptive si esta habilitado."""
        grid_cfg = config.grid

        if grid_cfg.atr_adaptive and current_price > 0:
            atr = self._get_atr(pair)
            if atr and atr > 0:
                range_amount = atr * grid_cfg.atr_range_mult
                spacing = atr / grid_cfg.atr_spacing_divisor
                lower = current_price - range_amount
                upper = current_price + range_amount
                n_levels = max(
                    4, int((upper - lower) / max(spacing, 0.01)) + 1
                )
                n_levels = min(n_levels, grid_cfg.max_levels)
                spacing = (upper - lower) / max(n_levels - 1, 1)
                logger.info(
                    f"Grid {pair}: ATR-adaptive range={range_amount:.2f}, "
                    f"spacing={spacing:.2f}, levels={n_levels}"
                )
            else:
                range_amount = current_price * grid_cfg.range_pct
                lower = current_price - range_amount
                upper = current_price + range_amount
                n_levels = grid_cfg.levels_per_pair
                spacing = (upper - lower) / max(n_levels - 1, 1)
        else:
            range_amount = current_price * grid_cfg.range_pct
            lower = current_price - range_amount
            upper = current_price + range_amount
            n_levels = grid_cfg.levels_per_pair
            spacing = (upper - lower) / max(n_levels - 1, 1)

        level_value_eur = capital_per_pair / n_levels

        levels = []
        for i in range(n_levels):
            level_price = lower + (i * spacing)
            side = "buy" if level_price < current_price else "sell"
            amount = (level_value_eur * grid_cfg.leverage) / level_price
            levels.append(
                {
                    "id": i,
                    "price": round(level_price, 8),
                    "side": side,
                    "amount": round(amount, 8),
                    "value_eur": round(level_value_eur, 2),
                    "entry_price": round(level_price, 8),
                    "status": "open",
                    "filled_at": None,
                    "filled_price": None,
                }
            )

        self._state[pair] = {
            "pair": pair,
            "current_price": current_price,
            "center_price": current_price,
            "lower": round(lower, 8),
            "upper": round(upper, 8),
            "spacing": round(spacing, 8),
            "leverage": grid_cfg.leverage,
            "levels": levels,
            "pnl_eur": 0.0,
            "pnl_pct": 0.0,
            "fees_eur": 0.0,
            "total_grid_trades": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        await self._save_pair_state(pair)

    async def _handle_fill(self, pair: str, level: dict, fill_price: float):
        """Marca orden como llena, coloca counter-order y acredita PnL neto de comisiones."""
        level["status"] = "filled"
        level["filled_at"] = datetime.now(timezone.utc).isoformat()
        level["filled_price"] = fill_price

        spacing = self._state[pair]["spacing"]
        entry_price = level.get("entry_price", level["price"])
        amount = level["amount"]
        fee_rate = config.exchange.maker_fee
        fee_eur = fill_price * amount * fee_rate
        pnl = 0.0

        if level["side"] == "buy":
            sell_price = level["price"] + spacing
            pnl = (entry_price - fill_price) * amount - fee_eur
            new_level = {
                "id": f"{level['id']}_sell_{len(self._state[pair]['levels'])}",
                "price": round(sell_price, 8),
                "side": "sell",
                "amount": amount,
                "value_eur": round(amount * sell_price, 2),
                "entry_price": round(fill_price, 8),
                "status": "open",
                "filled_at": None,
                "filled_price": None,
            }
            self._state[pair]["levels"].append(new_level)
            logger.info(
                f"Grid {pair}: BUY {entry_price:.2f}e -> SELL {sell_price:.2f}e "
                f"(fill={fill_price:.2f}, PnL={pnl:.4f}e, fee={fee_eur:.4f}e)"
            )
        else:
            buy_price = level["price"] - spacing
            pnl = (fill_price - entry_price) * amount - fee_eur
            new_level = {
                "id": f"{level['id']}_buy_{len(self._state[pair]['levels'])}",
                "price": round(buy_price, 8),
                "side": "buy",
                "amount": amount,
                "value_eur": round(amount * buy_price, 2),
                "entry_price": round(fill_price, 8),
                "status": "open",
                "filled_at": None,
                "filled_price": None,
            }
            self._state[pair]["levels"].append(new_level)
            logger.info(
                f"Grid {pair}: SELL {entry_price:.2f}e -> BUY {buy_price:.2f}e "
                f"(fill={fill_price:.2f}, PnL={pnl:.4f}e, fee={fee_eur:.4f}e)"
            )

        self._state[pair]["pnl_eur"] += pnl
        self._state[pair]["fees_eur"] = self._state[pair].get("fees_eur", 0) + fee_eur
        self._state[pair]["total_grid_trades"] += 1

        total_capital_used = max(
            sum(l["value_eur"] for l in self._state[pair]["levels"] if l["status"] == "open"),
            1,
        )
        self._state[pair]["pnl_pct"] = (
            self._state[pair]["pnl_eur"] / total_capital_used * 100
        )

        self._global_state["total_pnl_eur"] = (
            self._global_state.get("total_pnl_eur", 0) + pnl
        )
        self._global_state["total_fees_eur"] = (
            self._global_state.get("total_fees_eur", 0) + fee_eur
        )
        self._global_state["total_grid_trades"] = (
            self._global_state.get("total_grid_trades", 0) + 1
        )
        await self._save_global_state()

    def _cleanup_filled_levels(self, pair: str):
        """Elimina filled levels antiguos para evitar crecimiento indefinido."""
        levels = self._state[pair]["levels"]
        filled = [l for l in levels if l["status"] == "filled"]
        if len(filled) > MAX_FILLED_HISTORY:
            open_levels = [l for l in levels if l["status"] == "open"]
            recent_filled = filled[-MAX_FILLED_HISTORY:]
            self._state[pair]["levels"] = open_levels + recent_filled

    async def _check_rebalance(self, pair: str, current_price: float):
        """Recentra el grid si el precio se desvió del centro."""
        center = self._state[pair]["center_price"]
        deviation = abs(current_price - center) / center if center > 0 else 0
        threshold = config.grid.rebalance_threshold

        if deviation > threshold:
            logger.info(
                f"Grid {pair}: precio desviado {deviation:.1%} > {threshold:.0%}, recalculando..."
            )
            pnl = self._state[pair]["pnl_eur"]
            fees = self._state[pair].get("fees_eur", 0)
            trades_count = self._state[pair]["total_grid_trades"]
            port_state = self.portfolio.get()
            total_balance = port_state.get(
                "total_value_eur",
                port_state.get("balance_eur", config.trading.demo_initial_balance),
            )
            capital_per_pair = (
                total_balance * config.grid.capital_pct
                / max(len(config.grid.pairs), 1)
            )

            await self._init_pair_grid(pair, current_price, capital_per_pair)

            self._state[pair]["pnl_eur"] = pnl
            self._state[pair]["fees_eur"] = fees
            self._state[pair]["total_grid_trades"] = trades_count
            total_capital_used = max(
                sum(
                    l["value_eur"]
                    for l in self._state[pair]["levels"]
                    if l["status"] == "open"
                ),
                1,
            )
            self._state[pair]["pnl_pct"] = pnl / total_capital_used * 100

            await self._save_pair_state(pair)
            logger.info(
                f"Grid {pair}: recalculado. Nuevo centro: {current_price:.2f}e"
            )

    async def _check_global_stop_loss(self):
        """Stop loss global del grid."""
        total_pnl = self._global_state.get("total_pnl_eur", 0)
        port_state = self.portfolio.get()
        total_balance = port_state.get(
            "total_value_eur",
            port_state.get("balance_eur", config.trading.demo_initial_balance),
        )
        total_invested = total_balance * config.grid.capital_pct
        if total_invested > 0:
            pnl_pct = total_pnl / total_invested
            if pnl_pct < -config.grid.stop_loss_pct:
                logger.warning(
                    f"Grid SL activado: PnL={pnl_pct:.1%} < -{config.grid.stop_loss_pct:.0%}"
                )
                await self.stop()

    async def _save_pair_state(self, pair: str):
        await self.redis.set(
            REDIS_GRID_STATE_KEY.format(pair=pair),
            json.dumps(self._state[pair], default=str),
        )

    async def _save_global_state(self):
        await self.redis.set(
            REDIS_GRID_GLOBAL_KEY, json.dumps(self._global_state, default=str)
        )

    async def load_state(self):
        """Recupera estado desde Redis (util tras reinicio del contenedor)."""
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
                "spacing_pct": round(
                    state["spacing"] / max(state["center_price"], 0.001) * 100, 3
                ),
                "leverage": state["leverage"],
                "open_orders": len(open_orders),
                "filled_orders": len(filled_orders),
                "pnl_eur": round(state["pnl_eur"], 4),
                "pnl_pct": round(state["pnl_pct"], 2),
                "fees_eur": round(state.get("fees_eur", 0), 4),
                "total_grid_trades": state["total_grid_trades"],
            }

        return {
            "enabled": config.grid.enabled,
            "running": self._running,
            "simulated": True,
            "pairs": pairs,
            "total_pnl_eur": round(
                self._global_state.get("total_pnl_eur", 0), 4
            ),
            "total_fees_eur": round(
                self._global_state.get("total_fees_eur", 0), 4
            ),
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
                "atr_adaptive": config.grid.atr_adaptive,
                "taker_fee": config.exchange.taker_fee,
                "maker_fee": config.exchange.maker_fee,
            },
        }
