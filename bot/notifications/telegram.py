"""Notificaciones via Telegram Bot API."""
from datetime import datetime, timezone
import httpx
from loguru import logger
from config import config


def _fmt_price(price: float) -> str:
    if price >= 1000:
        return f"{price:.0f}"
    return f"{price:.2f}"


def _pair_short(pair: str) -> str:
    return pair.split("/")[0] if "/" in pair else pair


def _format_duration_between(start_ts: str, end_ts: str) -> str:
    """Formatea la duración entre dos timestamps ISO (consciente de TZ)."""
    if not start_ts or not end_ts:
        return "—"
    try:
        start = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
        delta = end - start
        seconds = int(delta.total_seconds())
        if seconds < 0:
            seconds = 0
        hours, remainder = divmod(seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        if minutes > 0:
            return f"{minutes} min"
        return "<1m"
    except Exception:
        return "—"


def _format_duration(entry_ts: str) -> str:
    if not entry_ts:
        return "—"
    try:
        now = datetime.now(timezone.utc).isoformat()
        return _format_duration_between(entry_ts, now)
    except Exception:
        return "—"


class TelegramNotifier:
    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self):
        self.enabled = config.telegram.enabled
        self.token = config.telegram.bot_token
        self.chat_id = config.telegram.chat_id
        self._last_send_time = {}
        self._warning_cooldown = 300
        self._error_cooldown = 300

    async def _send(self, text: str, priority: str = "normal", warn_key: str = None) -> None:
        if not self.enabled or not self.token:
            return

        cooldown = {"warning": self._warning_cooldown, "error": self._error_cooldown}.get(priority, 0)
        if cooldown > 0:
            category = warn_key or f"text:{hash(text[:50])}"
            key = f"{priority}:{category}"
            now = __import__("time").time()
            if key in self._last_send_time and now - self._last_send_time[key] < cooldown:
                return
            self._last_send_time[key] = now

        url = self.BASE_URL.format(token=self.token)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(url, json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                })
        except Exception as e:
            logger.warning(f"Error enviando notificación Telegram: {e}")

    async def notify_bot_started(self) -> None:
        mode = "DEMO" if config.trading.is_demo() else "REAL"
        text = f"🤖 *Bot iniciado* · {mode} · {', '.join(config.trading.pairs)} · {config.trading.analysis_interval // 60}min · {config.trading.timeframe}"
        await self._send(text)

    async def notify_bot_stopped(self) -> None:
        text = "🛑 *Bot detenido*"
        await self._send(text)

    async def notify_trade(self, trade: dict, signal: dict, portfolio_state: dict | None = None) -> None:
        is_short = trade.get("side") == "short"
        emoji = "🔴" if is_short else "🟢"
        label = "APOSTADO A BAJA" if is_short else "COMPRADO"
        pair = _pair_short(trade["pair"])
        balance = portfolio_state.get("balance_eur", 0) if portfolio_state else 0
        price = _fmt_price(trade["price"])
        amount_eur = trade.get("amount_eur", 0)

        text = f"{emoji} {label} {pair} {amount_eur:.0f}€ a {price}€ · Efectivo: {balance:.0f}€"
        await self._send(text)

    async def notify_position_closed(self, trade: dict, pnl_eur: float, position: dict, portfolio_state: dict | None = None) -> None:
        if pnl_eur >= 0:
            emoji = "💰"
            label = "GANANCIA"
        else:
            emoji = "🔻"
            label = "PÉRDIDA"
        duration = _format_duration(position.get("entry_timestamp", "") if position else trade.get("entry_timestamp", ""))
        pair = _pair_short(trade["pair"])
        balance = portfolio_state.get("balance_eur", 0) if portfolio_state else 0

        text = f"{emoji} {label} {pnl_eur:+.2f}€ en {pair} · {duration} · Efectivo: {balance:.2f}€"
        await self._send(text)

    async def notify_error(self, error: str) -> None:
        text = f"🔴 *ERROR* `{error[:300]}`"
        await self._send(text, priority="error")

    async def notify_warning(self, message: str, warn_key: str = None) -> None:
        text = f"⚠️ *AVISO* {message}"
        await self._send(text, priority="warning", warn_key=warn_key)

    async def notify_grid_cycle(
        self, pair: str, side: str, entry_price: float, exit_price: float,
        pnl_net: float, fees_total: float, duration: str,
    ) -> None:
        """Notifica una operacion completa del grid (cierre de ciclo) con formato historial."""
        side_label = "LARGO" if side == "buy" else "CORTO"
        if pnl_net > 0:
            emoji = "🟢"
            label = "GANANCIA"
        elif pnl_net < 0:
            emoji = "🔴"
            label = "PÉRDIDA"
        else:
            emoji = "🟡"
            label = "EMPATE"
        pair_s = _pair_short(pair)
        entry_s = f"{entry_price:.2f}€" if entry_price is not None else "—"
        exit_s = f"{exit_price:.2f}€" if exit_price is not None else "—"
        text = (
            f"{emoji} *{label} CICLO GRID* {pair_s} · {side_label}\n"
            f"Entrada {entry_s} → Salida {exit_s}\n"
            f"PnL {pnl_net:+.2f}€ · Comisiones {fees_total:.2f}€ · {duration}"
        )
        await self._send(text)

    def _op_line(self, op: dict) -> str:
        """Formatea una operacion como una linea del historial (fila de la tabla)."""
        pair = _pair_short(op.get("pair", "-"))
        status = op.get("status", "open")
        side_label = "ABIERTA" if status == "open" else ("LARGO" if op.get("side") == "buy" else "CORTO")
        ts = op.get("entry_timestamp") or ""
        hora = ts[11:16] if len(ts) >= 16 else ""
        entry = op.get("entry_price")
        exit_p = op.get("exit_price")
        pnl = op.get("pnl_eur")
        fees = op.get("total_fees", 0)

        if status == "open":
            emoji = "⚪"
        elif pnl is None:
            emoji = "⚪"
        elif pnl > 0:
            emoji = "🟢"
        elif pnl < 0:
            emoji = "🔴"
        else:
            emoji = "🟡"

        entry_s = f"{entry:.2f}€" if entry is not None else "—"
        exit_s = f"{exit_p:.2f}€" if exit_p is not None else "—"
        pnl_s = f"{pnl:+.2f}€" if pnl is not None else "—"
        return (
            f"{emoji} {hora} · {pair} · {side_label} · "
            f"Entrada {entry_s} · Salida {exit_s} · PnL {pnl_s} · Com. {fees:.2f}€"
        )

    async def send_daily_summary(self, portfolio: dict, stats: dict, grid: dict | None = None, operations: list | None = None) -> None:
        mode = "DEMO" if config.trading.is_demo() else "REAL"
        pnl = portfolio.get("total_pnl_eur", 0)
        pnl_pct = portfolio.get("total_pnl_pct", 0)
        val = portfolio.get("total_value_eur", 0)
        open_pos = stats.get("open_operations", portfolio.get("open_positions", 0))
        pnl_emoji = "📈" if pnl >= 0 else "📉"

        win_rate = stats.get("win_rate", 0)
        if win_rate and win_rate <= 1:
            win_rate *= 100
        today = stats.get("today_operations", stats.get("trades_today", 0))
        wins = stats.get("wins_today", 0)
        losses = stats.get("losses_today", 0)
        today_closed = stats.get("today_closed", 0)
        errors = stats.get("errors_today", 0)

        text = (
            f"📊 *Resumen* `{mode}` · {val:.2f}€ · {pnl_emoji} PnL {pnl:+.2f}€ ({pnl_pct:+.2f}%)\n"
            f"Hoy {today} ops ({wins}✅ / {losses}❌ de {today_closed} cerradas) · "
            f"WR {win_rate:.0f}% · {open_pos} abiertas · Errores {errors}"
        )
        if grid:
            grid_pnl = grid.get("total_pnl_eur", 0)
            grid_fees = grid.get("total_fees_eur", 0)
            grid_emoji = "📈" if grid_pnl >= 0 else "📉"
            text += f"\nGrid {grid_emoji} {grid_pnl:+.2f}€ · Comisiones {grid_fees:.2f}€"
        if operations:
            text += "\n\n📋 *Operaciones recientes*"
            for op in operations[:8]:
                text += "\n" + self._op_line(op)
        await self._send(text)
