"""Notificaciones via Telegram Bot API."""
from datetime import datetime
import httpx
from loguru import logger
from config import config


def _format_duration(entry_ts: str) -> str:
    """Calcula duración desde entrada hasta ahora."""
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
        label = "SHORT" if is_short else "COMPRA"
        pair = trade["pair"].replace("/", "")
        balance = portfolio_state.get("balance_eur", 0) if portfolio_state else 0
        price = trade["price"]
        crypto = trade["amount_crypto"]
        sl = trade.get("stop_loss", 0)
        tp = trade.get("take_profit", 0)

        line1 = f"{emoji} *{label}* {pair} {price:.2f}€ · {crypto:.6g} {pair.split('/')[0]}"
        line2 = f"SL {sl:.2f} TP {tp:.2f} · Balance {balance:.2f}€"
        text = f"{line1}\n{line2}"
        await self._send(text)

    async def notify_position_closed(self, trade: dict, pnl_eur: float, position: dict, portfolio_state: dict | None = None) -> None:
        emoji = "💚" if pnl_eur >= 0 else "🔴"
        sign = "+" if pnl_eur >= 0 else ""
        reason = trade.get("close_reason", "signal")
        duration = _format_duration(position.get("entry_timestamp", "") if position else trade.get("entry_timestamp", ""))
        is_short_close = trade.get("side") == "buy_to_close"
        label = "COBERTURA" if is_short_close else "VENTA"
        pair = trade["pair"].replace("/", "")
        balance = portfolio_state.get("balance_eur", 0) if portfolio_state else 0
        pnl_pct = trade.get("pnl_pct", 0)

        line1 = f"{emoji} *{label}* {pair} {sign}{pnl_eur:.2f}€ ({sign}{pnl_pct:.2f}%) · {duration}"
        line2 = f"{reason} · Balance {balance:.2f}€"
        text = f"{line1}\n{line2}"
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
