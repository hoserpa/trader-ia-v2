"""Estrategia de grid trading para capturar volatilidad lateral.

Coloca órdenes limit en niveles equidistantes (rango fijo configurado).
Cada fill genera la orden opuesta en el nivel adyacente, capturando el spread.
"""
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4
from loguru import logger
import asyncio
from config import config
from trading.accounting import (
    margin_short_open,
    margin_short_rollover,
    trade_ledger_delta,
    trade_margin_fee,
)
from trading.portfolio import Portfolio
from notifications.telegram import _format_duration_between
try:
    from trading.broker_abstraction import build_broker
except Exception:
    from trading.broker_abstraction import DemoBroker

    def build_broker():  # type: ignore
        return DemoBroker()


def _hours_between(start_ts: str, end_ts: str) -> float:
    """Horas transcurridas entre dos timestamps ISO (0 si no hay datos)."""
    if not start_ts or not end_ts:
        return 0.0
    try:
        start = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
        return max(0.0, (end - start).total_seconds() / 3600)
    except Exception:
        return 0.0


def _is_opening(level: dict) -> bool:
    """True si el fill de este nivel ABRE una posicion (y no la cierra).

    El campo explicito `is_opening` (bool) se escribe al crear cada nivel y
    alterna estrictamente en cada hop (apertura -> cierre -> apertura ...).
    Para niveles legacy persistidos sin el campo (escritos por versiones
    anteriores del grid), la fase se deriva de la estructura del id: el nivel
    inicial es un entero (apertura) y cada hop anade "_side_n", asi que la
    profundidad de hop par == apertura. Nunca detectar por isinstance(id, str):
    todas las contra-ordenes heredan ids de tipo string, lo que rompia la
    alternancia y duplicaba el PnL en cada cruce de nivel.
    """
    field = level.get("is_opening")
    if field is not None:
        return bool(field)
    lid = level.get("id")
    if isinstance(lid, int):
        return True
    if isinstance(lid, str):
        depth = max(0, (len(str(lid).split("_")) - 1) // 2)
        return depth % 2 == 0
    return True


def _held_side(level: dict) -> Optional[str]:
    """'long'/'short' si el nivel es la contra-orden de cierre de una pierna
    ya ejecutada; None si es una apertura resting (no hay posición)."""
    if level.get("status") != "open" or _is_opening(level):
        return None
    return "long" if level["side"] == "sell" else "short"


REDIS_GRID_STATE_KEY = "grid:state:{pair}"
REDIS_GRID_GLOBAL_KEY = "grid:global"

MAX_FILLED_HISTORY = 50


class GridStrategy:
    """Grid trading de rango fijo (sin ATR-adaptive)."""

    def __init__(self, redis_client, portfolio: Portfolio, telegram=None, clock=None):
        from trading.clock import SystemClock

        self.redis = redis_client
        self.clock = clock or SystemClock()
        self.portfolio = portfolio
        self.telegram = telegram
        try:
            self.broker = build_broker()
        except Exception:
            try:
                from trading.broker_abstraction import DemoBroker

                self.broker = DemoBroker()
            except Exception:
                self.broker = None
        self._running = False
        self._state: dict[str, dict] = {}
        self._global_state: dict = {}
        self._margin_support: dict = {}
        self._last_valuation_ts = 0.0
        self._last_exposure_log: dict[str, float] = {}
        self._config_hash: Optional[str] = None

    @property
    def _fee_rate(self) -> float:
        """Comisión por pierna: maker en demo, taker en real (conservador)."""
        return config.exchange.taker_fee if not config.trading.is_demo() else config.exchange.maker_fee

    def set_margin_support(self, support: dict) -> None:
        self._margin_support = support or {}

    def _is_real(self) -> bool:
        """True en modo real: el grid ejecuta y reconcilia ordenes reales."""
        return (config.trading.mode or "demo").lower() == "real"

    @staticmethod
    def _is_short_leg(level: dict) -> bool:
        """True si la pierna usa margen (abre corto o lo recompra)."""
        if level["side"] == "sell":
            return _is_opening(level)
        return not _is_opening(level)

    def _pair_short_ok(self, pair: str) -> bool:
        """Un par puede abrir shorts solo si la API real permite margen/corto en él.

        En demo se permite simular shorts donde la API real los soporte (short_ok);
        en real se exige además allow_short + margin_enabled."""
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
            return "mercado admite short pero el grid lo descarta"
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
            for pair in list(self._state):
                await self._sync_broker_state(pair)
                await self._save_pair_state(pair)

    def _short_covers(self, pair: str) -> list:
        """Contra-ordenes de recompra abiertas: representan shorts vivas."""
        return [
            l for l in self._state[pair]["levels"]
            if l["side"] == "buy" and not _is_opening(l) and l["status"] == "open"
        ]

    def margin_health(self, current_prices: dict) -> dict:
        """Salud de margen de la cuenta (cross margin como Kraken).

        used_margin = Σ notional/L de las shorts abiertas; equity = balance
        realizado + no realizado de todas las piernas. margin_level = equity /
        used_margin × 100. Sin shorts abiertas, margin_level es None.
        """
        lev = max(config.exchange.margin_leverage, 1)
        used_margin = 0.0
        unrealized = 0.0
        for pair, st in self._state.items():
            px = current_prices.get(pair) or st.get("current_price") or st.get("center_price") or 0
            if not px:
                continue
            for lvl in st.get("levels", []):
                held = _held_side(lvl)
                if not held:
                    continue
                entry = lvl.get("entry_price") or lvl["price"]
                if held == "short":
                    used_margin += entry * lvl["amount"] / lev
                    unrealized += (entry - px) * lvl["amount"]
                else:
                    unrealized += (px - entry) * lvl["amount"]
        balance = 0.0
        try:
            balance = self.portfolio.get().get("balance_eur", 0.0)
        except Exception:
            pass
        equity = balance + unrealized
        level_pct = (equity / used_margin * 100.0) if used_margin > 0 else None
        return {
            "used_margin_eur": round(used_margin, 4),
            "equity_eur": round(equity, 4),
            "unrealized_eur": round(unrealized, 4),
            "margin_level_pct": round(level_pct, 2) if level_pct is not None else None,
        }

    def _margin_warned(self) -> bool:
        return bool(self._global_state.get("margin_call_warned", False))

    def _mark_margin_warned(self, warned: bool = True) -> None:
        self._global_state["margin_call_warned"] = warned

    async def _check_short_liquidation(self, pair: str, current_price: float) -> int:
        """Fiel a Kraken: si margin_level <= margin_stop (40%) liquida las shorts.

        El nivel de margen se mide sobre toda la cuenta; la liquidación (orden de
        mercado) cierra al precio actual las contra-ordenes BUY del par en poll,
        debitando el PnL realizado mas la comision de liquidacion (2-3%).
        """
        covers = self._short_covers(pair)
        if not covers:
            return 0
        health = self.margin_health({pair: current_price})
        level_pct = health["margin_level_pct"]
        if level_pct is None:
            return 0
        if level_pct > config.grid.margin_call_pct * 100:
            self._mark_margin_warned(False)
            return 0
        if level_pct > config.grid.margin_stop_pct * 100:
            if not self._margin_warned():
                self._mark_margin_warned()
                logger.warning(
                    f"MARGIN CALL: margin_level={level_pct:.1f}% <= "
                    f"{config.grid.margin_call_pct:.0%} (leverage "
                    f"{config.exchange.margin_leverage}x, used={health['used_margin_eur']:.2f}e) "
                    f"· shorts de {pair} en riesgo"
                )
            return 0
        logger.warning(
            f"LIQUIDACION de margen ({pair}): margin_level={level_pct:.1f}% <= "
            f"{config.grid.margin_stop_pct:.0%} (used={health['used_margin_eur']:.2f}e)"
        )
        if self._is_real() and getattr(self, "broker", None):
            await self.broker.cancel_orders_for_pair(pair)
        n = 0
        for lvl in list(covers):
            n += await self._liquidate_short(pair, lvl, current_price)
        self._mark_margin_warned(False)
        return n

    async def _liquidate_short(self, pair: str, lvl: dict, liq_price: float) -> int:
        """Cierra una short por liquidación de margen (market-like).

        En demo: cierre virtual al precio indicado.
        En real: orden de mercado real (buy to cover) y se usan sus fills/fees.
        """
        execution = None
        if self._is_real():
            if not getattr(self, "broker", None):
                logger.error(f"[REAL] _liquidate_short({pair}): broker no disponible")
                return 0
            execution = await self.broker.close_short_market(pair, lvl["amount"], liq_price)
            if not execution or not execution.success:
                logger.error(
                    f"[REAL] _liquidate_short({pair}): cierre rechazado: "
                    f"{getattr(execution, 'error', None)}"
                )
                return 0
            if execution.average_price:
                liq_price = execution.average_price
        fee_rate = self._fee_rate
        entry = lvl.get("entry_price") or lvl["price"]
        if execution is not None and getattr(execution, "fee_eur", 0):
            fee_eur = execution.fee_eur
        else:
            fee_eur = liq_price * lvl["amount"] * fee_rate
        liq_fee = round(entry * lvl["amount"] * config.grid.margin_liq_fee_pct, 4)
        pnl = (entry - liq_price) * lvl["amount"] - fee_eur - liq_fee
        close_level = {
            "id": f"{lvl['id']}_margincall",
            "is_opening": False,
            "side": "buy",
            "amount": lvl["amount"],
            "entry_price": entry,
            "cycle_id": lvl.get("cycle_id") or str(uuid4()),
            "fee_eur": fee_eur,
            "margin_rollover_eur": 0.0,
            "opened_by_trade_id": lvl.get("opened_by_trade_id"),
        }
        lvl["status"] = "filled"
        lvl["filled_at"] = self.clock.now().isoformat()
        lvl["filled_price"] = liq_price

        state = self._state[pair]
        state["pnl_eur"] = state.get("pnl_eur", 0) + pnl
        state["margin_fees_eur"] = state.get("margin_fees_eur", 0) + liq_fee
        state["total_grid_trades"] = state.get("total_grid_trades", 0) + 1
        self._global_state["total_pnl_eur"] = self._global_state.get("total_pnl_eur", 0) + pnl
        self._global_state["total_margin_fees_eur"] = (
            self._global_state.get("total_margin_fees_eur", 0) + liq_fee
        )
        await self._persist_grid_fill(
            pair,
            close_level,
            liq_price,
            pnl,
            fee_eur,
            reason="margin_call",
            exchange_order_id=getattr(execution, "order_id", None),
        )
        await self.portfolio.remove_position(pair)
        await self._sync_broker_state(pair)
        await self._save_pair_state(pair)
        logger.warning(
            f"Grid {pair}: short {round(entry, 2)}e liquidada @ {liq_price:.2f}e "
            f"({pnl:+.4f}e, liq_fee={liq_fee:.4f}e)"
        )
        return 1

    def _effective_config_dict(self) -> dict:
        return {
            "trading": {
                "mode": config.trading.mode,
                "pairs": config.trading.pairs,
                "base_currency": config.trading.base_currency,
                "demo_initial_balance": config.trading.demo_initial_balance,
            },
            "exchange": {
                "name": config.exchange.name,
                "taker_fee": config.exchange.taker_fee,
                "maker_fee": config.exchange.maker_fee,
                "margin_enabled": config.exchange.margin_enabled,
                "margin_mode": config.exchange.margin_mode,
                "margin_leverage": config.exchange.margin_leverage,
                "allow_short": config.exchange.allow_short,
            },
            "grid": {
                "enabled": config.grid.enabled,
                "pairs": config.grid.pairs,
                "leverage": config.grid.leverage,
                "levels_per_pair": config.grid.levels_per_pair,
                "min_lot_value_eur": config.grid.min_lot_value_eur,
                "capital_pct": config.grid.capital_pct,
                "range_pct": config.grid.range_pct,
                "rebalance_threshold": config.grid.rebalance_threshold,
                "stop_loss_pct": config.grid.stop_loss_pct,
                "poll_interval": config.grid.poll_interval,
                "max_exposure_mult": config.grid.max_exposure_mult,
            },
        }

    def _compute_config_hash(self) -> str:
        payload = json.dumps(self._effective_config_dict(), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _register_config_history(self, source: str = "startup") -> None:
        self._config_hash = self._compute_config_hash()
        try:
            from database.crud import create_config_history
            from database.init_db import SessionLocal

            from database.models import ConfigHistory

            def _write():
                with SessionLocal() as db:
                    last = db.query(ConfigHistory).order_by(ConfigHistory.ts.desc()).first()
                    if getattr(last, "config_hash", None) == self._config_hash:
                        return
                    create_config_history(
                        db,
                        {
                            "ts": self.clock.now(),
                            "config_hash": self._config_hash,
                            "config_json": json.dumps(self._effective_config_dict(), sort_keys=True, default=str),
                            "source": source,
                        },
                    )

            import asyncio as _asyncio

            await _asyncio.to_thread(_write)
        except Exception as e:
            logger.warning(f"Grid: no se pudo registrar config_history: {e}")

    async def start(self):
        """Inicia grid en todos los pares configurados.

        Restaura el estado (niveles e histórico de PnL) desde Redis siempre que
        exista, independientemente de si el grid quedó 'enabled'. Reconciliar el
        PnL total con el balance real acreditado al portfolio evita que, tras un
        reinicio del contenedor, el dashboard muestre un PnL del grid incoherente
        con el saldo acumulado.
        """
        await self.load_state()

        await self._register_config_history("startup")

        await self._retrofit_short_unavailable()

        await self._rebuild_balance_from_ledger()
        try:
            prices = {}
            for p in config.grid.pairs:
                prices[p] = self._state.get(p, {}).get("current_price") or await self._get_price(p)
            await self.portfolio.update_valuations(prices, self.open_positions_for_valuations())
        except Exception as e:
            logger.debug(f"No se pudo recalcular patrimonio tras rebuild del ledger: {e}")

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
                "started_at": self.clock.now().isoformat(),
            }

            capital_per_pair = self._capital_per_pair()

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
        await self._reconcile_pair_pnl()
        await self._restore_open_positions()
        if self._is_real():
            for pair in config.grid.pairs:
                if self._state.get(pair, {}).get("levels"):
                    await self._place_missing_orders(pair)
        self._global_state["enabled"] = True
        self._running = True
        await self._save_global_state()
        logger.info(f"Grid activo en {len(self._state)} pares")

    def _reconcile_with_portfolio(self):
        """Sincroniza el PnL total reportado por el grid con el patrimonio real del
        portfolio (fuente de verdad de lo efectivamente acreditado).

        El patrimonio (total_value_eur = caja + posiciones abiertas) acumula el
        PnL neto de cada fill de forma persistente, mientras que el acumulado del
        grid podía perderse en reinicios. Al alinear el total del grid con
        (patrimonio - capital inicial), el dashboard deja de mostrar dos cifras de
        PnL contradictorias. Los fills posteriores incrementan este total de forma
        coherente con el patrimonio.
        """
        port = self.portfolio.get()
        initial = port.get("initial_balance_eur", port.get("balance_eur", 0))
        equity = port.get("total_value_eur", port.get("balance_eur", 0))
        real_pnl = round(equity - initial, 4)
        self._global_state["total_pnl_eur"] = real_pnl
        self._global_state.pop("real_pnl_eur", None)

    async def _reconcile_pair_pnl(self):
        """Sincroniza el PnL por par en Redis con la suma real de la BD."""
        from database.init_db import SessionLocal
        from database.models import Trade

        def _load():
            res = {}
            db = SessionLocal()
            try:
                for pair in config.grid.pairs:
                    q = (
                        db.query(
                            Trade.side,
                            Trade.pnl_eur,
                            Trade.fee_eur,
                            Trade.margin_fee_eur,
                            Trade.amount_crypto,
                            Trade.price,
                        )
                        .filter(Trade.pair == pair)
                        .all()
                    )
                    pair_pnl = 0.0
                    for side, pnl, fee, margin_fee, amount, price in q:
                        pair_pnl += trade_ledger_delta(
                            side,
                            pnl,
                            fee,
                            margin_fee,
                            amount,
                            price,
                            config.grid.margin_open_fee_pct,
                        )
                    res[pair] = round(pair_pnl, 4)
                return res
            finally:
                db.close()

        pnls = await asyncio.to_thread(_load)
        for pair, pair_pnl in pnls.items():
            state = self._state.get(pair)
            if not state:
                continue
            state["pnl_eur"] = pair_pnl
            total_capital_used = max(
                sum(l["value_eur"] for l in state.get("levels", []) if l["status"] == "open"),
                1,
            )
            state["pnl_pct"] = pair_pnl / total_capital_used * 100
            await self._sync_broker_state(pair)
            await self._save_pair_state(pair)

    async def _restore_open_positions(self):
        try:
            from database.crud import get_operations
            from database.init_db import SessionLocal

            def _get():
                with SessionLocal() as db:
                    return get_operations(db, limit=500)

            ops = await asyncio.to_thread(_get)
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
                await self._sync_broker_state(pair)
                await self._save_pair_state(pair)
        self._global_state["enabled"] = False
        await self._save_global_state()
        logger.info("Grid pausado (niveles conservados para reanudar)")

    async def reset(self):
        """Reinicia el grid en caliente: limpia niveles, histórico y Redis, y
        reinicializa los niveles desde cero (ciclo limpio sin reiniciar el contenedor)."""
        for pair in list(self._state.keys()):
            await self.redis.delete(REDIS_GRID_STATE_KEY.format(pair=pair))
        await self.redis.delete(REDIS_GRID_GLOBAL_KEY)
        self._state = {}
        self._global_state = {
            "enabled": True,
            "pairs": config.grid.pairs,
            "total_pnl_eur": 0.0,
            "total_grid_trades": 0,
            "total_fees_eur": 0.0,
            "started_at": self.clock.now().isoformat(),
        }
        self._running = True

        capital_per_pair = self._capital_per_pair()
        for pair in config.grid.pairs:
            price = await self._get_price(pair)
            if not price:
                continue
            await self._init_pair_grid(pair, price, capital_per_pair)
        await self._save_global_state()
        logger.info(f"Grid reiniciado en caliente: {len(self._state)} pares desde cero")

    def _trend_allows_open(self, pair: str, side: str) -> bool:
        mode = getattr(config.grid, "trend_filter", "off")
        if mode == "off":
            return True
        history = self._state.get(pair, {}).get("price_history", [])
        if len(history) < 20:
            return True
        fast_n = 12
        slow_n = 48
        alpha_fast = 2 / (fast_n + 1)
        alpha_slow = 2 / (slow_n + 1)
        ema_fast = history[0]
        ema_slow = history[0]
        for price in history[1:]:
            ema_fast = alpha_fast * price + (1 - alpha_fast) * ema_fast
            ema_slow = alpha_slow * price + (1 - alpha_slow) * ema_slow
        if side == "buy":
            # bloquea compras (longs) en tendencia bajista
            return not (ema_fast < ema_slow)
        # bloquea ventas (shorts) en tendencia alcista
        return not (ema_fast > ema_slow)

    def _capital_per_pair(self) -> float:
        port = self.portfolio.get()
        base = port.get("total_value_eur", port.get("balance_eur", config.trading.demo_initial_balance))
        return base * config.grid.capital_pct / max(len(config.grid.pairs), 1)

    def _open_notional(self) -> float:
        total = 0.0
        for st in self._state.values():
            for lvl in st.get("levels", []):
                if _held_side(lvl):
                    total += lvl["amount"] * (lvl.get("entry_price") or lvl["price"])
        return total

    def _exposure_cap(self) -> float:
        mult = config.grid.max_exposure_mult
        if mult <= 0:
            return float("inf")
        port = self.portfolio.get()
        base = port.get("total_value_eur", port.get("balance_eur", 0))
        return base * config.grid.capital_pct * max(config.grid.leverage, 1) * mult

    async def check_orders(self):
        """Verifica fills, recoloca órdenes e inicializa pares pendientes."""
        if not self._running or not config.grid.enabled:
            return

        capital_per_pair = self._capital_per_pair()
        prices = {}

        for pair in config.grid.pairs:
            needs_init = pair not in self._state or not self._state[pair].get("levels")
            if needs_init:
                price = await self._get_price(pair)
                if price:
                    prices[pair] = price
                    await self._init_pair_grid(pair, price, capital_per_pair)
                    logger.info(
                        f"Grid {pair}: inicializado (tardío o levels vacíos) @ {price:.2f}e"
                    )
                continue

            try:
                current_price = await self._get_price(pair)
                if not current_price:
                    continue

                prices[pair] = current_price
                self._state[pair]["current_price"] = current_price
                history = self._state[pair].setdefault("price_history", [])
                history.append(current_price)
                if len(history) > 200:
                    del history[0]
                levels = self._state[pair]["levels"]

                if self._is_real():
                    filled_any = await self._check_real_fills(pair)
                    await self._place_missing_orders(pair)
                else:
                    filled_any = False
                    for level in levels:
                        if level["status"] != "open":
                            continue
                        triggers = (
                            (level["side"] == "buy" and current_price <= level["price"])
                            or (level["side"] == "sell" and current_price >= level["price"])
                        )
                        if not triggers:
                            continue
                        if _is_opening(level) and not self._trend_allows_open(pair, level["side"]):
                            continue
                        if _is_opening(level) and not self._is_real():
                            if (
                                self._open_notional()
                                + level["amount"] * current_price
                                > self._exposure_cap()
                            ):
                                now = time.monotonic()
                                if now - self._last_exposure_log.get(pair, 0.0) >= 60:
                                    logger.info(
                                        f"Grid {pair}: tope de exposicion alcanzado "
                                        f"({self._open_notional():.2f}e), orden resting"
                                    )
                                    self._last_exposure_log[pair] = now
                                continue
                        await self._handle_fill(pair, level, current_price)
                        filled_any = True

                if filled_any:
                    self._cleanup_filled_levels(pair)
                    await self._sync_broker_state(pair)
                    await self._save_pair_state(pair)

                await self._check_short_liquidation(pair, current_price)
                await self._check_rebalance(pair, current_price)

            except Exception as e:
                logger.error(f"Grid error en {pair}: {e}")

        now = time.monotonic()
        if prices and now - self._last_valuation_ts >= 30:
            try:
                await self.portfolio.update_valuations(
                    prices, self.open_positions_for_valuations()
                )
                self._last_valuation_ts = now
            except Exception as e:
                logger.debug(f"Grid: no se pudo valorar el patrimonio: {e}")

        await self._check_global_stop_loss()

    async def _get_price(self, pair: str) -> Optional[float]:
        """Obtiene precio actual desde Redis.

        La frescura se garantiza con el TTL de `price:{pair}` (60s en el
        collector): si la clave expira no hay precio y el grid salta el poll.
        """
        raw = await self.redis.get(f"price:{pair}")
        if not raw:
            return None

        try:
            price = float(raw)
        except (ValueError, TypeError):
            return None

        if price <= 0:
            return None

        return price


    async def _place_level_order(self, pair: str, level: dict):
        """Coloca en el exchange real la orden limit de un nivel (si aplica).

        Las piernas de corto (venta de apertura o recompra de cierre) se envian
        con margen/leverage; el resto son spot. Guarda el order_id en el nivel
        para reconciliar el fill por id (no por precio).
        """
        if not self._is_real() or not getattr(self, "broker", None):
            return
        margin = self._is_short_leg(level)
        try:
            if level["side"] == "buy":
                res = await self.broker.create_limit_buy(
                    pair, level["amount"], level["price"], margin=margin
                )
            else:
                res = await self.broker.create_limit_sell(
                    pair, level["amount"], level["price"], margin=margin
                )
        except Exception as e:
            logger.error(f"[REAL] error colocando orden {pair} {level['side']} @ {level['price']}: {e}")
            return
        if res.success and res.order_id:
            level["order_id"] = res.order_id
            level["place_attempts"] = 0
        else:
            level["place_attempts"] = int(level.get("place_attempts", 0)) + 1
            logger.error(
                f"[REAL] orden rechazada {pair} {level['side']} "
                f"{level['amount']} @ {level['price']}: {res.error}"
            )
            if level["place_attempts"] >= 5:
                level["status"] = "canceled"
                logger.warning(
                    f"Grid {pair}: nivel cancelado tras {level['place_attempts']} intentos fallidos"
                )
                if self.telegram:
                    try:
                        await self.telegram.notify_warning(
                            f"Nivel cancelado {pair} tras {level['place_attempts']} intentos",
                            warn_key=f"place_fail_{pair}",
                        )
                    except Exception:
                        pass

    async def _place_missing_orders(self, pair: str):
        """Coloca ordenes reales para los niveles abiertos aun sin order_id."""
        if not self._is_real():
            return
        for level in self._state.get(pair, {}).get("levels", []):
            if (
                level.get("status") == "open"
                and not level.get("order_id")
                and level.get("status") != "canceled"
                and int(level.get("place_attempts", 0)) < 5
            ):
                await self._place_level_order(pair, level)

    async def _sync_broker_state(self, pair: str):
        """Cancela en el exchange ordenes huerfanas (no ligadas a ningun nivel).

        En real, el grid coloca y cancela sus propias ordenes de forma explicita;
        esto solo evita ordenes colgadas que no correspondan a ningun nivel
        abierto (p.ej. tras quitar niveles en pares long-only).
        """
        if not self._is_real() or not getattr(self, "broker", None):
            return
        state = self._state.get(pair)
        if not state:
            return
        tracked = {
            str(lvl.get("order_id"))
            for lvl in state.get("levels", [])
            if lvl.get("status") == "open" and lvl.get("order_id")
        }
        try:
            open_orders = await self.broker.fetch_open_orders(pair)
        except Exception as e:
            logger.error(f"[REAL] sync fetch error {pair}: {e}")
            return
        for order in open_orders or []:
            order_id = order.get("id")
            if order_id and str(order_id) not in tracked:
                await self.broker.cancel_order(pair, str(order_id))

    async def _check_real_fills(self, pair: str) -> bool:
        """Detecta fills reales: un nivel cuyo order_id ya no esta abierto.

        Se confirma consultando la orden (estado/filled), de modo que un nivel
        solo se marca lleno cuando el exchange lo confirma, nunca por precio.
        """
        if not getattr(self, "broker", None):
            return False
        open_orders = await self.broker.fetch_open_orders(pair)
        open_ids = {str(o.get("id")) for o in open_orders or [] if o.get("id")}
        filled_any = False
        for level in list(self._state[pair]["levels"]):
            if level["status"] != "open" or level.get("filled_at"):
                continue
            order_id = level.get("order_id")
            if not order_id or str(order_id) in open_ids:
                continue
            res = await self.broker.fetch_order_result(pair, str(order_id))
            if res is None:
                continue
            amount = level["amount"]
            filled = float(res.filled_amount or 0.0)
            if res.status == "closed" or filled >= amount * 0.999:
                if 0 < filled < amount:
                    level["amount"] = filled
                fill_price = res.average_price or level["price"]
                await self._handle_fill(pair, level, fill_price, execution=res)
                filled_any = True
            elif res.status in ("canceled", "expired", "rejected"):
                if filled > 0:
                    level["amount"] = filled
                    fill_price = res.average_price or level["price"]
                    await self._handle_fill(pair, level, fill_price, execution=res)
                    filled_any = True
                else:
                    level["status"] = "canceled"
                    filled_any = True
        return filled_any

    async def _init_pair_grid(
        self, pair: str, current_price: float, capital_per_pair: float
    ):
        """Calcula niveles iniciales con rango fijo configurado."""
        grid_cfg = config.grid
        range_amount = current_price * grid_cfg.range_pct
        lower = current_price - range_amount
        upper = current_price + range_amount
        n_levels = grid_cfg.levels_per_pair
        spacing = (upper - lower) / max(n_levels - 1, 1)

        if getattr(grid_cfg, "lot_pct_of_equity", 0) and grid_cfg.lot_pct_of_equity > 0:
            base_lot = self.portfolio.get().get("total_value_eur", capital_per_pair) * grid_cfg.lot_pct_of_equity
        else:
            base_lot = capital_per_pair / n_levels
        level_value_eur = max(base_lot, grid_cfg.min_lot_value_eur)

        info = self._margin_support.get(pair, {})
        ordermin = info.get("ordermin")
        costmin = info.get("costmin")
        min_eur = (ordermin or 0) * current_price if ordermin else 0.0
        if costmin:
            min_eur = max(min_eur, costmin)
        if grid_cfg.min_lot_value_eur + 1e-9 < min_eur:
            logger.warning(
                f"Grid {pair}: GRID_MIN_LOT_VALUE_EUR={grid_cfg.min_lot_value_eur:.2f}e "
                f"< minimo real Kraken {min_eur:.2f}e (ordermin={ordermin}, "
                f"costmin={costmin}). En real Kraken rechazaria el lote; "
                f"sube GRID_MIN_LOT_VALUE_EUR a >= {min_eur:.1f}e."
            )

        short_ok = self._pair_short_ok(pair)
        levels = []
        for i in range(n_levels):
            # Media posicion de spacing de turno: con n impar ningún nivel cae
            # exactamente en current_price (antes el nivel central se rellenaba
            # en el primer check_orders con pnl = -fee: un ciclo fantasma).
            level_price = current_price + (i - (n_levels - 1) / 2 + 0.5) * spacing
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
                    "is_opening": True,
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

        if levels:
            lower = min(l["price"] for l in levels)
            upper = max(l["price"] for l in levels)

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
            "margin_fees_eur": 0.0,
            "total_grid_trades": 0,
            "started_at": self.clock.now().isoformat(),
        }

        await self._place_missing_orders(pair)
        await self._sync_broker_state(pair)
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

    async def _handle_fill(self, pair: str, level: dict, fill_price: float, execution=None):
        """Marca orden como llena, coloca counter-order y acredita PnL neto de comisiones.

        La pierna de apertura (id entero) solo debita su comision (-fee), ya que no hay
        PnL realizado todavia. La pierna de cierre (id string) acredita el PnL realizado
        del ciclo usando el precio real de apertura (entry_price), evitando el doble
        conteo de la mejora de precio (slippage favorable). Las comisiones no realizadas
        ni los beneficios por slippage se inflan el balance.

        En modo real, `execution` (ExecutionResult del broker) aporta el precio medio y
        la comision reales del fill, y la counter-order se envia al exchange.
        """
        if execution is not None and getattr(execution, "average_price", 0):
            fill_price = execution.average_price

        level["status"] = "filled"
        level["filled_at"] = self.clock.now().isoformat()
        level["filled_price"] = fill_price

        if not level.get("cycle_id"):
            level["cycle_id"] = str(uuid4())

        spacing = self._state[pair]["spacing"]
        entry_price = level.get("entry_price", level["price"])
        amount = level["amount"]
        fee_rate = self._fee_rate
        if execution is not None and getattr(execution, "fee_eur", 0):
            fee_eur = execution.fee_eur
        else:
            fee_eur = fill_price * amount * fee_rate
        level["fee_eur"] = fee_eur
        pnl = 0.0
        margin_cost_eur = 0.0
        counter_price = None

        if level["side"] == "buy":
            sell_price = level["price"] + spacing
            counter_price = sell_price
            is_close = not _is_opening(level)
            if is_close:
                margin_cost_eur = margin_short_rollover(
                    entry_price * amount,
                    _hours_between(level.get("opened_at"), level["filled_at"]),
                    config.grid.margin_rollover_pct,
                    config.grid.margin_rollover_hours,
                )
            pnl = (
                (entry_price - fill_price) * amount - fee_eur - margin_cost_eur
                if is_close
                else -fee_eur
            )
            new_level = {
                "id": f"{level['id']}_sell_{len(self._state[pair]['levels'])}",
                "is_opening": not _is_opening(level),
                "price": round(sell_price, 8),
                "side": "sell",
                "amount": amount,
                "value_eur": round(amount * sell_price, 2),
                "entry_price": round(fill_price, 8),
                "cycle_id": level["cycle_id"],
                "status": "open",
                "filled_at": None,
                "filled_price": None,
                "opened_at": self.clock.now().isoformat(),
                "fee_eur_opening": fee_eur,
            }
            self._state[pair]["levels"].append(new_level)
            await self._place_level_order(pair, new_level)
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
            is_close = not _is_opening(level)
            if not is_close:
                margin_cost_eur = margin_short_open(fill_price * amount, config.grid.margin_open_fee_pct)
            pnl = (
                (fill_price - entry_price) * amount - fee_eur
                if is_close
                else -fee_eur - margin_cost_eur
            )
            new_level = {
                "id": f"{level['id']}_buy_{len(self._state[pair]['levels'])}",
                "is_opening": not _is_opening(level),
                "price": round(buy_price, 8),
                "side": "buy",
                "amount": amount,
                "value_eur": round(amount * buy_price, 2),
                "entry_price": round(fill_price, 8),
                "cycle_id": level["cycle_id"],
                "status": "open",
                "filled_at": None,
                "filled_price": None,
                "opened_at": self.clock.now().isoformat(),
                "fee_eur_opening": fee_eur,
                "margin_open_eur": margin_cost_eur,
            }
            self._state[pair]["levels"].append(new_level)
            await self._place_level_order(pair, new_level)
            logger.info(
                f"Grid {pair}: SELL {entry_price:.2f}e -> BUY {buy_price:.2f}e "
                f"(fill={fill_price:.2f}, PnL={pnl:.4f}e, fee={fee_eur:.4f}e)"
            )
            if not is_close:
                await self._open_portfolio_position(pair, fill_price, amount, "short")
            else:
                await self.portfolio.remove_position(pair)

        level["margin_fees_eur"] = margin_cost_eur

        self._state[pair]["pnl_eur"] += pnl
        self._state[pair]["fees_eur"] = self._state[pair].get("fees_eur", 0) + fee_eur
        self._state[pair]["margin_fees_eur"] = self._state[pair].get("margin_fees_eur", 0) + margin_cost_eur
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
        self._global_state["total_margin_fees_eur"] = (
            self._global_state.get("total_margin_fees_eur", 0) + margin_cost_eur
        )
        self._global_state["total_grid_trades"] = (
            self._global_state.get("total_grid_trades", 0) + 1
        )
        await self._save_global_state()

        await self._sync_broker_state(pair)
        await self._save_pair_state(pair)
        order_id = execution.order_id if execution is not None else level.get("order_id")
        trade_id = await self._persist_grid_fill(
            pair, level, fill_price, pnl, fee_eur, exchange_order_id=order_id
        )
        if trade_id and _is_opening(level):
            new_level["opened_by_trade_id"] = trade_id
            await self._save_pair_state(pair)

        if self.telegram:
            is_cycle_close = not _is_opening(level)
            if is_cycle_close:
                fees_total = (
                    fee_eur + level.get("fee_eur_opening", 0)
                    + level.get("margin_open_eur", 0) + level.get("margin_fees_eur", 0)
                )
                duration = _format_duration_between(
                    level.get("opened_at", ""), level.get("filled_at", "")
                )
                op_side = "buy" if level["side"] == "sell" else "sell"
                await self.telegram.notify_grid_cycle(
                    pair, op_side, level.get("entry_price"), fill_price,
                    pnl, fees_total, duration,
                )

    async def _persist_grid_fill(
        self,
        pair: str,
        level: dict,
        fill_price: float,
        pnl: float,
        fee_eur: float,
        reason: str = "grid",
        exchange_order_id: Optional[str] = None,
    ) -> Optional[int]:
        """Registra cada fill en la BD como trade y acredita el PnL neto al portfolio.

        Mantiene el balance_total del portfolio sincronizado con el PnL neto del grid
        (spread - comisiones). Publica portfolio_update por WebSocket para el dashboard.
        `reason` distingue el origen del cierre: "grid" (ciclo normal) o "rebalance".
        Devuelve el id del trade persistido (None si falla la BD).
        """
        is_opening = _is_opening(level)
        payload = {
            "pair": pair,
            "side": level["side"].upper(),
            "amount_crypto": level["amount"],
            "amount_eur": round(level["amount"] * fill_price, 4),
            "price": round(fill_price, 8),
            "fee_eur": round(fee_eur, 4),
            "pnl_eur": round(pnl, 4) if not is_opening else None,
            "entry_price": (
                round(level["entry_price"], 8)
                if not is_opening and level.get("entry_price") is not None
                else None
            ),
            "mode": config.trading.mode,
            "cycle_id": level.get("cycle_id"),
            "reason": reason,
            "margin_fee_eur": (
                float(level.get("margin_fees_eur") or 0.0)
                if is_opening
                else 0.0
            ),
            "open_trade_id": level.get("opened_by_trade_id"),
            "exchange_order_id": exchange_order_id,
            "config_hash": self._config_hash,
        }

        trade_id = None
        db_ok = False
        last_exc = None
        for attempt in range(3):
            try:
                from database.crud import create_trade
                from database.init_db import SessionLocal

                def _write(p):
                    with SessionLocal() as db:
                        trade = create_trade(db, p)
                        return getattr(trade, "id", None)

                trade_id = await asyncio.to_thread(_write, payload)
                db_ok = True
                break
            except Exception as e:
                last_exc = e
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2 ** attempt))
        if not db_ok:
            logger.error(f"Grid {pair}: no se persistio fill tras 3 intentos: {last_exc}")
            try:
                import os

                db_dir = os.path.dirname(os.path.abspath(config.database.sqlite_path)) or "."
                failed_path = os.path.join(db_dir, "failed_trades.jsonl")
                with open(failed_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, default=str) + "\n")
            except Exception as dump_exc:
                logger.exception(f"Grid {pair}: no se pudo volcar failed_trades.jsonl: {dump_exc}")
            return None

        try:
            await self.portfolio.update_balance(pnl)
        except Exception as e:
            logger.exception(f"Grid {pair}: no se pudo actualizar el balance tras persistir fill: {e}")
        port = self.portfolio.get()

        initial = port.get("initial_balance_eur", port.get("balance_eur", 0))
        new_total = self._equity_eur(port)
        port["total_value_eur"] = round(new_total, 4)
        port["total_pnl_eur"] = round(new_total - initial, 4)
        port["total_pnl_pct"] = (
            round((new_total - initial) / initial * 100, 4) if initial > 0 else 0
        )
        if hasattr(self.portfolio, "_save"):
            try:
                await self.portfolio._save(port)
            except Exception as e:
                logger.exception(f"Grid {pair}: no se pudo guardar el portfolio tras persistir fill: {e}")
        try:
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
            logger.exception(f"Grid {pair}: no se pudo publicar portfolio_update: {e}")
        return trade_id

    def _equity_eur(self, port: dict) -> float:
        """Patrimonio = PnL realizado + no realizado de niveles abiertos (vs entrada).

        El balance (realizado) no incluye los niveles abiertos. Sumar el valor
        completo del nivel doblaría la cuenta (el balance nunca lo debitó), así
        que solo se suma la diferencia precio-actual − entrada: unrealized,
        que es lo que mide el drawdown del objetivo (≤2%).
        """
        value = port.get("balance_eur", 0.0)
        for pair, st in self._state.items():
            px = st.get("current_price") or st.get("center_price") or 0
            if not px:
                continue
            for lvl in st.get("levels", []):
                held = _held_side(lvl)
                if not held:
                    continue
                entry = lvl.get("entry_price") or lvl.get("price") or px
                delta = (px - entry) if held == "long" else (entry - px)
                value += lvl["amount"] * delta
        return value

    def open_positions_for_valuations(self) -> dict:
        """Contra-órdenes de cierre abiertas, para valorar el patrimonio.

        Una apertura resting (is_opening=True) no es una posición y no se
        valora. Solo cuenta la pierna ejecutada que espera su cierre.
        """
        result = {}
        for pair, st in self._state.items():
            held_levels = []
            for lvl in st.get("levels", []):
                held = _held_side(lvl)
                if not held:
                    continue
                held_levels.append({
                    "side": held,
                    "amount": lvl["amount"],
                    "entry_price": lvl.get("entry_price") or lvl.get("price"),
                })
            result[pair] = held_levels
        return result

    async def _rebuild_balance_from_ledger(self) -> None:
        port = self.portfolio.get()
        initial = port.get("initial_balance_eur", port.get("balance_eur", 0))
        from database.init_db import SessionLocal
        from database.models import Trade

        def _calc():
            db = SessionLocal()
            try:
                q = db.query(
                    Trade.side,
                    Trade.pnl_eur,
                    Trade.fee_eur,
                    Trade.margin_fee_eur,
                    Trade.amount_crypto,
                    Trade.price,
                ).all()
                balance = initial
                for side, pnl, fee, margin_fee, amount, price in q:
                    balance += trade_ledger_delta(
                        side,
                        pnl,
                        fee,
                        margin_fee,
                        amount,
                        price,
                        config.grid.margin_open_fee_pct,
                    )
                return round(balance, 4)
            finally:
                db.close()

        expected = await asyncio.to_thread(_calc)
        current = round(port.get("balance_eur", initial), 4)
        if abs(expected - current) > 0.0001:
            await self.portfolio.update_balance(expected - current)
            logger.info(
                f"Grid: balance reconciliado con el ledger: {current:.4f} -> {expected:.4f}"
            )

    def _cleanup_filled_levels(self, pair: str):
        """Elimina filled levels antiguos para evitar crecimiento indefinido."""
        levels = self._state[pair]["levels"]
        filled = [l for l in levels if l["status"] == "filled"]
        if len(filled) > MAX_FILLED_HISTORY:
            open_levels = [l for l in levels if l["status"] == "open"]
            recent_filled = filled[-MAX_FILLED_HISTORY:]
            self._state[pair]["levels"] = open_levels + recent_filled

    async def _real_liquidate_pair(self, pair: str, current_price: float) -> int:
        """Cierre real del par antes de recentrar: cancela resting y cierra a mercado.

        Solo las piernas de cierre (contra-ordenes ya ejecutadas de su apertura)
        representan posiciones vivas; las de apertura resting se cancelan sin PnL.
        Cada posicion se cierra por separado (no en neto) porque un mismo par puede
        tener simultaneamente una pata larga y una corta vivas.
        """
        state = self._state[pair]
        broker = getattr(self, "broker", None)

        if broker:
            await broker.cancel_orders_for_pair(pair)

        for level in state["levels"]:
            if (
                level["status"] == "open"
                and _is_opening(level)
                and not level.get("filled_at")
            ):
                level["status"] = "canceled"

        liquidated = []
        fully_closed = True
        for level in list(state["levels"]):
            if level["status"] != "open":
                continue
            entry = level.get("entry_price", level["price"])
            requested = level["amount"]
            side = level["side"]

            execution = None
            if broker:
                if side == "buy":
                    execution = await broker.close_short_market(pair, requested, current_price)
                else:
                    execution = await broker.close_long_market(pair, requested, current_price)
            if not execution or not execution.success:
                logger.error(
                    f"[REAL] no se pudo cerrar posicion {pair} {side} {requested}: "
                    f"{getattr(execution, 'error', None)}"
                )
                fully_closed = False
                continue

            filled = float(getattr(execution, "filled_amount", 0.0) or requested)
            if filled <= 0:
                filled = requested
            fill_price = execution.average_price or current_price
            fee_eur = execution.fee_eur or fill_price * filled * self._fee_rate
            margin_cost_eur = 0.0
            if side == "buy":
                margin_cost_eur = margin_short_rollover(
                    entry * filled,
                    _hours_between(
                        level.get("opened_at"), self.clock.now().isoformat()
                    ),
                    config.grid.margin_rollover_pct,
                    config.grid.margin_rollover_hours,
                )
                pnl = (entry - fill_price) * filled - fee_eur - margin_cost_eur
            else:
                pnl = (fill_price - entry) * filled - fee_eur

            close_level = {
                "id": f"{level.get('id')}_liquidated",
                "is_opening": False,
                "side": side,
                "amount": filled,
                "entry_price": entry,
                "cycle_id": level.get("cycle_id") or str(uuid4()),
                "fee_eur": fee_eur,
                "margin_rollover_eur": margin_cost_eur,
                "opened_by_trade_id": level.get("opened_by_trade_id"),
            }

            state["pnl_eur"] = state.get("pnl_eur", 0) + pnl
            state["fees_eur"] = state.get("fees_eur", 0) + fee_eur
            state["margin_fees_eur"] = state.get("margin_fees_eur", 0) + margin_cost_eur
            state["total_grid_trades"] = state.get("total_grid_trades", 0) + 1
            self._global_state["total_pnl_eur"] = (
                self._global_state.get("total_pnl_eur", 0) + pnl
            )
            self._global_state["total_fees_eur"] = (
                self._global_state.get("total_fees_eur", 0) + fee_eur
            )
            self._global_state["total_margin_fees_eur"] = (
                self._global_state.get("total_margin_fees_eur", 0) + margin_cost_eur
            )
            self._global_state["total_grid_trades"] = (
                self._global_state.get("total_grid_trades", 0) + 1
            )

            level["filled_price"] = fill_price
            await self._persist_grid_fill(
                pair,
                close_level,
                fill_price,
                pnl,
                fee_eur,
                reason="rebalance",
                exchange_order_id=level.get("order_id"),
            )
            liquidated.append((level.get("id"), side, round(pnl, 4)))

            if filled < requested - 1e-9:
                level["amount"] = round(requested - filled, 8)
                fully_closed = False
            else:
                level["status"] = "filled"
                level["filled_at"] = self.clock.now().isoformat()

        total_capital_used = max(
            sum(l["value_eur"] for l in state["levels"] if l["status"] == "open"),
            1,
        )
        state["pnl_pct"] = state["pnl_eur"] / total_capital_used * 100

        if fully_closed:
            await self.portfolio.remove_position(pair)
        await self._save_global_state()
        await self._sync_broker_state(pair)
        await self._save_pair_state(pair)

        if liquidated:
            logger.info(
                f"Grid {pair}: {len(liquidated)} posiciones cerradas a mercado "
                f"@ {current_price:.2f}e antes de recentrar: {liquidated}"
            )
        return len(liquidated)

    async def _liquidate_pair_positions(self, pair: str, current_price: float) -> int:
        """Liquida limpiamente las posiciones abiertas del par antes de recentrar.

        En modo demo: cierra virtualmente cada nivel abierto contra el precio actual
        acreditando su PnL neto y registrando el ciclo como cerrado.
        En modo real: cancela las ordenes resting del par y cierra cada posicion viva
        con orden de mercado, contabilizando los fills reales.
        """
        if self._is_real():
            return await self._real_liquidate_pair(pair, current_price)

        state = self._state[pair]
        # Un rebalanceo cierra a mercado: se contabiliza con taker_fee.
        fee_rate = config.exchange.taker_fee
        liquidated = []

        for level in state["levels"]:
            if level["status"] != "open":
                continue
            if _is_opening(level):
                # Orden de apertura resting: nunca se ejecutó, no hay posición.
                level["status"] = "canceled"
                continue
            if not level.get("cycle_id"):
                continue
            entry = level.get("entry_price", level["price"])
            amount = level["amount"]
            side = level["side"]
            fee_eur = current_price * amount * fee_rate

            if side == "buy":
                pnl = (entry - current_price) * amount - fee_eur
            else:
                pnl = (current_price - entry) * amount - fee_eur

            margin_cost_eur = 0.0
            if side == "buy":
                margin_cost_eur = margin_short_rollover(
                    entry * amount,
                    _hours_between(
                        level.get("opened_at"), self.clock.now().isoformat()
                    ),
                    config.grid.margin_rollover_pct,
                    config.grid.margin_rollover_hours,
                )
                pnl -= margin_cost_eur

            close_level = {
                "id": f"{level.get('id')}_liquidated",
                "is_opening": False,
                "side": side,
                "amount": amount,
                "entry_price": entry,
                "cycle_id": level.get("cycle_id") or str(uuid4()),
                "fee_eur": fee_eur,
                "margin_rollover_eur": margin_cost_eur,
                "opened_by_trade_id": level.get("opened_by_trade_id"),
            }

            state["pnl_eur"] = state.get("pnl_eur", 0) + pnl
            state["fees_eur"] = state.get("fees_eur", 0) + fee_eur
            state["margin_fees_eur"] = state.get("margin_fees_eur", 0) + margin_cost_eur
            state["total_grid_trades"] = state.get("total_grid_trades", 0) + 1
            self._global_state["total_pnl_eur"] = (
                self._global_state.get("total_pnl_eur", 0) + pnl
            )
            self._global_state["total_fees_eur"] = (
                self._global_state.get("total_fees_eur", 0) + fee_eur
            )
            self._global_state["total_margin_fees_eur"] = (
                self._global_state.get("total_margin_fees_eur", 0) + margin_cost_eur
            )
            self._global_state["total_grid_trades"] = (
                self._global_state.get("total_grid_trades", 0) + 1
            )

            level["status"] = "filled"
            level["filled_at"] = self.clock.now().isoformat()
            level["filled_price"] = current_price
            await self._persist_grid_fill(
                pair,
                close_level,
                current_price,
                pnl,
                fee_eur,
                reason="rebalance",
                exchange_order_id=level.get("order_id"),
            )
            liquidated.append(
                (level.get("id"), side, round(pnl, 4))
            )

        total_capital_used = max(
            sum(l["value_eur"] for l in state["levels"] if l["status"] == "open"),
            1,
        )
        state["pnl_pct"] = state["pnl_eur"] / total_capital_used * 100

        await self.portfolio.remove_position(pair)
        await self._save_global_state()
        await self._sync_broker_state(pair)
        await self._save_pair_state(pair)

        if liquidated:
            logger.info(
                f"Grid {pair}: {len(liquidated)} posiciones liquidadas "
                f"@ {current_price:.2f}e antes de recentrar: {liquidated}"
            )
        return len(liquidated)

    async def _check_rebalance(self, pair: str, current_price: float):
        """Recentra el grid si el precio se desvió del centro."""
        center = self._state[pair]["center_price"]
        deviation = abs(current_price - center) / center if center > 0 else 0
        threshold = config.grid.rebalance_threshold

        if deviation > threshold:
            logger.info(
                f"Grid {pair}: precio desviado {deviation:.1%} > {threshold:.0%}, liquidando y recalculando..."
            )
            pnl_before = self._state[pair]["pnl_eur"]
            fees_before = self._state[pair].get("fees_eur", 0.0)
            resting_before = sum(
                1 for lvl in self._state[pair]["levels"]
                if lvl.get("status") == "open" and _is_opening(lvl)
            )
            center_old = center
            if config.grid.rebalance_mode == "shift":
                held_levels = [
                    lvl for lvl in self._state[pair]["levels"]
                    if _held_side(lvl) is not None and lvl.get("status") == "open"
                ]
                resting_before = sum(
                    1 for lvl in self._state[pair]["levels"]
                    if lvl.get("status") == "open" and _is_opening(lvl)
                )
                for lvl in self._state[pair]["levels"]:
                    if lvl.get("status") == "open" and _is_opening(lvl):
                        lvl["status"] = "canceled"
                pnl_before = self._state[pair]["pnl_eur"]
                fees_before = self._state[pair].get("fees_eur", 0.0)
                n_liquidated = 0
                pnl_saved = self._state[pair]["pnl_eur"]
                fees_saved = self._state[pair].get("fees_eur", 0.0)
                trades_saved = self._state[pair]["total_grid_trades"]
                capital_per_pair = self._capital_per_pair()
                await self._init_pair_grid(pair, current_price, capital_per_pair)
                self._state[pair]["pnl_eur"] = pnl_saved
                self._state[pair]["fees_eur"] = fees_saved
                self._state[pair]["total_grid_trades"] = trades_saved
                self._state[pair]["levels"].extend(held_levels)
                pnl_after = self._state[pair].get("pnl_eur", 0.0)
                fees_after = self._state[pair].get("fees_eur", 0.0)
                resting_after = 0
            else:
                n_liquidated = await self._liquidate_pair_positions(pair, current_price)
                pnl_after = self._state[pair]["pnl_eur"]
                fees_after = self._state[pair].get("fees_eur", 0.0)
                resting_after = sum(
                    1 for lvl in self._state[pair]["levels"]
                    if lvl.get("status") == "open" and _is_opening(lvl)
                )
            try:
                from database.crud import create_rebalance_event
                from database.init_db import SessionLocal

                def _write():
                    with SessionLocal() as db:
                        create_rebalance_event(
                            db,
                            {
                                "ts": self.clock.now(),
                                "pair": pair,
                                "price": round(current_price, 8),
                                "center_old": round(center_old, 8) if center_old is not None else None,
                                "center_new": round(current_price, 8),
                                "deviation_pct": round(deviation * 100, 4),
                                "positions_closed": int(n_liquidated),
                                "resting_cancelled": max(0, resting_before - resting_after),
                                "realized_pnl_eur": round(pnl_after - pnl_before, 4),
                                "fees_eur": round(fees_after - fees_before, 4),
                                "config_hash": self._config_hash,
                            },
                        )

                import asyncio as _asyncio

                await _asyncio.to_thread(_write)
            except Exception as e:
                logger.warning(f"Grid {pair}: no se pudo registrar rebalance_event: {e}")
            pnl = pnl_after
            fees = fees_after
            trades_count = self._state[pair]["total_grid_trades"]
            capital_per_pair = self._capital_per_pair()
            logger.debug(
                f"Grid {pair}: capital tras rebalance = {capital_per_pair:.2f}€ "
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

            await self._sync_broker_state(pair)
            await self._save_pair_state(pair)
            try:
                await self.portfolio.update_valuations({pair: current_price}, self.open_positions_for_valuations())
            except Exception:
                pass
            logger.info(
                f"Grid {pair}: recalculado. Nuevo centro: {current_price:.2f}e"
            )
            if self.telegram:
                await self.telegram.notify_warning(
                    f"Grid {pair} RECENTRADO por desviación {deviation:.1%}: "
                    f"{n_liquidated} posiciones liquidadas @ {current_price:.2f}€ · PnL {pnl:+.2f}€"
                )

    async def _check_global_stop_loss(self):
        """Stop loss global del grid.

        En demo: detiene el grid. En modo real: cierra toda la exposicion a
        mercado (_flatten_all_real) antes de detener.
        """
        mode = (config.trading.mode or "demo").lower()
        port_state = self.portfolio.get()
        total_pnl = port_state.get("total_pnl_eur", 0)
        total_balance = port_state.get(
            "total_value_eur",
            port_state.get("balance_eur", config.trading.demo_initial_balance),
        )
        total_invested = total_balance * config.grid.capital_pct
        if total_invested > 0:
            pnl_pct = total_pnl / total_invested
            if pnl_pct < -config.grid.stop_loss_pct:
                logger.warning(
                    f"Grid SL activado: PnL={pnl_pct:.1%} < -{config.grid.stop_loss_pct:.0%} (mode={mode})"
                )
                if self.telegram:
                    await self.telegram.notify_warning(
                        f"Stop-loss global: PnL {pnl_pct:.1%} < -{config.grid.stop_loss_pct:.0%} · {total_pnl:+.2f}€",
                        warn_key="global_sl",
                    )
                if mode == "real":
                    await self._flatten_all_real()
                await self.stop()

    async def _flatten_all_real(self):
        """Cierra toda la exposicion real al dispararse el stop-loss global."""
        for pair in list(self._state.keys()):
            logger.warning(f"[REAL-MODE] SL global: cerrando exposicion de {pair}")
            try:
                await self._real_liquidate_pair(pair, self._state[pair].get("current_price", 0) or 0)
            except Exception as e:
                logger.error(f"[REAL-MODE] flatten_all {pair}: {e}")

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
                    for lvl in parsed.get("levels", []):
                        lvl.setdefault("is_opening", _is_opening(lvl))
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
            "execution_mode": "demo" if config.trading.is_demo() else "real",
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
            "margin": self.margin_health(
                {p: st.get("current_price", 0) for p, st in self._state.items()}
            ),
            "margin_limits": {
                "call_pct": config.grid.margin_call_pct,
                "stop_pct": config.grid.margin_stop_pct,
                "leverage": config.exchange.margin_leverage,
                "liq_fee_pct": config.grid.margin_liq_fee_pct,
            },
            "config": {
                "leverage": config.grid.leverage,
                "levels_per_pair": config.grid.levels_per_pair,
                "min_lot_value_eur": config.grid.min_lot_value_eur,
                "capital_pct": config.grid.capital_pct,
                "range_pct": config.grid.range_pct,
                "rebalance_threshold": config.grid.rebalance_threshold,
                "stop_loss_pct": config.grid.stop_loss_pct,
                "poll_interval": config.grid.poll_interval,
                "taker_fee": config.exchange.taker_fee,
                "maker_fee": config.exchange.maker_fee,
                "margin_enabled": config.exchange.margin_enabled,
                "margin_mode": config.exchange.margin_mode,
                "margin_leverage": config.exchange.margin_leverage,
                "allow_short": config.exchange.allow_short,
            },
        }
