"""Estrategia de grid trading para capturar volatilidad lateral.

Opera independientemente del ML: coloca órdenes limit en niveles equidistantes.
Cada fill genera la orden opuesta en el nivel adyacente, capturando el spread.

Soporta modo ATR-adaptive donde el rango y spacing se ajustan automaticamente
a la volatilidad actual del mercado.
"""
import json
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4
from loguru import logger
from config import config
from trading.portfolio import Portfolio
from notifications.telegram import _format_duration_between

REDIS_GRID_STATE_KEY = "grid:state:{pair}"
REDIS_GRID_GLOBAL_KEY = "grid:global"

MAX_FILLED_HISTORY = 50


class GridStrategy:
    """Grid trading con soporte ATR-adaptive."""

    def __init__(self, redis_client, portfolio: Portfolio, broker=None, telegram=None):
        self.redis = redis_client
        self.portfolio = portfolio
        self.broker = broker
        self.telegram = telegram
        self._running = False
        self._state: dict[str, dict] = {}
        self._global_state: dict = {}
        self._margin_support: dict = {}

    def set_margin_support(self, support: dict) -> None:
        self._margin_support = support or {}
        if self.broker:
            self.broker.set_margin_support(self._margin_support)

    def _pair_short_ok(self, pair: str) -> bool:
        """Un par puede abrir shorts solo si la API real permite margen/corto en el.

        En demo se permite simular shorts donde la API real los soporte (short_ok);
        en real se exige ademas allow_short + margin_enabled."""
        if self.broker is not None:
            return self.broker.has_short_support(pair)
        if not config.trading.is_demo() and not (config.exchange.allow_short and config.exchange.margin_enabled):
            return False
        info = self._margin_support.get(pair, {})
        if info.get("in_markets") is False:
            return False
        return bool(info.get("short_ok"))

    def _short_disabled_reason(self, pair: str) -> str:
        """Devuelve por que no se abren shorts en este par (para logs fieles)."""
        if not config.trading.is_demo():
            if not config.exchange.allow_short:
                return "EXCHANGE_ALLOW_SHORT=false en config"
            if not config.exchange.margin_enabled:
                return "EXCHANGE_MARGIN_ENABLED=false en config"
        info = self._margin_support.get(pair, {})
        if info.get("short_ok"):
            return "mercado admite short pero broker lo descarta"
        return "la API real no permite margen/corto en este par"

    @property
    def margin_supported(self) -> dict:
        return self._margin_support

    async def _retrofit_short_unavailable(self) -> None:
        """Elimina de pares ya inicializados (restaurados desde Redis) los niveles
        SELL que abririan un short, cuando el par real no permite margen/corto.

        Mantiene la paridad demo/real: si un par no soporta shorts en la API real,
        el grid (en cualquier modo) no mantiene niveles que abririan un corto.
        """
        changed = False
        for pair, state in self._state.items():
            if not self._pair_short_ok(pair):
                keep = [
                    l for l in state.get("levels", [])
                    if l.get("status") == "filled" or l.get("side") != "sell"
                ]
                if len(keep) != len(state.get("levels", [])):
                    state["levels"] = keep
                    changed = True
        if changed:
            logger.info("Grid: removidos niveles SELL en pares long-only")
            for pair in self._state:
                await self._save_pair_state(pair)

    async def start(self):
        """Inicia grid en todos los pares configurados.

        Restaura el estado (niveles e histórico de PnL) desde Redis siempre que
        exista, independientemente de si el grid quedó 'enabled'. Reconciliar el
        PnL total con el balance real acreditado al portfolio evita que, tras un
        reinicio del contenedor, el dashboard muestre un PnL del grid incoherente
        con el saldo acumulado.
        """
        await self.load_state()

        await self._retrofit_short_unavailable()

        has_global = bool(self._global_state) and bool(self._global_state.get("started_at"))
        has_levels = any(self._state.get(p, {}).get("levels") for p in config.grid.pairs)

        if not (has_global and has_levels):
            logger.info("Grid: sin estado histórico, inicializando niveles desde cero")
            self._global_state = {
                "enabled": True,
                "pairs": config.grid.pairs,
                "total_pnl_eur": 0.0,
                "total_grid_trades": 0,
                "total_fees_eur": 0.0,
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
                if self._state.get(pair, {}).get("levels"):
                    continue
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
        else:
            logger.info("Grid: estado recuperado desde Redis, manteniendo niveles e histórico")

        self._reconcile_with_portfolio()
        await self._restore_open_positions()
        self._running = True
        await self._save_global_state()
        logger.info(f"Grid activo en {len(self._state)} pares")

    def _reconcile_with_portfolio(self):
        """Sincroniza el PnL total reportado por el grid con el balance real del
        portfolio (fuente de verdad de lo efectivamente acreditado).

        El balance acumula el PnL neto de cada fill de forma persistente, mientras
        que el acumulado del grid podía perderse en reinicios. Al alinear el total
        del grid con (balance - capital inicial), el dashboard deja de mostrar dos
        cifras de PnL contradictorias. Los fills posteriores incrementan este total
        de forma coherente con el balance.
        """
        port = self.portfolio.get()
        initial = port.get("initial_balance_eur", port.get("balance_eur", 0))
        real_pnl = round(port.get("balance_eur", 0) - initial, 4)
        self._global_state["total_pnl_eur"] = real_pnl
        self._global_state["real_pnl_eur"] = real_pnl

    async def _restore_open_positions(self):
        """Rehidrata en portfolio.positions las posiciones abiertas tras un reinicio.

        Fuente de verdad: la BD SQLite (tabla Trade), persistente e independiente del
        estado volátil del grid en Redis. Una operacion abierta es aquella agrupada por
        ciclo que aun no tiene contra-orden de cierre (status 'open').
        """
        try:
            from database.crud import get_operations
            from database.init_db import SessionLocal
            with SessionLocal() as db:
                ops = get_operations(db, limit=500)
        except Exception as e:
            logger.warning(f"Grid: no se pudieron restaurar posiciones abiertas: {e}")
            return

        for op in ops:
            if op.get("status") != "open":
                continue
            pair = op["pair"]
            position_type = "long" if op["side"] == "BUY" else "short"
            await self._open_portfolio_position(
                pair, op["entry_price"], op["amount_crypto"], position_type
            )
            logger.info(
                f"Grid {pair}: posicion abierta restaurada "
                f"({position_type} @ {op['entry_price']:.2f}e)"
            )

    async def stop(self):
        """Pausa todos los grids conservando su estado (niveles e histórico).

        No borra los niveles ni el PnL: estos se persisten intactos en Redis para que,
        al reiniciar el contenedor, el grid continue donde quedo en lugar de
        re-inicializarse desde cero y reabrir posiciones.
        """
        self._running = False
        if self._state:
            for pair in list(self._state.keys()):
                await self._save_pair_state(pair)
        self._global_state["enabled"] = False
        await self._save_global_state()
        logger.info("Grid pausado (niveles conservados para reanudar)")

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

        level_value_eur = max(
            capital_per_pair / n_levels, grid_cfg.min_lot_value_eur
        )

        short_ok = self._pair_short_ok(pair)
        levels = []
        for i in range(n_levels):
            level_price = lower + (i * spacing)
            side = "buy" if level_price < current_price else "sell"
            if side == "sell" and not short_ok:
                logger.info(
                    f"Grid {pair}: nivel SELL {level_price:.2f} omitido (long-only, "
                    f"{self._short_disabled_reason(pair)})"
                )
                continue
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

    async def _open_portfolio_position(self, pair: str, entry_price: float, amount: float, position_type: str):
        """Registra una posicion abierta del grid en el portafolio para el dashboard.

        El grid cierra por cruce de nivel (no por SL/TP), por eso stop_loss/take_profit
        se guardan como None para que el frontend los muestre como '—'.
        """
        await self.portfolio.add_position(pair, {
            "position_type": position_type,
            "entry_price": round(entry_price, 8),
            "amount_crypto": round(amount, 8),
            "amount_eur_invested": round(entry_price * amount, 4),
            "current_price": round(entry_price, 8),
            "pnl_eur": 0.0,
            "pnl_pct": 0.0,
            "stop_loss_price": None,
            "take_profit_price": None,
        })

    async def _handle_fill(self, pair: str, level: dict, fill_price: float):
        """Marca orden como llena, coloca counter-order y acredita PnL neto de comisiones.

        La pierna de apertura (id entero) solo debita su comision (-fee), ya que no hay
        PnL realizado todavia. La pierna de cierre (id string) acredita el PnL realizado
        del ciclo usando el precio real de apertura (entry_price), evitando el doble
        conteo de la mejora de precio (slippage favorable). Las comisiones no realizadas
        ni los beneficios por slippage se inflan el balance.
        """
        level["status"] = "filled"
        level["filled_at"] = datetime.now(timezone.utc).isoformat()
        level["filled_price"] = fill_price

        if not level.get("cycle_id"):
            level["cycle_id"] = str(uuid4())

        spacing = self._state[pair]["spacing"]
        entry_price = level.get("entry_price", level["price"])
        amount = level["amount"]
        fee_rate = config.exchange.maker_fee
        fee_eur = fill_price * amount * fee_rate
        level["fee_eur"] = fee_eur
        pnl = 0.0
        counter_price = None

        if level["side"] == "buy":
            sell_price = level["price"] + spacing
            counter_price = sell_price
            is_close = isinstance(level.get("id"), str)
            pnl = (
                (entry_price - fill_price) * amount - fee_eur
                if is_close
                else -fee_eur
            )
            new_level = {
                "id": f"{level['id']}_sell_{len(self._state[pair]['levels'])}",
                "price": round(sell_price, 8),
                "side": "sell",
                "amount": amount,
                "value_eur": round(amount * sell_price, 2),
                "entry_price": round(fill_price, 8),
                "cycle_id": level["cycle_id"],
                "status": "open",
                "filled_at": None,
                "filled_price": None,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "fee_eur_opening": fee_eur,
            }
            self._state[pair]["levels"].append(new_level)
            logger.info(
                f"Grid {pair}: BUY {entry_price:.2f}e -> SELL {sell_price:.2f}e "
                f"(fill={fill_price:.2f}, PnL={pnl:.4f}e, fee={fee_eur:.4f}e)"
            )
            if not is_close:
                await self._open_portfolio_position(pair, fill_price, amount, "long")
            else:
                await self.portfolio.remove_position(pair)
        else:
            buy_price = level["price"] - spacing
            counter_price = buy_price
            is_close = isinstance(level.get("id"), str)
            pnl = (
                (fill_price - entry_price) * amount - fee_eur
                if is_close
                else -fee_eur
            )
            new_level = {
                "id": f"{level['id']}_buy_{len(self._state[pair]['levels'])}",
                "price": round(buy_price, 8),
                "side": "buy",
                "amount": amount,
                "value_eur": round(amount * buy_price, 2),
                "entry_price": round(fill_price, 8),
                "cycle_id": level["cycle_id"],
                "status": "open",
                "filled_at": None,
                "filled_price": None,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "fee_eur_opening": fee_eur,
            }
            self._state[pair]["levels"].append(new_level)
            logger.info(
                f"Grid {pair}: SELL {entry_price:.2f}e -> BUY {buy_price:.2f}e "
                f"(fill={fill_price:.2f}, PnL={pnl:.4f}e, fee={fee_eur:.4f}e)"
            )
            if not is_close:
                await self._open_portfolio_position(pair, fill_price, amount, "short")
            else:
                await self.portfolio.remove_position(pair)

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

        await self._save_pair_state(pair)
        await self._persist_grid_fill(pair, level, fill_price, pnl, fee_eur)

        if self.telegram:
            is_cycle_close = isinstance(level.get("id"), str)
            if is_cycle_close:
                fees_total = fee_eur + level.get("fee_eur_opening", 0)
                duration = _format_duration_between(
                    level.get("opened_at", ""), level.get("filled_at", "")
                )
                op_side = "buy" if level["side"] == "sell" else "sell"
                await self.telegram.notify_grid_cycle(
                    pair, op_side, level.get("entry_price"), fill_price,
                    pnl, fees_total, duration,
                )

    async def _persist_grid_fill(self, pair: str, level: dict, fill_price: float, pnl: float, fee_eur: float):
        """Registra cada fill en la BD como trade y acredita el PnL neto al portfolio.

        Mantiene el balance_total del portfolio sincronizado con el PnL neto del grid
        (spread - comisiones). Publica portfolio_update por WebSocket para el dashboard.
        """
        try:
            intraday = self.portfolio.get()
            await self.portfolio.update_balance(pnl)
            port = self.portfolio.get()

            initial = port.get("initial_balance_eur", port.get("balance_eur", 0))
            new_total = round(port["balance_eur"], 4)
            port["total_value_eur"] = new_total
            port["total_pnl_eur"] = round(new_total - initial, 4)
            port["total_pnl_pct"] = (
                round((new_total - initial) / initial * 100, 4) if initial > 0 else 0
            )
            await self.portfolio._save(port)

            from database.crud import create_trade
            from database.init_db import SessionLocal
            with SessionLocal() as db:
                create_trade(db, {
                    "pair": pair,
                    "side": level["side"].upper(),
                    "amount_crypto": level["amount"],
                    "amount_eur": round(level["amount"] * fill_price, 4),
                    "price": round(fill_price, 8),
                    "fee_eur": round(fee_eur, 4),
                    "pnl_eur": round(pnl, 4) if isinstance(level.get("id"), str) else None,
                    "mode": config.trading.mode,
                    "cycle_id": level.get("cycle_id"),
                })

            await self.redis.publish(
                "bot:live_updates",
                json.dumps({
                    "type": "portfolio_update",
                    "data": {
                        "balance_eur": port["balance_eur"],
                        "initial_balance_eur": port.get("initial_balance_eur", initial),
                        "positions": port.get("positions", {}),
                        "total_value_eur": port["total_value_eur"],
                        "total_pnl_eur": port["total_pnl_eur"],
                        "total_pnl_pct": port["total_pnl_pct"],
                        "grid_pnl": round(self._global_state.get("total_pnl_eur", 0), 4),
                    },
                }),
            )
        except Exception as e:
            logger.warning(f"Grid {pair}: no se persistió fill: {e}")

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
                try:
                    parsed = json.loads(raw)
                except Exception:
                    parsed = None
                if parsed:
                    self._state[pair] = parsed

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
                "short_supported": self._pair_short_ok(pair),
            }

        return {
            "enabled": config.grid.enabled,
            "running": self._running,
            "simulated": True,
            "execution_mode": getattr(self.broker, "mode", "demo") if self.broker else "demo",
            "margin_enabled": config.exchange.margin_enabled,
            "allow_short": config.exchange.allow_short,
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
                "min_lot_value_eur": config.grid.min_lot_value_eur,
                "capital_pct": config.grid.capital_pct,
                "range_pct": config.grid.range_pct,
                "rebalance_threshold": config.grid.rebalance_threshold,
                "stop_loss_pct": config.grid.stop_loss_pct,
                "poll_interval": config.grid.poll_interval,
                "atr_adaptive": config.grid.atr_adaptive,
                "taker_fee": config.exchange.taker_fee,
                "maker_fee": config.exchange.maker_fee,
                "margin_enabled": config.exchange.margin_enabled,
                "margin_mode": config.exchange.margin_mode,
                "margin_leverage": config.exchange.margin_leverage,
                "allow_short": config.exchange.allow_short,
            },
        }
