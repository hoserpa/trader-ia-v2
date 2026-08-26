"""Notificaciones via Telegram Bot API."""
from datetime import datetime
import httpx
from loguru import logger
from config import config


def _fmt_price(price: float) -> str:
    if price >= 1000:
        return f"{price:.0f}"
    return f"{price:.2f}"


def _pair_short(pair: str) -> str:
    return pair.split("/")[0] if "/" in pair else pair


def _format_duration(entry_ts: str) -> str:
    if not entry_ts:
        return "—"
    try:
        entry = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
        now = datetime.utcnow()
        delta = now - entry.replace(tzinfo=None)
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes} min"
    except Exception:
        return "—"


class TelegramNotifier:
    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self):
        self.enabled = config.telegram.enabled
        self.token = config.telegram.bot_token
        self.chat_id = config.telegram.chat_id
        self._last_warning_time = {}
        self._warning_cooldown = 300

    async def _send(self, text: str, priority: str = "normal") -> None:
        if not self.enabled or not self.token:
            return

        cooldown_key = f"{priority}_{hash(text[:50])}"
        now = __import__("time").time()
        if priority == "warning":
            if cooldown_key in self._last_warning_time:
                if now - self._last_warning_time[cooldown_key] < self._warning_cooldown:
                    return
            self._last_warning_time[cooldown_key] = now

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

    async def notify_warning(self, message: str) -> None:
        text = f"⚠️ *AVISO* {message}"
        await self._send(text, priority="warning")

    async def send_daily_summary(self, portfolio: dict, stats: dict) -> None:
        mode = "DEMO" if config.trading.is_demo() else "REAL"
        pnl = portfolio.get("total_pnl_eur", 0)
        pnl_pct = portfolio.get("total_pnl_pct", 0)
        val = portfolio.get("total_value_eur", 0)
        open_pos = portfolio.get("open_positions", 0)
        pnl_emoji = "📈" if pnl >= 0 else "📉"

        win_rate = stats.get("win_rate", 0)
        if win_rate and win_rate <= 1:
            win_rate *= 100
        trades = stats.get("trades_today", 0)
        wins = stats.get("wins_today", 0)
        errors = stats.get("errors_today", 0)

        text = (
            f"📊 *Resumen* `{mode}` · {val:.2f}€ · {pnl_emoji} PnL {pnl:+.2f}€ ({pnl_pct:+.2f}%)\n"
            f"Hoy {trades} trades ({wins}✅) · WR {win_rate:.0f}% · {open_pos} open · Errores {errors}"
        )
        await self._send(text)
