"""Carga y validación de toda la configuración del bot desde variables de entorno."""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _normalize_pair(pair: str, base_currency: str = "EUR") -> str:
    """Convierte par al formato que espera ccxt.
    
    BTC/EUR -> BTC/EUR (ccxt maneja este formato)
    BTCEUR -> BTC/EUR (convierte formato sin barra)
    """
    if "/" in pair:
        return pair
    if len(pair) == 6 and pair.isupper():
        base = pair[:3]
        quote = pair[3:]
        return f"{base}/{quote}"
    return pair


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


def _get_exchange_symbol(pair: str) -> str:
    """Convierte par al formato de símbolo del exchange (sin barra).
    
    BTC/EUR -> BTCEUR
    """
    return pair.replace("/", "")


@dataclass
class ExchangeConfig:
    name: str = field(default_factory=lambda: os.getenv("EXCHANGE", "kraken"))
    api_key: str = field(default_factory=lambda: os.getenv("KRAKEN_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("KRAKEN_API_SECRET", ""))
    taker_fee: float = 0.0026  # Kraken taker fee ~0.26%
    maker_fee: float = 0.0016  # Kraken maker fee ~0.16%


@dataclass
class TradingConfig:
    mode: str = field(default_factory=lambda: os.getenv("TRADING_MODE", "demo"))
    pairs: list = field(default_factory=lambda: os.getenv("TRADING_PAIRS", "BTC/EUR,ETH/EUR,SOL/EUR").split(","))
    base_currency: str = field(default_factory=lambda: os.getenv("BASE_CURRENCY", "EUR"))
    demo_initial_balance: float = field(default_factory=lambda: float(os.getenv("DEMO_INITIAL_BALANCE", "100.0")))
    analysis_interval: int = field(default_factory=lambda: int(os.getenv("ANALYSIS_INTERVAL_SECONDS", "600")))
    timeframe: str = field(default_factory=lambda: _normalize_timeframe(os.getenv("MODEL_TIMEFRAME", "15m")))
    candles_required: int = field(default_factory=lambda: int(os.getenv("MODEL_CANDLES_REQUIRED", "200")))

    def is_demo(self) -> bool:
        return self.mode == "demo"
    
    def get_symbol(self, pair: str) -> str:
        """Retorna el símbolo del par para el exchange.
        
        ccxt maneja internamente la conversión al formato nativo del exchange,
        así que pasamos el formato unificado con barra.
        """
        return pair


@dataclass
class RiskConfig:
    max_risk_per_trade_pct: float = field(default_factory=lambda: float(os.getenv("MAX_RISK_PER_TRADE_PCT", "0.01")))
    max_open_positions: int = field(default_factory=lambda: int(os.getenv("MAX_OPEN_POSITIONS", "1")))
    max_portfolio_in_crypto_pct: float = field(default_factory=lambda: float(os.getenv("MAX_PORTFOLIO_IN_CRYPTO_PCT", "0.25")))
    buy_threshold: float = field(default_factory=lambda: float(os.getenv("BUY_THRESHOLD", "0.10")))
    sell_threshold: float = field(default_factory=lambda: float(os.getenv("SELL_THRESHOLD", "0.10")))
    stop_loss_atr_multiplier: float = field(default_factory=lambda: float(os.getenv("STOP_LOSS_ATR_MULTIPLIER", "2.0")))
    take_profit_atr_multiplier: float = field(default_factory=lambda: float(os.getenv("TAKE_PROFIT_ATR_MULTIPLIER", "3.0")))
    max_daily_trades: int = field(default_factory=lambda: int(os.getenv("MAX_DAILY_TRADES", "10")))
    high_volatility_atr_threshold: float = field(default_factory=lambda: float(os.getenv("HIGH_VOLATILITY_ATR_THRESHOLD", "0.025")))
    min_confidence_threshold: float = field(default_factory=lambda: float(os.getenv("MIN_CONFIDENCE_THRESHOLD", "0.20")))
    close_confidence_threshold: float = field(default_factory=lambda: float(os.getenv("CLOSE_CONFIDENCE_THRESHOLD", "0.45")))
    min_trade_eur: float = 5.0
    max_position_hours: int = field(default_factory=lambda: int(os.getenv("MAX_POSITION_HOURS", "4")))
    exchange_stop_loss: bool = field(default_factory=lambda: os.getenv("EXCHANGE_STOP_LOSS", "true").lower() == "true")
    limit_order_timeout: int = field(default_factory=lambda: int(os.getenv("LIMIT_ORDER_TIMEOUT", "15")))
    trailing_stop_activation_pct: float = field(default_factory=lambda: float(os.getenv("TRAILING_STOP_ACTIVATION_PCT", "0.008")))
    trailing_stop_distance_atr: float = field(default_factory=lambda: float(os.getenv("TRAILING_STOP_DISTANCE_ATR", "1.0")))
    partial_exit_pct: float = field(default_factory=lambda: float(os.getenv("PARTIAL_EXIT_PCT", "0.50")))
    partial_exit_r_multiple: float = field(default_factory=lambda: float(os.getenv("PARTIAL_EXIT_R_MULTIPLE", "1.5")))
    rsi_oversold: float = field(default_factory=lambda: float(os.getenv("RSI_OVERSOLD", "30.0")))
    rsi_overbought: float = field(default_factory=lambda: float(os.getenv("RSI_OVERBOUGHT", "70.0")))
    cooldown_minutes: int = field(default_factory=lambda: int(os.getenv("COOLDOWN_MINUTES", "30")))
    min_volatility_atr_pct: float = field(default_factory=lambda: float(os.getenv("MIN_VOLATILITY_ATR_PCT", "0.0015")))


@dataclass
class GridConfig:
    enabled: bool = field(default_factory=lambda: os.getenv("GRID_ENABLED", "false").lower() == "true")
    pairs: list = field(default_factory=lambda: os.getenv("GRID_PAIRS", "BTC/EUR,ETH/EUR,SOL/EUR").split(","))
    leverage: int = field(default_factory=lambda: int(os.getenv("GRID_LEVERAGE", "2")))
    levels_per_pair: int = field(default_factory=lambda: int(os.getenv("GRID_LEVELS", "20")))
    capital_pct: float = field(default_factory=lambda: float(os.getenv("GRID_CAPITAL_PCT", "0.7")))
    range_pct: float = field(default_factory=lambda: float(os.getenv("GRID_RANGE_PCT", "0.10")))
    rebalance_threshold: float = field(default_factory=lambda: float(os.getenv("GRID_REBALANCE_THRESHOLD", "0.15")))
    stop_loss_pct: float = field(default_factory=lambda: float(os.getenv("GRID_STOP_LOSS_PCT", "0.10")))
    poll_interval: int = field(default_factory=lambda: int(os.getenv("GRID_POLL_INTERVAL", "30")))


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
    grid: GridConfig = field(default_factory=GridConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    api: APIConfig = field(default_factory=APIConfig)
    log: LogConfig = field(default_factory=LogConfig)

    def validate(self) -> None:
        if not self.trading.is_demo():
            if not self.exchange.api_key or not self.exchange.api_secret:
                raise ValueError("KRAKEN_API_KEY y KRAKEN_API_SECRET son obligatorios en modo real.")
        if self.trading.mode not in ("demo", "real"):
            raise ValueError(f"TRADING_MODE inválido: {self.trading.mode}. Debe ser 'demo' o 'real'.")


config = AppConfig()
