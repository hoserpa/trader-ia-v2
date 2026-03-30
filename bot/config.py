"""Carga y validación de toda la configuración del bot desde variables de entorno."""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ExchangeConfig:
    name: str = field(default_factory=lambda: os.getenv("EXCHANGE", "binance"))
    api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET", ""))
    taker_fee: float = 0.001  # Binance taker fee ~0.1%


@dataclass
class TradingConfig:
    mode: str = field(default_factory=lambda: os.getenv("TRADING_MODE", "demo"))
    pairs: list = field(default_factory=lambda: os.getenv("TRADING_PAIRS", "BTC/EUR,ETH/EUR,SOL/EUR").split(","))
    base_currency: str = field(default_factory=lambda: os.getenv("BASE_CURRENCY", "EUR"))
    demo_initial_balance: float = field(default_factory=lambda: float(os.getenv("DEMO_INITIAL_BALANCE", "1000.0")))
    analysis_interval: int = field(default_factory=lambda: int(os.getenv("ANALYSIS_INTERVAL_SECONDS", "3600")))
    timeframe: str = field(default_factory=lambda: os.getenv("MODEL_TIMEFRAME", "1h"))
    candles_required: int = field(default_factory=lambda: int(os.getenv("MODEL_CANDLES_REQUIRED", "200")))

    def is_demo(self) -> bool:
        return self.mode == "demo"


@dataclass
class RiskConfig:
    max_risk_per_trade_pct: float = field(default_factory=lambda: float(os.getenv("MAX_RISK_PER_TRADE_PCT", "0.02")))
    max_open_positions: int = field(default_factory=lambda: int(os.getenv("MAX_OPEN_POSITIONS", "3")))
    max_portfolio_in_crypto_pct: float = field(default_factory=lambda: float(os.getenv("MAX_PORTFOLIO_IN_CRYPTO_PCT", "0.60")))
    buy_threshold: float = field(default_factory=lambda: float(os.getenv("BUY_THRESHOLD", "0.40")))
    sell_threshold: float = field(default_factory=lambda: float(os.getenv("SELL_THRESHOLD", "0.40")))
    stop_loss_atr_multiplier: float = field(default_factory=lambda: float(os.getenv("STOP_LOSS_ATR_MULTIPLIER", "1.5")))
    take_profit_atr_multiplier: float = field(default_factory=lambda: float(os.getenv("TAKE_PROFIT_ATR_MULTIPLIER", "3.0")))
    max_daily_trades: int = field(default_factory=lambda: int(os.getenv("MAX_DAILY_TRADES", "20")))
    high_volatility_atr_threshold: float = field(default_factory=lambda: float(os.getenv("HIGH_VOLATILITY_ATR_THRESHOLD", "0.05")))
    min_confidence_threshold: float = field(default_factory=lambda: float(os.getenv("MIN_CONFIDENCE_THRESHOLD", "0.0")))
    min_trade_eur: float = 10.0


@dataclass
class DatabaseConfig:
    sqlite_path: str = field(default_factory=lambda: os.getenv("SQLITE_DB_PATH", "/app/data/crypto_trader.db"))
    redis_host: str = field(default_factory=lambda: os.getenv("REDIS_HOST", "redis"))
    redis_port: int = field(default_factory=lambda: int(os.getenv("REDIS_PORT", "6379")))
    redis_db: int = field(default_factory=lambda: int(os.getenv("REDIS_DB", "0")))


@dataclass
class ModelConfig:
    model_path: str = field(default_factory=lambda: os.getenv("MODEL_PATH", "/app/model/trained_model.pkl"))
    scaler_path: str = field(default_factory=lambda: os.getenv("SCALER_PATH", "/app/model/scaler.pkl"))


@dataclass
class TelegramConfig:
    enabled: bool = field(default_factory=lambda: os.getenv("TELEGRAM_ENABLED", "false").lower() == "true")
    bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))


@dataclass
class APIConfig:
    host: str = field(default_factory=lambda: os.getenv("API_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))
    username: str = field(default_factory=lambda: os.getenv("API_USERNAME", "admin"))
    password: str = field(default_factory=lambda: os.getenv("API_PASSWORD", "changeme"))


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
    risk: RiskConfig = field(default_factory=RiskConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    api: APIConfig = field(default_factory=APIConfig)
    log: LogConfig = field(default_factory=LogConfig)

    def validate(self) -> None:
        if not self.trading.is_demo():
            if not self.exchange.api_key or not self.exchange.api_secret:
                raise ValueError("BINANCE_API_KEY y BINANCE_API_SECRET son obligatorios en modo real.")
        if self.trading.mode not in ("demo", "real"):
            raise ValueError(f"TRADING_MODE inválido: {self.trading.mode}. Debe ser 'demo' o 'real'.")


config = AppConfig()
