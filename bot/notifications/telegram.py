"""Notificaciones via Telegram Bot API."""
import httpx
from loguru import logger
from config import config


class TelegramNotifier:
    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self):
        self.enabled = config.telegram.enabled
        self.token = config.telegram.bot_token
        self.chat_id = config.telegram.chat_id

    async def _send(self, text: str) -> None:
        if not self.enabled or not self.token:
            return
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

    async def notify_trade(self, trade: dict) -> None:
        mode_label = "[DEMO]" if trade.get("mode") == "demo" else "⚠️ [REAL]"
        text = (
            f"🟢 *COMPRA ejecutada {mode_label}*\n"
            f"Par: `{trade['pair']}`\n"
            f"Cantidad: `{trade['amount_crypto']:.8f}`\n"
            f"Precio: `{trade['price']:.2f}€`\n"
            f"Inversión: `{trade['amount_eur']:.2f}€`\n"
            f"Stop-loss: `{trade.get('stop_loss', 0):.2f}€`\n"
            f"Take-profit: `{trade.get('take_profit', 0):.2f}€`\n"
        )
        await self._send(text)

    async def notify_position_closed(self, trade: dict, pnl_eur: float) -> None:
        emoji = "💚" if pnl_eur >= 0 else "🔴"
        sign = "+" if pnl_eur >= 0 else ""
        text = (
            f"{emoji} *VENTA ejecutada [{trade.get('mode', 'demo').upper()}]*\n"
            f"Par: `{trade['pair']}`\n"
            f"Precio: `{trade['price']:.2f}€`\n"
            f"PnL: `{sign}{pnl_eur:.2f}€ ({sign}{trade.get('pnl_pct', 0):.2f}%)`\n"
            f"Razón: `{trade.get('close_reason', '-')}`\n"
        )
        await self._send(text)

    async def notify_error(self, error: str) -> None:
        await self._send(f"🔴 *ERROR CRÍTICO BOT*\n```{error[:500]}```")

    async def send_daily_summary(self, portfolio: dict, stats: dict) -> None:
        text = (
            f"📊 *Resumen diario [{'DEMO' if config.trading.is_demo() else 'REAL'}]*\n"
            f"Portfolio: `{portfolio['total_value_eur']:.2f}€`\n"
            f"PnL total: `{portfolio['total_pnl_eur']:+.2f}€ ({portfolio['total_pnl_pct']:+.2f}%)`\n"
            f"Trades hoy: `{stats.get('trades_today', 0)}`\n"
            f"Win rate: `{stats.get('win_rate', 0):.1f}%`\n"
        )
        await self._send(text)
