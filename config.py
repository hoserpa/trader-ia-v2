"""Carga y validación de toda la configuración del bot desde variables de entorno."""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _split_pairs(val: str) -> list:
    """Divide lista de pares separada por comas: sin espacios ni vacíos."""
    return [p.strip() for p in val.split(",") if p.strip()]


def _normalize_timeframe(tf: str) -> str:
    """Normaliza el timeframe al formato que espera el exchange.
    
    15min -> 15m
    1hour -> 1h
    4h -> 4h (sin cambios)
    """
    tf = tf.lower().strip()
    mapping = {
        "1min": "1m", "3min": "3m", "5min": "5m", "15min": "15m", "30min": "30m",
        "1hour": "1h", "2hours": "2h", "4hours": "4h", "6hours": "6h", "8hours": "8h", "12hours": "12h",
        "1day": "1d", "3days": "3d", "1week": "1w", "1month": "1M",
    }
    return mapping.get(tf, tf)


@dataclass
class ExchangeConfig:
    name: str = field(default_factory=lambda: os.getenv("EXCHANGE", "kraken"))
    api_key: str = field(default_factory=lambda: os.getenv("KRAKEN_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("KRAKEN_API_SECRET", ""))
    taker_fee: float = field(default_factory=lambda: float(os.getenv("KRAKEN_TAKER_FEE", "0.0026")))  # Kraken taker fee ~0.26%
    maker_fee: float = field(default_factory=lambda: float(os.getenv("KRAKEN_MAKER_FEE", "0.0016")))  # Kraken maker fee ~0.16%
    margin_enabled: bool = field(default_factory=lambda: os.getenv("EXCHANGE_MARGIN_ENABLED", "false").lower() == "true")
    margin_mode: str = field(default_factory=lambda: os.getenv("EXCHANGE_MARGIN_MODE", "isolated"))
    margin_leverage: int = field(default_factory=lambda: int(os.getenv("EXCHANGE_MARGIN_LEVERAGE", "2")))
    allow_short: bool = field(default_factory=lambda: os.getenv("EXCHANGE_ALLOW_SHORT", "false").lower() == "true")


@dataclass
class TradingConfig:
    mode: str = field(default_factory=lambda: os.getenv("TRADING_MODE", "demo"))
    pairs: list = field(default_factory=lambda: _split_pairs(os.getenv("TRADING_PAIRS", "BTC/EUR,ETH/EUR,SOL/EUR")))
    base_currency: str = field(default_factory=lambda: os.getenv("BASE_CURRENCY", "EUR"))
    demo_initial_balance: float = field(default_factory=lambda: float(os.getenv("DEMO_INITIAL_BALANCE", "100.0")))
    analysis_interval: int = field(default_factory=lambda: int(os.getenv("ANALYSIS_INTERVAL_SECONDS", "600")))
    timeframe: str = field(default_factory=lambda: _normalize_timeframe(os.getenv("TIMEFRAME", "15m")))

    def is_demo(self) -> bool:
        return self.mode == "demo"


@dataclass
class GridConfig:
    enabled: bool = field(default_factory=lambda: os.getenv("GRID_ENABLED", "true").lower() == "true")
    pairs: list = field(default_factory=lambda: _split_pairs(os.getenv("GRID_PAIRS", "BTC/EUR,ETH/EUR,SOL/EUR")))
    leverage: int = field(default_factory=lambda: int(os.getenv("GRID_LEVERAGE", "1")))
    levels_per_pair: int = field(default_factory=lambda: int(os.getenv("GRID_LEVELS", "15")))
    min_lot_value_eur: float = field(default_factory=lambda: float(os.getenv("GRID_MIN_LOT_VALUE_EUR", "5")))
    capital_pct: float = field(default_factory=lambda: float(os.getenv("GRID_CAPITAL_PCT", "0.90")))
    range_pct: float = field(default_factory=lambda: float(os.getenv("GRID_RANGE_PCT", "0.05")))
    rebalance_threshold: float = field(default_factory=lambda: float(os.getenv("GRID_REBALANCE_THRESHOLD", "0.08")))
    stop_loss_pct: float = field(default_factory=lambda: float(os.getenv("GRID_STOP_LOSS_PCT", "0.05")))
    poll_interval: int = field(default_factory=lambda: int(os.getenv("GRID_POLL_INTERVAL", "15")))
    margin_open_fee_pct: float = field(default_factory=lambda: float(os.getenv("GRID_MARGIN_OPEN_FEE_PCT", "0.0002")))
    margin_rollover_pct: float = field(default_factory=lambda: float(os.getenv("GRID_MARGIN_ROLLOVER_PCT", "0.00025")))
    margin_rollover_hours: int = field(default_factory=lambda: int(os.getenv("GRID_MARGIN_ROLLOVER_HOURS", "4")))
    margin_call_pct: float = field(default_factory=lambda: float(os.getenv("GRID_MARGIN_CALL_PCT", "0.80")))
    margin_stop_pct: float = field(default_factory=lambda: float(os.getenv("GRID_MARGIN_STOP_PCT", "0.40")))
    margin_liq_fee_pct: float = field(default_factory=lambda: float(os.getenv("GRID_MARGIN_LIQ_FEE_PCT", "0.03")))


@dataclass
class DatabaseConfig:
    sqlite_path: str = field(default_factory=lambda: os.getenv("SQLITE_DB_PATH", "/app/data/crypto_trader.db"))
    redis_host: str = field(default_factory=lambda: os.getenv("REDIS_HOST", "redis"))
    redis_port: int = field(default_factory=lambda: int(os.getenv("REDIS_PORT", "6379")))
    redis_db: int = field(default_factory=lambda: int(os.getenv("REDIS_DB", "0")))


@dataclass
class TelegramConfig:
    enabled: bool = field(default_factory=lambda: os.getenv("TELEGRAM_ENABLED", "false").lower() == "true")
    bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))


