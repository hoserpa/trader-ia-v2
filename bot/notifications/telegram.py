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
        text = (
            f"🤖 *Bot iniciado*\n"
            f"Modo: `{mode}`\n"
            f"Pares: `{', '.join(config.trading.pairs)}`\n"
            f"Intervalo: `{config.trading.analysis_interval // 60}min`\n"
            f"Timeframe: `{config.trading.timeframe}`"
        )
        await self._send(text)

    async def notify_bot_stopped(self) -> None:
        text = "🛑 *Bot detenido*"
        await self._send(text)

    async def notify_trade(self, trade: dict, signal: dict) -> None:
        mode_label = "DEMO" if trade.get("mode") == "demo" else "⚠️ REAL"
        text = (
            f"🟢 *NUEVA COMPRA* `[{mode_label}]`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 *{trade['pair']}*\n"
            f"💰 Invertido: `{trade.get('amount_eur', 0):.2f}€`\n"
            f"📊 Recibido: `{trade['amount_crypto']:.8f}`\n"
            f"💵 Precio entrada: `{trade['price']:.2f}€`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ SL: `{trade.get('stop_loss', 0):.2f}€` | 🎯 TP: `{trade.get('take_profit', 0):.2f}€`\n"
            f"📈 Señal: *{signal.get('signal', 'HOLD')}* `{signal.get('confidence', 0):.0%}` | B:{signal.get('probabilities', {}).get('BUY', 0):.0%} S:{signal.get('probabilities', {}).get('SELL', 0):.0%}"
        )
        await self._send(text)

    async def notify_position_closed(self, trade: dict, pnl_eur: float, position: object) -> None:
        emoji = "💚" if pnl_eur >= 0 else "🔴"
        sign = "+" if pnl_eur >= 0 else ""
        reason = trade.get("close_reason", "signal")
        entry_price = trade.get("entry_price", 0)
        duration = _format_duration(trade.get("entry_timestamp", ""))
        
        reason_icon = "📊"
        if "stop" in reason.lower():
            reason_icon = "🛡️"
        elif "take" in reason.lower():
            reason_icon = "🎯"
        
        text = (
            f"{emoji} *VENTA* `[{trade.get('mode', 'demo').upper()}]`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 *{trade['pair']}* a `{trade['price']:.2f}€`\n"
            f"(entró a `{entry_price:.2f}€` | ⏱️ {duration})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 PnL: `{sign}{pnl_eur:.2f}€ ({sign}{trade.get('pnl_pct', 0):.2f}%)`\n"
            f"{reason_icon} Razón: `{reason}`"
        )
        await self._send(text)

    async def notify_error(self, error: str) -> None:
        text = (
            f"🔴 *ERROR CRÍTICO*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"```{error[:500]}```"
        )
        await self._send(text, priority="error")

    async def notify_warning(self, message: str) -> None:
        text = (
            f"⚠️ *AVISO*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{message}"
        )
        await self._send(text, priority="warning")

    async def notify_signal(self, signal: dict) -> None:
        signal_emoji = "🟢" if signal["signal"] == "BUY" else ("🔴" if signal["signal"] == "SELL" else "⚪")
        text = (
            f"{signal_emoji} *SEÑAL {signal['signal']}*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📌 Par: `{signal['pair']}`\n"
            f"💶 Precio: `{signal.get('price', 0):.2f}€`\n"
            f"📊 Confianza: `{signal['confidence']:.1%}`"
        )
        await self._send(text, priority="info")

    async def send_daily_summary(self, portfolio: dict, stats: dict) -> None:
        mode = "DEMO" if config.trading.is_demo() else "REAL"
        pnl = portfolio.get("total_pnl_eur", 0)
        pnl_pct = portfolio.get("total_pnl_pct", 0)
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        
        win_rate = stats.get("win_rate", 0) * 100 if stats.get("win_rate", 0) <= 1 else stats.get("win_rate", 0)
        trades = stats.get("trades_today", 0)
        wins = stats.get("wins_today", 0)
        losses = stats.get("losses_today", 0)
        best_trade = stats.get("best_trade", 0)
        worst_trade = stats.get("worst_trade", 0)
        max_dd = stats.get("max_drawdown", 0)
        errors = stats.get("errors_today", 0)
        
        text = (
            f"📊 *Resumen Diario* `[{mode}]`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{pnl_emoji} Portfolio: `{portfolio.get('total_value_eur', 0):.2f}€`\n"
            f"{pnl_emoji} PnL: `{pnl:+.2f}€ ({pnl_pct:+.2f}%)`\n"
            f"\n"
            f"📈 *Trades:*\n"
            f"├ Hoy: `{trades}` ({wins}✅ / {losses}❌)\n"
            f"├ Win rate: `{win_rate:.1f}%`\n"
            f"├ Mejor: `+{best_trade:.2f}€`\n"
            f"└ Peor: `{worst_trade:.2f}€`\n"
            f"\n"
            f"📊 *Riesgo:*\n"
            f"├ Max DD: `{max_dd:.1f}%`\n"
            f"└ Posiciones: `{portfolio.get('open_positions', 0)}`\n"
            f"\n"
            f"🔧 *Sistema:*\n"
            f"└ Errores hoy: `{errors}`"
        )
        await self._send(text)