@dataclass
class LogConfig:
    level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    file: str = field(default_factory=lambda: os.getenv("LOG_FILE", "/app/logs/bot.log"))
    max_size: int = field(default_factory=lambda: int(os.getenv("LOG_MAX_SIZE_MB", "50")))
    backup_count: int = field(default_factory=lambda: int(os.getenv("LOG_BACKUP_COUNT", "5")))


@dataclass
class AppConfig:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    grid: GridConfig = field(default_factory=GridConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    log: LogConfig = field(default_factory=LogConfig)

    def validate(self) -> None:
        if not self.trading.is_demo():
            if not self.exchange.api_key or not self.exchange.api_secret:
                raise ValueError("KRAKEN_API_KEY y KRAKEN_API_SECRET son obligatorios en modo real.")
        if self.trading.mode not in ("demo", "real"):
            raise ValueError(f"TRADING_MODE inválido: {self.trading.mode}. Debe ser 'demo' o 'real'.")
        if not (0 < self.exchange.taker_fee <= 0.05):
            raise ValueError(f"KRAKEN_TAKER_FEE inválido: {self.exchange.taker_fee}. Debe estar entre 0 y 0.05.")
        if not (0 < self.exchange.maker_fee <= 0.05):
            raise ValueError(f"KRAKEN_MAKER_FEE inválido: {self.exchange.maker_fee}. Debe estar entre 0 y 0.05.")
        if not (self.grid.min_lot_value_eur > 0):
            raise ValueError(f"GRID_MIN_LOT_VALUE_EUR inválido: {self.grid.min_lot_value_eur}. Debe ser > 0.")
        if self.grid.leverage < 1:
            raise ValueError(f"GRID_LEVERAGE inválido: {self.grid.leverage}. Debe ser >= 1.")
        if self.grid.levels_per_pair < 2:
            raise ValueError(f"GRID_LEVELS inválido: {self.grid.levels_per_pair}. Debe ser >= 2.")
        if not (0 < self.grid.capital_pct <= 1):
            raise ValueError(f"GRID_CAPITAL_PCT inválido: {self.grid.capital_pct}. Debe estar entre 0 y 1.")
        if not (0 < self.grid.range_pct < 1):
            raise ValueError(f"GRID_RANGE_PCT inválido: {self.grid.range_pct}. Debe estar entre 0 y 1.")
        if self.grid.rebalance_threshold <= 0:
            raise ValueError(f"GRID_REBALANCE_THRESHOLD inválido: {self.grid.rebalance_threshold}. Debe ser > 0.")
        if self.grid.poll_interval <= 0:
            raise ValueError(f"GRID_POLL_INTERVAL inválido: {self.grid.poll_interval}. Debe ser > 0.")
        if self.grid.stop_loss_pct <= 0:
            raise ValueError(f"GRID_STOP_LOSS_PCT inválido: {self.grid.stop_loss_pct}. Debe ser > 0.")
        if not (0 <= self.grid.margin_open_fee_pct <= 0.05):
            raise ValueError(f"GRID_MARGIN_OPEN_FEE_PCT inválido: {self.grid.margin_open_fee_pct}. Debe estar entre 0 y 0.05 (Kraken: 0.01-0.05%).")
        if not (0 <= self.grid.margin_rollover_pct <= 0.05):
            raise ValueError(f"GRID_MARGIN_ROLLOVER_PCT inválido: {self.grid.margin_rollover_pct}. Debe estar entre 0 y 0.05 por periodo (Kraken: 0.01-0.05%/4h).")
        if self.grid.margin_rollover_hours <= 0:
            raise ValueError(f"GRID_MARGIN_ROLLOVER_HOURS inválido: {self.grid.margin_rollover_hours}. Debe ser > 0.")
        if not (0 < self.grid.margin_call_pct < 1):
            raise ValueError(f"GRID_MARGIN_CALL_PCT inválido: {self.grid.margin_call_pct}. Debe estar entre 0 y 1 (Kraken: 0.80).")
        if not (0 < self.grid.margin_stop_pct <= self.grid.margin_call_pct):
            raise ValueError(f"GRID_MARGIN_STOP_PCT inválido: {self.grid.margin_stop_pct}. Debe estar entre 0 y margin_call (Kraken: 0.40).")
        if not (0 <= self.grid.margin_liq_fee_pct <= 0.10):
            raise ValueError(f"GRID_MARGIN_LIQ_FEE_PCT inválido: {self.grid.margin_liq_fee_pct}. Debe estar entre 0 y 0.10 (Kraken liquida con comision 2-3%).")
        if not (self.exchange.margin_leverage >= 1):
            raise ValueError(f"EXCHANGE_MARGIN_LEVERAGE inválido: {self.exchange.margin_leverage}. Debe ser >= 1.")
        if self.exchange.margin_mode not in ("isolated", "cross"):
            raise ValueError(f"EXCHANGE_MARGIN_MODE inválido: {self.exchange.margin_mode}. Debe ser 'isolated' o 'cross'.")
        if self.exchange.allow_short and not self.exchange.margin_enabled:
            raise ValueError("EXCHANGE_ALLOW_SHORT=true requiere EXCHANGE_MARGIN_ENABLED=true: sin margen la API real no permite shorts.")


config = AppConfig()
