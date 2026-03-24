# Crypto Trader Bot — Código de Implementación Completo

> Continuación de `crypto_trader_bot_spec.md`. Este documento contiene el código fuente completo y listo para usar de todos los módulos del sistema.

---

## Índice

1. [Archivos de configuración raíz](#1-archivos-de-configuración-raíz)
2. [bot/config.py](#2-botconfigpy)
3. [bot/database/models.py y init_db.py](#3-botdatabasemodelspy-e-init_dbpy)
4. [bot/database/crud.py](#4-botdatabasecrudpy)
5. [bot/data/collector.py](#5-botdatacollectorpy)
6. [bot/data/historical.py](#6-botdatahistoricalpy)
7. [bot/indicators/technical.py](#7-botindicatorstechnicalpy)
8. [bot/indicators/features.py](#8-botindicatorsfeaturespy)
9. [bot/model/predictor.py](#9-botmodelpredictorpy)
10. [bot/trading/risk_manager.py](#10-botttradingrisk_managerpy)
11. [bot/trading/portfolio.py](#11-bottradingportfoliospy)
12. [bot/trading/demo_trader.py](#12-bottradingdemo_traderpy)
13. [bot/trading/real_trader.py](#13-bottradingreal_traderpy)
14. [bot/trading/engine.py](#14-bottradingengine py)
15. [bot/notifications/telegram.py](#15-botnotificationstelegrampy)
16. [bot/scheduler/jobs.py](#16-botschedulerjobspy)
17. [bot/main.py](#17-botmainpy)
18. [api/main.py y routers](#18-apimainpy-y-routers)
19. [api/websocket/live.py](#19-apiwebsocketlivepy)
20. [frontend/index.html](#20-frontendindexhtml)
21. [frontend/js/app.js](#21-frontendjsappjs)
22. [training/fetch_historical_data.py](#22-trainingfetch_historical_datapy)
23. [training/train_model.py](#23-trainingtrain_modelpy)
24. [bot/requirements.txt](#24-botrequirementstxt)
25. [api/requirements.txt](#25-apirequirementstxt)
26. [bot/Dockerfile y api/Dockerfile](#26-botdockerfile-y-apidockerfile)
27. [scripts/setup_rpi.sh](#27-scriptssetup_rpish)

---

## 1. Archivos de configuración raíz

### `.gitignore`

```gitignore
.env
*.pkl
*.db
logs/
data/
__pycache__/
*.pyc
.DS_Store
node_modules/
```

### `scripts/backup_db.sh`

```bash
#!/bin/bash
# Ejecutar via cron: 0 2 * * * /home/pi/crypto-trader/scripts/backup_db.sh
BACKUP_DIR="/home/pi/backups/crypto-trader"
DB_PATH="/home/pi/crypto-trader/data/crypto_trader.db"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"
sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/crypto_trader_$DATE.db'"
# Mantener solo los últimos 7 backups
ls -t "$BACKUP_DIR"/*.db | tail -n +8 | xargs -r rm
echo "[$DATE] Backup completado: crypto_trader_$DATE.db"
```

---

## 2. `bot/config.py`

```python
"""Carga y validación de toda la configuración del bot desde variables de entorno."""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class CoinbaseConfig:
    api_key: str = field(default_factory=lambda: os.getenv("COINBASE_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("COINBASE_API_SECRET", ""))
    sandbox: bool = field(default_factory=lambda: os.getenv("COINBASE_SANDBOX", "false").lower() == "true")


@dataclass
class TradingConfig:
    mode: str = field(default_factory=lambda: os.getenv("TRADING_MODE", "demo"))
    pairs: list = field(default_factory=lambda: os.getenv("TRADING_PAIRS", "BTC-EUR,ETH-EUR").split(","))
    base_currency: str = field(default_factory=lambda: os.getenv("BASE_CURRENCY", "EUR"))
    demo_initial_balance: float = field(default_factory=lambda: float(os.getenv("DEMO_INITIAL_BALANCE", "1000.0")))
    analysis_interval: int = field(default_factory=lambda: int(os.getenv("ANALYSIS_INTERVAL_SECONDS", "300")))
    timeframe: str = field(default_factory=lambda: os.getenv("MODEL_TIMEFRAME", "5m"))
    candles_required: int = field(default_factory=lambda: int(os.getenv("MODEL_CANDLES_REQUIRED", "200")))

    def is_demo(self) -> bool:
        return self.mode == "demo"


@dataclass
class RiskConfig:
    max_risk_per_trade_pct: float = field(default_factory=lambda: float(os.getenv("MAX_RISK_PER_TRADE_PCT", "0.02")))
    max_open_positions: int = field(default_factory=lambda: int(os.getenv("MAX_OPEN_POSITIONS", "3")))
    max_portfolio_in_crypto_pct: float = field(default_factory=lambda: float(os.getenv("MAX_PORTFOLIO_IN_CRYPTO_PCT", "0.60")))
    min_confidence_threshold: float = field(default_factory=lambda: float(os.getenv("MIN_CONFIDENCE_THRESHOLD", "0.70")))
    stop_loss_atr_multiplier: float = field(default_factory=lambda: float(os.getenv("STOP_LOSS_ATR_MULTIPLIER", "1.5")))
    take_profit_atr_multiplier: float = field(default_factory=lambda: float(os.getenv("TAKE_PROFIT_ATR_MULTIPLIER", "3.0")))
    max_daily_trades: int = field(default_factory=lambda: int(os.getenv("MAX_DAILY_TRADES", "20")))
    high_volatility_atr_threshold: float = field(default_factory=lambda: float(os.getenv("HIGH_VOLATILITY_ATR_THRESHOLD", "0.05")))
    min_trade_eur: float = 10.0
    coinbase_taker_fee: float = 0.006  # 0.6% tarifa taker Coinbase plan básico


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
    coinbase: CoinbaseConfig = field(default_factory=CoinbaseConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    api: APIConfig = field(default_factory=APIConfig)
    log: LogConfig = field(default_factory=LogConfig)

    def validate(self) -> None:
        """Valida que la configuración crítica está presente."""
        if not self.trading.is_demo():
            if not self.coinbase.api_key or not self.coinbase.api_secret:
                raise ValueError("COINBASE_API_KEY y COINBASE_API_SECRET son obligatorios en modo real.")
        if self.trading.mode not in ("demo", "real"):
            raise ValueError(f"TRADING_MODE inválido: {self.trading.mode}. Debe ser 'demo' o 'real'.")


# Instancia global de configuración
config = AppConfig()
```

---

## 3. `bot/database/models.py` e `init_db.py`

### `bot/database/models.py`

```python
"""Modelos SQLAlchemy para la base de datos SQLite."""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, DateTime, Boolean, Text,
    ForeignKey, UniqueConstraint, Index, create_engine
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Candle(Base):
    __tablename__ = "candles"
    id = Column(Integer, primary_key=True, autoincrement=True)
    pair = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    __table_args__ = (
        UniqueConstraint("pair", "timeframe", "timestamp"),
        Index("idx_candles_pair_ts", "pair", "timeframe", "timestamp"),
    )


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    balance_eur = Column(Float, nullable=False)
    total_value_eur = Column(Float, nullable=False)
    total_pnl_eur = Column(Float, nullable=False)
    total_pnl_pct = Column(Float, nullable=False)
    positions_json = Column(Text, nullable=False, default="{}")


class Position(Base):
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    pair = Column(String(20), nullable=False)
    amount_crypto = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    entry_timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    stop_loss_price = Column(Float, nullable=False)
    take_profit_price = Column(Float, nullable=False)
    amount_eur_invested = Column(Float, nullable=False)
    status = Column(String(10), nullable=False, default="open")  # open | closed
    close_price = Column(Float, nullable=True)
    close_timestamp = Column(DateTime, nullable=True)
    pnl_eur = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    close_reason = Column(String(20), nullable=True)  # signal | stop_loss | take_profit | manual
    trades = relationship("Trade", back_populates="position")


class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(Integer, ForeignKey("positions.id"), nullable=True)
    pair = Column(String(20), nullable=False)
    side = Column(String(4), nullable=False)   # buy | sell
    amount_crypto = Column(Float, nullable=False)
    amount_eur = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    fee_eur = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    mode = Column(String(4), nullable=False)    # demo | real
    exchange_order_id = Column(String(100), nullable=True)
    position = relationship("Position", back_populates="trades")
    __table_args__ = (Index("idx_trades_timestamp", "timestamp"),)


class ModelDecision(Base):
    __tablename__ = "model_decisions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    pair = Column(String(20), nullable=False)
    signal = Column(String(4), nullable=False)   # BUY | SELL | HOLD
    confidence = Column(Float, nullable=False)
    prob_buy = Column(Float, nullable=False)
    prob_sell = Column(Float, nullable=False)
    prob_hold = Column(Float, nullable=False)
    executed = Column(Boolean, nullable=False, default=False)
    rejection_reason = Column(String(200), nullable=True)
    __table_args__ = (Index("idx_decisions_timestamp", "timestamp"),)


class BotConfig(Base):
    __tablename__ = "bot_config"
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SystemLog(Base):
    __tablename__ = "system_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    level = Column(String(10), nullable=False)
    module = Column(String(50), nullable=False)
    message = Column(Text, nullable=False)
    extra_json = Column(Text, nullable=True)
    __table_args__ = (Index("idx_logs_timestamp", "timestamp"),)
```

### `bot/database/init_db.py`

```python
"""Inicialización de la base de datos SQLite."""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from .models import Base
from config import config
from loguru import logger


def get_engine():
    os.makedirs(os.path.dirname(config.database.sqlite_path), exist_ok=True)
    return create_engine(
        f"sqlite:///{config.database.sqlite_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )


def init_db() -> sessionmaker:
    engine = get_engine()
    Base.metadata.create_all(engine)
    logger.info(f"Base de datos inicializada en {config.database.sqlite_path}")
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


SessionLocal = init_db()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

---

## 4. `bot/database/crud.py`

```python
"""Operaciones CRUD sobre la base de datos."""
import json
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from .models import Candle, PortfolioSnapshot, Position, Trade, ModelDecision, SystemLog, BotConfig


# ── CANDLES ──────────────────────────────────────────────────────────────────

def upsert_candles(db: Session, candles: list[dict]) -> int:
    """Inserta velas ignorando duplicados. Retorna número de velas insertadas."""
    inserted = 0
    for c in candles:
        exists = db.query(Candle).filter_by(
            pair=c["pair"], timeframe=c["timeframe"], timestamp=c["timestamp"]
        ).first()
        if not exists:
            db.add(Candle(**c))
            inserted += 1
    db.commit()
    return inserted


def get_candles(db: Session, pair: str, timeframe: str, limit: int = 500) -> list[Candle]:
    return (
        db.query(Candle)
        .filter_by(pair=pair, timeframe=timeframe)
        .order_by(desc(Candle.timestamp))
        .limit(limit)
        .all()
    )


# ── PORTFOLIO ─────────────────────────────────────────────────────────────────

def save_portfolio_snapshot(db: Session, snapshot: dict) -> PortfolioSnapshot:
    obj = PortfolioSnapshot(
        timestamp=datetime.utcnow(),
        balance_eur=snapshot["balance_eur"],
        total_value_eur=snapshot["total_value_eur"],
        total_pnl_eur=snapshot["total_pnl_eur"],
        total_pnl_pct=snapshot["total_pnl_pct"],
        positions_json=json.dumps(snapshot.get("positions", {})),
    )
    db.add(obj)
    db.commit()
    return obj


def get_portfolio_history(db: Session, days: int = 30) -> list[PortfolioSnapshot]:
    since = datetime.utcnow() - timedelta(days=days)
    return (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.timestamp >= since)
        .order_by(PortfolioSnapshot.timestamp)
        .all()
    )


# ── POSITIONS ─────────────────────────────────────────────────────────────────

def create_position(db: Session, position_data: dict) -> Position:
    pos = Position(**position_data)
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return pos


def get_open_positions(db: Session) -> list[Position]:
    return db.query(Position).filter_by(status="open").all()


def get_open_position_by_pair(db: Session, pair: str) -> Optional[Position]:
    return db.query(Position).filter_by(pair=pair, status="open").first()


def close_position(db: Session, position_id: int, close_price: float, reason: str) -> Position:
    pos = db.query(Position).get(position_id)
    pos.status = "closed"
    pos.close_price = close_price
    pos.close_timestamp = datetime.utcnow()
    pos.close_reason = reason
    pos.pnl_eur = (close_price - pos.entry_price) * pos.amount_crypto
    pos.pnl_pct = (close_price - pos.entry_price) / pos.entry_price * 100
    db.commit()
    return pos


# ── TRADES ────────────────────────────────────────────────────────────────────

def create_trade(db: Session, trade_data: dict) -> Trade:
    trade = Trade(**trade_data)
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return trade


def get_trades(db: Session, limit: int = 50, offset: int = 0) -> list[Trade]:
    return (
        db.query(Trade)
        .order_by(desc(Trade.timestamp))
        .offset(offset)
        .limit(limit)
        .all()
    )


def count_trades_today(db: Session) -> int:
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(func.count(Trade.id)).filter(Trade.timestamp >= today).scalar()


# ── MODEL DECISIONS ───────────────────────────────────────────────────────────

def save_decision(db: Session, decision: dict) -> ModelDecision:
    obj = ModelDecision(**decision)
    db.add(obj)
    db.commit()
    return obj


def get_recent_decisions(db: Session, limit: int = 50) -> list[ModelDecision]:
    return (
        db.query(ModelDecision)
        .order_by(desc(ModelDecision.timestamp))
        .limit(limit)
        .all()
    )


# ── STATS ─────────────────────────────────────────────────────────────────────

def get_stats_summary(db: Session) -> dict:
    total_trades = db.query(func.count(Trade.id)).scalar()
    closed_positions = db.query(Position).filter_by(status="closed").all()
    if not closed_positions:
        return {"total_trades": total_trades, "win_rate": 0, "avg_pnl_pct": 0, "total_pnl_eur": 0}

    winners = [p for p in closed_positions if p.pnl_eur and p.pnl_eur > 0]
    total_pnl = sum(p.pnl_eur for p in closed_positions if p.pnl_eur)
    avg_pnl_pct = sum(p.pnl_pct for p in closed_positions if p.pnl_pct) / len(closed_positions)

    return {
        "total_trades": total_trades,
        "closed_positions": len(closed_positions),
        "win_rate": len(winners) / len(closed_positions) * 100,
        "avg_pnl_pct": avg_pnl_pct,
        "total_pnl_eur": total_pnl,
    }


# ── SYSTEM LOGS ───────────────────────────────────────────────────────────────

def save_log(db: Session, level: str, module: str, message: str, extra: dict = None):
    obj = SystemLog(
        level=level, module=module, message=message,
        extra_json=json.dumps(extra) if extra else None,
    )
    db.add(obj)
    db.commit()


def get_logs(db: Session, level: Optional[str] = None, limit: int = 100) -> list[SystemLog]:
    q = db.query(SystemLog).order_by(desc(SystemLog.timestamp))
    if level:
        q = q.filter_by(level=level.upper())
    return q.limit(limit).all()
```

---

## 5. `bot/data/collector.py`

```python
"""Recolección de datos de mercado en tiempo real via WebSocket y REST."""
import asyncio
import json
from datetime import datetime
from typing import Optional
import ccxt.async_support as ccxt
import pandas as pd
import redis.asyncio as aioredis
from loguru import logger
from config import config
from database.crud import upsert_candles
from database.init_db import SessionLocal


class DataCollector:
    """
    Gestiona la conexión con Coinbase Advanced Trade.
    Almacena velas OHLCV en Redis (caché) y SQLite (histórico).
    Publica eventos en canal Redis 'new_candle' para el motor de trading.
    """

    REDIS_CANDLE_KEY = "candles:{pair}:{timeframe}"
    REDIS_PRICE_KEY = "price:{pair}"
    REDIS_CHANNEL = "new_candle"
    MAX_REDIS_CANDLES = 500

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
        self.exchange = self._build_exchange()
        self._running = False
        self._reconnect_delay = 5
        self._max_reconnect_delay = 300

    def _build_exchange(self) -> ccxt.coinbase:
        params = {
            "apiKey": config.coinbase.api_key,
            "secret": config.coinbase.api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
        if config.coinbase.sandbox:
            params["urls"] = {"api": {"public": "https://api-public.sandbox.exchange.coinbase.com"}}
        return ccxt.coinbase(params)

    async def start(self) -> None:
        self._running = True
        logger.info(f"Iniciando recolección de datos para pares: {config.trading.pairs}")
        await asyncio.gather(
            self._run_websocket_loop(),
            self._run_ohlcv_loop(),
        )

    async def stop(self) -> None:
        self._running = False
        await self.exchange.close()
        logger.info("DataCollector detenido.")

    async def _run_websocket_loop(self) -> None:
        """Loop de reconexión del WebSocket de tickers."""
        delay = self._reconnect_delay
        while self._running:
            try:
                await self._watch_tickers()
                delay = self._reconnect_delay
            except Exception as e:
                logger.warning(f"WebSocket desconectado: {e}. Reconectando en {delay}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._max_reconnect_delay)

    async def _watch_tickers(self) -> None:
        """Suscripción a precios en tiempo real via ccxt WebSocket."""
        while self._running:
            tickers = await self.exchange.watch_tickers(config.trading.pairs)
            for pair, ticker in tickers.items():
                price = ticker.get("last")
                if price:
                    await self.redis.set(
                        self.REDIS_PRICE_KEY.format(pair=pair),
                        str(price),
                        ex=60,  # TTL 60s
                    )
                    await self.redis.publish(
                        "price_update",
                        json.dumps({"pair": pair, "price": price, "timestamp": datetime.utcnow().isoformat()}),
                    )

    async def _run_ohlcv_loop(self) -> None:
        """Descarga periódica de velas OHLCV (cada cierre de vela)."""
        interval_seconds = self._timeframe_to_seconds(config.trading.timeframe)
        while self._running:
            for pair in config.trading.pairs:
                try:
                    await self._fetch_and_store_ohlcv(pair)
                except Exception as e:
                    logger.error(f"Error obteniendo OHLCV {pair}: {e}")
            await asyncio.sleep(interval_seconds)

    async def _fetch_and_store_ohlcv(self, pair: str) -> None:
        ohlcv = await self.exchange.fetch_ohlcv(
            pair, timeframe=config.trading.timeframe, limit=10
        )
        if not ohlcv:
            return

        candles_data = []
        for row in ohlcv:
            candles_data.append({
                "pair": pair,
                "timeframe": config.trading.timeframe,
                "timestamp": datetime.utcfromtimestamp(row[0] / 1000),
                "open": row[1], "high": row[2], "low": row[3],
                "close": row[4], "volume": row[5],
            })

        # Guardar en SQLite
        db = SessionLocal()
        try:
            upsert_candles(db, candles_data)
        finally:
            db.close()

        # Actualizar caché Redis con las últimas MAX_REDIS_CANDLES velas
        redis_key = self.REDIS_CANDLE_KEY.format(pair=pair, timeframe=config.trading.timeframe)
        pipe = self.redis.pipeline()
        for c in candles_data:
            pipe.rpush(redis_key, json.dumps({
                "timestamp": c["timestamp"].isoformat(),
                "open": c["open"], "high": c["high"],
                "low": c["low"], "close": c["close"], "volume": c["volume"],
            }))
        pipe.ltrim(redis_key, -self.MAX_REDIS_CANDLES, -1)
        await pipe.execute()

        # Publicar nueva vela al motor
        latest = candles_data[-1]
        await self.redis.publish(
            self.REDIS_CHANNEL,
            json.dumps({"pair": pair, "timestamp": latest["timestamp"].isoformat(), "close": latest["close"]}),
        )
        logger.debug(f"OHLCV actualizado: {pair} | close={latest['close']}")

    async def get_latest_candles(self, pair: str, limit: int = 200) -> pd.DataFrame:
        """Obtiene las últimas N velas desde Redis."""
        redis_key = self.REDIS_CANDLE_KEY.format(pair=pair, timeframe=config.trading.timeframe)
        raw = await self.redis.lrange(redis_key, -limit, -1)
        if not raw:
            return pd.DataFrame()

        rows = [json.loads(r) for r in raw]
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        return df

    async def get_current_price(self, pair: str) -> Optional[float]:
        """Obtiene el precio actual desde Redis."""
        val = await self.redis.get(self.REDIS_PRICE_KEY.format(pair=pair))
        return float(val) if val else None

    @staticmethod
    def _timeframe_to_seconds(tf: str) -> int:
        mapping = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
        return mapping.get(tf, 300)
```

---

## 6. `bot/data/historical.py`

```python
"""Descarga de datos históricos para inicialización del bot."""
import asyncio
from datetime import datetime, timedelta
import ccxt.async_support as ccxt
import pandas as pd
from loguru import logger
from config import config
from database.crud import upsert_candles
from database.init_db import SessionLocal


async def fetch_and_store_historical(pair: str, days: int = 90) -> int:
    """
    Descarga el histórico de los últimos N días para un par.
    Retorna el número de velas almacenadas.
    """
    exchange = ccxt.coinbase({
        "apiKey": config.coinbase.api_key,
        "secret": config.coinbase.api_secret,
        "enableRateLimit": True,
    })

    since = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    timeframe = config.trading.timeframe
    all_candles = []
    limit = 300  # máximo por request en Coinbase

    logger.info(f"Descargando histórico {pair} ({days} días, {timeframe})...")
    try:
        while True:
            ohlcv = await exchange.fetch_ohlcv(pair, timeframe=timeframe, since=since, limit=limit)
            if not ohlcv:
                break
            all_candles.extend(ohlcv)
            since = ohlcv[-1][0] + 1
            if len(ohlcv) < limit:
                break
            await asyncio.sleep(0.5)  # Respetar rate limit
    finally:
        await exchange.close()

    if not all_candles:
        logger.warning(f"No se obtuvieron datos históricos para {pair}")
        return 0

    candles_data = [{
        "pair": pair,
        "timeframe": timeframe,
        "timestamp": datetime.utcfromtimestamp(row[0] / 1000),
        "open": row[1], "high": row[2], "low": row[3],
        "close": row[4], "volume": row[5],
    } for row in all_candles]

    db = SessionLocal()
    try:
        inserted = upsert_candles(db, candles_data)
    finally:
        db.close()

    logger.info(f"Histórico {pair}: {inserted} velas nuevas almacenadas ({len(all_candles)} descargadas)")
    return inserted


async def initialize_historical_data(days: int = 90) -> None:
    """Inicializa el histórico para todos los pares configurados."""
    tasks = [fetch_and_store_historical(pair, days) for pair in config.trading.pairs]
    await asyncio.gather(*tasks)
```

---

## 7. `bot/indicators/technical.py`

```python
"""Cálculo de indicadores técnicos usando pandas-ta."""
import pandas as pd
import pandas_ta as ta
from loguru import logger


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Recibe DataFrame con columnas: timestamp, open, high, low, close, volume.
    Retorna el mismo DataFrame enriquecido con todos los indicadores.
    Requiere mínimo 200 filas para indicadores estables.
    """
    if len(df) < 50:
        logger.warning(f"Datos insuficientes para indicadores: {len(df)} velas (mínimo 50)")
        return df

    df = df.copy()

    # RSI
    df["rsi_14"] = ta.rsi(df["close"], length=14)

    # MACD
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        df["macd"] = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_hist"] = macd["MACDh_12_26_9"]

    # Bollinger Bands
    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None:
        df["bb_upper"] = bb["BBU_20_2.0"]
        df["bb_mid"] = bb["BBM_20_2.0"]
        df["bb_lower"] = bb["BBL_20_2.0"]
        df["bb_pct_b"] = bb["BBP_20_2.0"]
        df["bb_bandwidth"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

    # ATR
    df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    # Medias móviles
    df["ema_9"] = ta.ema(df["close"], length=9)
    df["ema_21"] = ta.ema(df["close"], length=21)
    df["sma_50"] = ta.sma(df["close"], length=50)
    df["sma_200"] = ta.sma(df["close"], length=200)

    # Stochastic
    stoch = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3)
    if stoch is not None:
        df["stoch_k"] = stoch["STOCHk_14_3_3"]
        df["stoch_d"] = stoch["STOCHd_14_3_3"]

    # Williams %R
    df["williams_r"] = ta.willr(df["high"], df["low"], df["close"], length=14)

    # CCI
    df["cci_20"] = ta.cci(df["high"], df["low"], df["close"], length=20)

    # OBV
    df["obv"] = ta.obv(df["close"], df["volume"])

    # Volumen relativo (ratio vs media 20 períodos)
    df["volume_sma_20"] = ta.sma(df["volume"], length=20)
    df["volume_ratio"] = df["volume"] / df["volume_sma_20"].replace(0, 1)

    return df


def get_atr(df: pd.DataFrame) -> float:
    """Retorna el ATR actual (última fila)."""
    if "atr_14" not in df.columns or df["atr_14"].isna().all():
        return 0.0
    return float(df["atr_14"].iloc[-1])


def get_current_price(df: pd.DataFrame) -> float:
    """Retorna el precio de cierre de la última vela."""
    return float(df["close"].iloc[-1])
```

---

## 8. `bot/indicators/features.py`

```python
"""Construcción del vector de features para el modelo de ML."""
import math
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np
from loguru import logger


class FeatureBuilder:
    """Transforma velas + indicadores en el vector de features para LightGBM."""

    MIN_ROWS = 55  # Mínimo para que los indicadores sean estables

    def build_features(self, df: pd.DataFrame) -> Optional[pd.Series]:
        """
        Recibe DataFrame con indicadores ya calculados.
        Retorna Series de features para la última vela, o None si hay datos insuficientes.
        """
        if len(df) < self.MIN_ROWS:
            logger.warning(f"Datos insuficientes para features: {len(df)} < {self.MIN_ROWS}")
            return None

        last = df.iloc[-1]
        prev3 = df.iloc[-4] if len(df) >= 4 else df.iloc[0]
        prev6 = df.iloc[-7] if len(df) >= 7 else df.iloc[0]

        price = last["close"]
        if price == 0:
            return None

        features = {}

        # ── Variaciones de precio ──────────────────────────────────────────
        features["price_change_1"] = self._safe_pct(price, df.iloc[-2]["close"] if len(df) >= 2 else price)
        features["price_change_3"] = self._safe_pct(price, prev3["close"])
        features["price_change_6"] = self._safe_pct(price, prev6["close"])

        # ── RSI ───────────────────────────────────────────────────────────
        features["rsi_14"] = self._safe_float(last.get("rsi_14"), 50.0) / 100.0
        rsi_slope = 0.0
        if len(df) >= 4 and "rsi_14" in df.columns:
            rsi_vals = df["rsi_14"].dropna()
            if len(rsi_vals) >= 4:
                rsi_slope = (rsi_vals.iloc[-1] - rsi_vals.iloc[-4]) / 100.0
        features["rsi_slope"] = rsi_slope

        # ── MACD ──────────────────────────────────────────────────────────
        features["macd"] = self._safe_float(last.get("macd"), 0.0) / (price + 1e-10)
        features["macd_signal"] = self._safe_float(last.get("macd_signal"), 0.0) / (price + 1e-10)
        features["macd_hist"] = self._safe_float(last.get("macd_hist"), 0.0) / (price + 1e-10)
        # Cruce MACD: 1 alcista, -1 bajista, 0 neutral
        if len(df) >= 2 and "macd" in df.columns and "macd_signal" in df.columns:
            prev_macd = df["macd"].iloc[-2]
            prev_sig = df["macd_signal"].iloc[-2]
            curr_macd = last.get("macd", 0)
            curr_sig = last.get("macd_signal", 0)
            if prev_macd < prev_sig and curr_macd >= curr_sig:
                features["macd_cross"] = 1.0
            elif prev_macd > prev_sig and curr_macd <= curr_sig:
                features["macd_cross"] = -1.0
            else:
                features["macd_cross"] = 0.0
        else:
            features["macd_cross"] = 0.0

        # ── Bollinger ─────────────────────────────────────────────────────
        features["bb_pct_b"] = self._safe_float(last.get("bb_pct_b"), 0.5)
        features["bb_bandwidth"] = self._safe_float(last.get("bb_bandwidth"), 0.0)

        # ── ATR ───────────────────────────────────────────────────────────
        atr = self._safe_float(last.get("atr_14"), 0.0)
        features["atr_pct"] = atr / price if price > 0 else 0.0

        # ── Medias móviles ────────────────────────────────────────────────
        features["price_vs_ema9"] = self._safe_ratio(price, last.get("ema_9", price))
        features["price_vs_ema21"] = self._safe_ratio(price, last.get("ema_21", price))
        features["price_vs_sma50"] = self._safe_ratio(price, last.get("sma_50", price))
        features["ema9_vs_ema21"] = self._safe_ratio(
            self._safe_float(last.get("ema_9"), price),
            self._safe_float(last.get("ema_21"), price)
        )

        # ── Volumen ───────────────────────────────────────────────────────
        features["volume_ratio"] = min(self._safe_float(last.get("volume_ratio"), 1.0), 10.0)
        if len(df) >= 2:
            prev_vol = df["volume"].iloc[-2]
            features["volume_change"] = self._safe_pct(last["volume"], prev_vol)
        else:
            features["volume_change"] = 0.0

        # OBV slope (tendencia últimas 5 velas)
        if "obv" in df.columns and len(df) >= 5:
            obv_vals = df["obv"].dropna()
            if len(obv_vals) >= 5:
                obv_slope = (obv_vals.iloc[-1] - obv_vals.iloc[-5]) / (abs(obv_vals.iloc[-5]) + 1e-10)
                features["obv_slope"] = max(min(obv_slope, 5.0), -5.0)
            else:
                features["obv_slope"] = 0.0
        else:
            features["obv_slope"] = 0.0

        # ── Osciladores ───────────────────────────────────────────────────
        features["stoch_k"] = self._safe_float(last.get("stoch_k"), 50.0) / 100.0
        features["stoch_d"] = self._safe_float(last.get("stoch_d"), 50.0) / 100.0
        features["williams_r"] = (self._safe_float(last.get("williams_r"), -50.0) + 100) / 100.0
        features["cci_20"] = max(min(self._safe_float(last.get("cci_20"), 0.0) / 200.0, 2.0), -2.0)

        # ── Contexto temporal ─────────────────────────────────────────────
        ts = last.get("timestamp", datetime.utcnow())
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        hour = ts.hour if hasattr(ts, "hour") else 0
        features["hour_sin"] = math.sin(hour * 2 * math.pi / 24)
        features["hour_cos"] = math.cos(hour * 2 * math.pi / 24)
        features["day_of_week"] = ts.weekday() / 6.0 if hasattr(ts, "weekday") else 0.0
        features["is_weekend"] = 1.0 if hasattr(ts, "weekday") and ts.weekday() >= 5 else 0.0

        # ── Régimen de volatilidad ────────────────────────────────────────
        atr_pct = features["atr_pct"]
        if atr_pct < 0.01:
            features["volatility_regime"] = 0.0   # Baja
        elif atr_pct < 0.03:
            features["volatility_regime"] = 0.5   # Media
        else:
            features["volatility_regime"] = 1.0   # Alta

        return pd.Series(features)

    def build_features_batch(self, df: pd.DataFrame) -> pd.DataFrame:
        """Para entrenamiento: construye features de todas las filas válidas."""
        feature_rows = []
        for i in range(self.MIN_ROWS, len(df)):
            subset = df.iloc[:i+1]
            row = self.build_features(subset)
            if row is not None:
                row["index"] = i
                feature_rows.append(row)
        return pd.DataFrame(feature_rows).set_index("index") if feature_rows else pd.DataFrame()

    @staticmethod
    def _safe_float(val, default: float = 0.0) -> float:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return default
        return float(val)

    @staticmethod
    def _safe_pct(current, previous) -> float:
        if not previous or previous == 0:
            return 0.0
        return (float(current) - float(previous)) / float(previous)

    @staticmethod
    def _safe_ratio(a, b) -> float:
        b = float(b) if b else 0
        if b == 0:
            return 1.0
        return float(a) / b - 1.0
```

---

## 9. `bot/model/predictor.py`

```python
"""Inferencia del modelo LightGBM entrenado."""
import os
import json
import pickle
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np
from loguru import logger
from config import config


class ModelPredictor:
    """Carga el modelo LightGBM y realiza inferencia de señales de trading."""

    SIGNAL_MAP = {0: "SELL", 1: "HOLD", 2: "BUY"}

    def __init__(self):
        self.model = None
        self.scaler = None
        self.metadata = {}
        self.feature_names = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(config.model.model_path):
            logger.warning(f"Modelo no encontrado en {config.model.model_path}. El bot funcionará en modo espera.")
            return
        try:
            with open(config.model.model_path, "rb") as f:
                self.model = pickle.load(f)
            with open(config.model.scaler_path, "rb") as f:
                self.scaler = pickle.load(f)

            metadata_path = config.model.model_path.replace(".pkl", "_metadata.json")
            if os.path.exists(metadata_path):
                with open(metadata_path) as f:
                    self.metadata = json.load(f)
                self.feature_names = self.metadata.get("feature_names", [])

            logger.info(f"Modelo cargado. Entrenado: {self.metadata.get('trained_at', 'desconocido')}")
            logger.info(f"Métricas validación: {self.metadata.get('validation_metrics', {})}")
        except Exception as e:
            logger.error(f"Error cargando modelo: {e}")
            self.model = None

    def is_model_loaded(self) -> bool:
        return self.model is not None and self.scaler is not None

    def predict(self, features: pd.Series) -> Optional[dict]:
        """
        Realiza predicción para el vector de features dado.
        Retorna dict con signal, confidence y probabilidades, o None si el modelo no está cargado.
        """
        if not self.is_model_loaded():
            return None

        try:
            # Alinear features con las del entrenamiento
            if self.feature_names:
                feature_vector = []
                for name in self.feature_names:
                    feature_vector.append(features.get(name, 0.0))
                X = np.array([feature_vector])
            else:
                X = features.values.reshape(1, -1)

            # Escalar
            X_scaled = self.scaler.transform(X)

            # Predecir probabilidades
            probs = self.model.predict_proba(X_scaled)[0]

            # El modelo tiene clases 0=SELL, 1=HOLD, 2=BUY
            # Asegurarse de que el orden coincide con SIGNAL_MAP
            class_order = self.model.classes_
            prob_dict = {self.SIGNAL_MAP[int(c)]: float(p) for c, p in zip(class_order, probs)}

            # Señal ganadora
            best_class = int(class_order[np.argmax(probs)])
            signal = self.SIGNAL_MAP[best_class]
            confidence = float(np.max(probs))

            # Si la confianza es baja, forzar HOLD
            if confidence < config.risk.min_confidence_threshold:
                signal = "HOLD"

            return {
                "signal": signal,
                "confidence": confidence,
                "probabilities": prob_dict,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Error en predicción: {e}")
            return None

    def reload_if_updated(self) -> None:
        """Recarga el modelo si el archivo ha sido actualizado."""
        if not os.path.exists(config.model.model_path):
            return
        mtime = os.path.getmtime(config.model.model_path)
        if mtime > self.metadata.get("_file_mtime", 0):
            logger.info("Modelo actualizado detectado, recargando...")
            self._load()
            self.metadata["_file_mtime"] = mtime

    def get_model_metadata(self) -> dict:
        return {
            "loaded": self.is_model_loaded(),
            "trained_at": self.metadata.get("trained_at"),
            "validation_metrics": self.metadata.get("validation_metrics", {}),
            "feature_count": len(self.feature_names),
            "model_path": config.model.model_path,
        }
```

---

## 10. `bot/trading/risk_manager.py`

```python
"""Gestión de riesgo: filtro final antes de ejecutar cualquier operación."""
from datetime import datetime
from loguru import logger
from config import config
from database.crud import count_trades_today, get_open_positions
from database.init_db import SessionLocal


class RiskManager:
    """
    Evalúa si una señal del modelo puede convertirse en operación real.
    Aplica todas las reglas de gestión de riesgo configuradas.
    """

    def can_buy(
        self,
        pair: str,
        signal: dict,
        portfolio: dict,
        current_price: float,
        atr: float,
    ) -> tuple[bool, str, float]:
        """
        Evalúa si se puede abrir una posición de compra.
        Retorna: (puede_comprar, razón, importe_eur_a_invertir)
        """
        # 1. Confianza del modelo
        if signal.get("signal") != "BUY":
            return False, f"Señal no es BUY: {signal.get('signal')}", 0.0

        if signal.get("confidence", 0) < config.risk.min_confidence_threshold:
            return False, f"Confianza insuficiente: {signal['confidence']:.2%} < {config.risk.min_confidence_threshold:.2%}", 0.0

        # 2. Verificar posición ya abierta en el par
        db = SessionLocal()
        try:
            open_positions = get_open_positions(db)
        finally:
            db.close()

        pairs_with_positions = [p.pair for p in open_positions]
        if pair in pairs_with_positions:
            return False, f"Ya existe posición abierta en {pair}", 0.0

        # 3. Máximo de posiciones abiertas
        if len(open_positions) >= config.risk.max_open_positions:
            return False, f"Máximo de posiciones abiertas alcanzado ({config.risk.max_open_positions})", 0.0

        # 4. Máximo de trades diarios
        db = SessionLocal()
        try:
            trades_today = count_trades_today(db)
        finally:
            db.close()

        if trades_today >= config.risk.max_daily_trades:
            return False, f"Máximo de trades diarios alcanzado ({config.risk.max_daily_trades})", 0.0

        # 5. Régimen de alta volatilidad
        atr_pct = atr / current_price if current_price > 0 else 0
        if atr_pct > config.risk.high_volatility_atr_threshold:
            return False, f"Alta volatilidad (ATR%={atr_pct:.2%} > {config.risk.high_volatility_atr_threshold:.2%})", 0.0

        # 6. Balance EUR suficiente
        balance_eur = portfolio.get("balance_eur", 0)
        if balance_eur < config.risk.min_trade_eur:
            return False, f"Balance EUR insuficiente: {balance_eur:.2f} < {config.risk.min_trade_eur}", 0.0

        # 7. Límite de exposición total en crypto
        total_value = portfolio.get("total_value_eur", balance_eur)
        crypto_value = total_value - balance_eur
        if total_value > 0 and crypto_value / total_value >= config.risk.max_portfolio_in_crypto_pct:
            return False, f"Exposición en crypto al máximo: {crypto_value/total_value:.0%}", 0.0

        # 8. Calcular tamaño de posición
        amount_eur = self.calculate_position_size(total_value, atr, current_price, balance_eur)
        if amount_eur < config.risk.min_trade_eur:
            return False, f"Tamaño de posición calculado demasiado pequeño: {amount_eur:.2f}€", 0.0

        logger.info(f"✅ Risk OK para compra {pair}: {amount_eur:.2f}€ | confianza={signal['confidence']:.2%}")
        return True, "OK", amount_eur

    def can_sell(
        self,
        pair: str,
        position,
        signal: dict,
        current_price: float,
    ) -> tuple[bool, str]:
        """
        Evalúa si se debe cerrar una posición.
        Retorna: (debe_vender, razón)
        """
        # Stop-loss
        if current_price <= position.stop_loss_price:
            loss_pct = (current_price - position.entry_price) / position.entry_price * 100
            return True, f"stop_loss (precio={current_price:.2f} <= SL={position.stop_loss_price:.2f}, pérdida={loss_pct:.2f}%)"

        # Take-profit
        if current_price >= position.take_profit_price:
            gain_pct = (current_price - position.entry_price) / position.entry_price * 100
            return True, f"take_profit (precio={current_price:.2f} >= TP={position.take_profit_price:.2f}, ganancia={gain_pct:.2f}%)"

        # Señal SELL del modelo
        if signal.get("signal") == "SELL" and signal.get("confidence", 0) >= config.risk.min_confidence_threshold:
            return True, f"signal (SELL con confianza={signal['confidence']:.2%})"

        return False, "mantener posición"

    def calculate_position_size(
        self,
        portfolio_value: float,
        atr: float,
        current_price: float,
        available_balance: float,
    ) -> float:
        """
        Calcula el importe en EUR a invertir usando sizing basado en riesgo.
        Fórmula: risk_amount / (ATR_pct * stop_loss_multiplier)
        """
        risk_amount = portfolio_value * config.risk.max_risk_per_trade_pct

        if atr > 0 and current_price > 0:
            atr_pct = atr / current_price
            stop_distance_pct = atr_pct * config.risk.stop_loss_atr_multiplier
            if stop_distance_pct > 0:
                position_size = risk_amount / stop_distance_pct
            else:
                position_size = risk_amount * 5
        else:
            position_size = risk_amount * 5

        # No superar el 20% del portafolio en una sola posición
        max_position = portfolio_value * 0.20
        position_size = min(position_size, max_position)

        # No superar el balance disponible
        position_size = min(position_size, available_balance * 0.95)

        return round(position_size, 2)

    def calculate_stop_loss(self, entry_price: float, atr: float) -> float:
        return round(entry_price - (atr * config.risk.stop_loss_atr_multiplier), 8)

    def calculate_take_profit(self, entry_price: float, atr: float) -> float:
        return round(entry_price + (atr * config.risk.take_profit_atr_multiplier), 8)
```

---

## 11. `bot/trading/portfolio.py`

```python
"""Estado y cálculos del portafolio."""
import json
from datetime import datetime
import redis
from loguru import logger
from config import config

REDIS_PORTFOLIO_KEY = "portfolio:state"


class Portfolio:
    """Gestiona el estado en memoria del portafolio (sincronizado con Redis)."""

    def __init__(self, redis_client: redis.Redis, initial_balance: float = None):
        self.redis = redis_client
        self._state = self._load_or_init(initial_balance or config.trading.demo_initial_balance)

    def _load_or_init(self, initial_balance: float) -> dict:
        raw = self.redis.get(REDIS_PORTFOLIO_KEY)
        if raw:
            return json.loads(raw)
        state = {
            "balance_eur": initial_balance,
            "initial_balance_eur": initial_balance,
            "positions": {},
            "total_value_eur": initial_balance,
            "total_pnl_eur": 0.0,
            "total_pnl_pct": 0.0,
            "created_at": datetime.utcnow().isoformat(),
        }
        self._save(state)
        return state

    def _save(self, state: dict) -> None:
        self.redis.set(REDIS_PORTFOLIO_KEY, json.dumps(state))
        self._state = state

    def get(self) -> dict:
        return self._state.copy()

    def update_balance(self, delta_eur: float) -> None:
        self._state["balance_eur"] = round(self._state["balance_eur"] + delta_eur, 4)
        self._save(self._state)

    def add_position(self, pair: str, position_data: dict) -> None:
        self._state["positions"][pair] = position_data
        self._save(self._state)

    def remove_position(self, pair: str) -> None:
        self._state["positions"].pop(pair, None)
        self._save(self._state)

    def update_valuations(self, current_prices: dict) -> dict:
        """Recalcula el valor total del portafolio con precios actuales."""
        crypto_value = 0.0
        for pair, pos in self._state["positions"].items():
            price = current_prices.get(pair, pos.get("entry_price", 0))
            pos["current_price"] = price
            pos["current_value_eur"] = pos["amount_crypto"] * price
            pos["pnl_eur"] = pos["current_value_eur"] - pos["amount_eur_invested"]
            pos["pnl_pct"] = pos["pnl_eur"] / pos["amount_eur_invested"] * 100 if pos["amount_eur_invested"] > 0 else 0
            crypto_value += pos["current_value_eur"]

        total = self._state["balance_eur"] + crypto_value
        initial = self._state["initial_balance_eur"]
        self._state["total_value_eur"] = round(total, 4)
        self._state["total_pnl_eur"] = round(total - initial, 4)
        self._state["total_pnl_pct"] = round((total - initial) / initial * 100, 4) if initial > 0 else 0
        self._save(self._state)
        return self._state
```

---

## 12. `bot/trading/demo_trader.py`

```python
"""Motor de trading simulado (modo demo)."""
from datetime import datetime
from loguru import logger
from config import config
from database import crud
from database.init_db import SessionLocal
from trading.portfolio import Portfolio
from trading.risk_manager import RiskManager


class DemoTrader:
    """Ejecuta operaciones de compra/venta simuladas con datos reales del mercado."""

    def __init__(self, portfolio: Portfolio, risk_manager: RiskManager):
        self.portfolio = portfolio
        self.risk = risk_manager

    def execute_buy(self, pair: str, amount_eur: float, current_price: float, atr: float) -> dict:
        """Simula una compra. Retorna el dict del trade creado."""
        fee = amount_eur * config.risk.coinbase_taker_fee
        net_eur = amount_eur - fee
        amount_crypto = net_eur / current_price
        stop_loss = self.risk.calculate_stop_loss(current_price, atr)
        take_profit = self.risk.calculate_take_profit(current_price, atr)

        # Actualizar portafolio
        self.portfolio.update_balance(-amount_eur)
        self.portfolio.add_position(pair, {
            "amount_crypto": amount_crypto,
            "entry_price": current_price,
            "amount_eur_invested": net_eur,
            "stop_loss_price": stop_loss,
            "take_profit_price": take_profit,
            "entry_timestamp": datetime.utcnow().isoformat(),
        })

        # Guardar posición en DB
        db = SessionLocal()
        try:
            position = crud.create_position(db, {
                "pair": pair,
                "amount_crypto": amount_crypto,
                "entry_price": current_price,
                "stop_loss_price": stop_loss,
                "take_profit_price": take_profit,
                "amount_eur_invested": net_eur,
            })
            trade = crud.create_trade(db, {
                "position_id": position.id,
                "pair": pair,
                "side": "buy",
                "amount_crypto": amount_crypto,
                "amount_eur": amount_eur,
                "price": current_price,
                "fee_eur": fee,
                "mode": "demo",
            })
        finally:
            db.close()

        result = {
            "trade_id": trade.id,
            "position_id": position.id,
            "pair": pair,
            "side": "buy",
            "amount_eur": amount_eur,
            "amount_crypto": amount_crypto,
            "price": current_price,
            "fee_eur": fee,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "timestamp": datetime.utcnow().isoformat(),
            "mode": "demo",
        }
        logger.info(f"🟢 [DEMO] COMPRA {pair}: {amount_crypto:.8f} @ {current_price:.2f}€ (inv={amount_eur:.2f}€, SL={stop_loss:.2f}, TP={take_profit:.2f})")
        return result

    def execute_sell(self, pair: str, position, current_price: float, reason: str) -> dict:
        """Simula una venta/cierre de posición."""
        amount_crypto = position.amount_crypto
        gross_eur = amount_crypto * current_price
        fee = gross_eur * config.risk.coinbase_taker_fee
        net_eur = gross_eur - fee
        pnl_eur = net_eur - position.amount_eur_invested

        # Actualizar portafolio
        self.portfolio.update_balance(net_eur)
        self.portfolio.remove_position(pair)

        # Cerrar posición en DB
        db = SessionLocal()
        try:
            closed = crud.close_position(db, position.id, current_price, reason)
            trade = crud.create_trade(db, {
                "position_id": position.id,
                "pair": pair,
                "side": "sell",
                "amount_crypto": amount_crypto,
                "amount_eur": gross_eur,
                "price": current_price,
                "fee_eur": fee,
                "mode": "demo",
            })
        finally:
            db.close()

        emoji = "🔴" if pnl_eur < 0 else "💚"
        logger.info(f"{emoji} [DEMO] VENTA {pair}: {amount_crypto:.8f} @ {current_price:.2f}€ | PnL={pnl_eur:+.2f}€ | razón={reason}")
        return {
            "trade_id": trade.id,
            "pair": pair,
            "side": "sell",
            "amount_crypto": amount_crypto,
            "amount_eur": gross_eur,
            "price": current_price,
            "fee_eur": fee,
            "pnl_eur": pnl_eur,
            "pnl_pct": pnl_eur / position.amount_eur_invested * 100,
            "close_reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
            "mode": "demo",
        }
```

---

## 13. `bot/trading/real_trader.py`

```python
"""Motor de trading real via Coinbase Advanced Trade API."""
import asyncio
from datetime import datetime
import ccxt.async_support as ccxt
from loguru import logger
from config import config
from database import crud
from database.init_db import SessionLocal
from trading.portfolio import Portfolio
from trading.risk_manager import RiskManager


class RealTrader:
    """
    Ejecuta operaciones reales en Coinbase Advanced Trade.
    ADVERTENCIA: Opera con dinero real. Usar solo tras validación exhaustiva en demo.
    """

    MAX_RETRIES = 3
    RETRY_DELAY = 5

    def __init__(self, portfolio: Portfolio, risk_manager: RiskManager):
        self.portfolio = portfolio
        self.risk = risk_manager
        self._consecutive_errors = 0
        self._circuit_open = False
        self.exchange = ccxt.coinbase({
            "apiKey": config.coinbase.api_key,
            "secret": config.coinbase.api_secret,
            "enableRateLimit": True,
        })

    def _check_circuit_breaker(self) -> None:
        if self._circuit_open:
            raise RuntimeError("Circuit breaker abierto. Bot pausado por errores consecutivos. Revisión manual requerida.")

    async def execute_buy(self, pair: str, amount_eur: float, current_price: float, atr: float) -> dict:
        self._check_circuit_breaker()
        amount_crypto = (amount_eur * (1 - config.risk.coinbase_taker_fee)) / current_price

        for attempt in range(self.MAX_RETRIES):
            try:
                order = await self.exchange.create_market_buy_order(
                    pair, amount_crypto, params={"quoteOrderQty": amount_eur}
                )
                self._consecutive_errors = 0
                filled_price = order.get("average", current_price)
                filled_amount = order.get("filled", amount_crypto)
                fee_eur = order.get("fee", {}).get("cost", amount_eur * config.risk.coinbase_taker_fee)

                stop_loss = self.risk.calculate_stop_loss(filled_price, atr)
                take_profit = self.risk.calculate_take_profit(filled_price, atr)

                db = SessionLocal()
                try:
                    position = crud.create_position(db, {
                        "pair": pair,
                        "amount_crypto": filled_amount,
                        "entry_price": filled_price,
                        "stop_loss_price": stop_loss,
                        "take_profit_price": take_profit,
                        "amount_eur_invested": amount_eur - fee_eur,
                    })
                    trade = crud.create_trade(db, {
                        "position_id": position.id,
                        "pair": pair,
                        "side": "buy",
                        "amount_crypto": filled_amount,
                        "amount_eur": amount_eur,
                        "price": filled_price,
                        "fee_eur": fee_eur,
                        "mode": "real",
                        "exchange_order_id": order.get("id"),
                    })
                finally:
                    db.close()

                logger.info(f"🟢 [REAL] COMPRA {pair}: {filled_amount:.8f} @ {filled_price:.2f}€")
                return {"trade_id": trade.id, "position_id": position.id, "pair": pair, "price": filled_price, "mode": "real"}

            except ccxt.InsufficientFunds as e:
                logger.error(f"Fondos insuficientes para compra {pair}: {e}")
                return None
            except Exception as e:
                self._consecutive_errors += 1
                logger.error(f"Error en compra {pair} (intento {attempt+1}): {e}")
                if self._consecutive_errors >= 3:
                    self._circuit_open = True
                    logger.critical("🔴 Circuit breaker activado. Bot detenido.")
                    raise RuntimeError("Circuit breaker activado") from e
                await asyncio.sleep(self.RETRY_DELAY)
        return None

    async def execute_sell(self, pair: str, position, current_price: float, reason: str) -> dict:
        self._check_circuit_breaker()
        for attempt in range(self.MAX_RETRIES):
            try:
                order = await self.exchange.create_market_sell_order(pair, position.amount_crypto)
                self._consecutive_errors = 0
                filled_price = order.get("average", current_price)
                gross_eur = position.amount_crypto * filled_price
                fee_eur = order.get("fee", {}).get("cost", gross_eur * config.risk.coinbase_taker_fee)
                pnl_eur = (gross_eur - fee_eur) - position.amount_eur_invested

                db = SessionLocal()
                try:
                    crud.close_position(db, position.id, filled_price, reason)
                    trade = crud.create_trade(db, {
                        "position_id": position.id,
                        "pair": pair,
                        "side": "sell",
                        "amount_crypto": position.amount_crypto,
                        "amount_eur": gross_eur,
                        "price": filled_price,
                        "fee_eur": fee_eur,
                        "mode": "real",
                        "exchange_order_id": order.get("id"),
                    })
                finally:
                    db.close()

                logger.info(f"{'💚' if pnl_eur >= 0 else '🔴'} [REAL] VENTA {pair} @ {filled_price:.2f}€ | PnL={pnl_eur:+.2f}€ | {reason}")
                return {"trade_id": trade.id, "pair": pair, "pnl_eur": pnl_eur, "mode": "real"}
            except Exception as e:
                self._consecutive_errors += 1
                logger.error(f"Error en venta {pair} (intento {attempt+1}): {e}")
                await asyncio.sleep(self.RETRY_DELAY)
        return None

    async def close(self):
        await self.exchange.close()
```

---

## 14. `bot/trading/engine.py`

```python
"""Orquestador principal del ciclo de análisis y trading."""
import asyncio
import json
from datetime import datetime
from loguru import logger
import redis.asyncio as aioredis
from config import config
from data.collector import DataCollector
from indicators.technical import calculate_indicators, get_atr, get_current_price
from indicators.features import FeatureBuilder
from model.predictor import ModelPredictor
from trading.risk_manager import RiskManager
from trading.portfolio import Portfolio
from trading.demo_trader import DemoTrader
from trading.real_trader import RealTrader
from notifications.telegram import TelegramNotifier
from database.crud import (
    save_decision, save_portfolio_snapshot, get_open_position_by_pair, get_open_positions
)
from database.init_db import SessionLocal


class TradingEngine:
    """Ciclo principal de análisis y ejecución de señales."""

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
        self.collector = DataCollector(redis_client)
        self.feature_builder = FeatureBuilder()
        self.predictor = ModelPredictor()
        self.risk_manager = RiskManager()
        self.portfolio = Portfolio(redis_client)
        self.telegram = TelegramNotifier()
        self._running = False
        self._status = "stopped"

        # Seleccionar trader según modo
        if config.trading.is_demo():
            self.trader = DemoTrader(self.portfolio, self.risk_manager)
            logger.info("Modo DEMO activado — no se realizarán operaciones reales.")
        else:
            self.trader = RealTrader(self.portfolio, self.risk_manager)
            logger.warning("⚠️  Modo REAL activado — se operará con dinero real.")

    async def start(self) -> None:
        self._running = True
        self._status = "running"
        await self._publish_status()
        logger.info(f"Motor de trading iniciado. Intervalo: {config.trading.analysis_interval}s")
        await asyncio.gather(
            self.collector.start(),
            self._analysis_loop(),
        )

    async def stop(self) -> None:
        self._running = False
        self._status = "stopped"
        await self.collector.stop()
        await self._publish_status()
        logger.info("Motor de trading detenido.")

    async def _analysis_loop(self) -> None:
        """Ciclo periódico de análisis y decisión."""
        # Esperar a que haya datos suficientes
        await asyncio.sleep(30)

        while self._running:
            start_time = asyncio.get_event_loop().time()

            for pair in config.trading.pairs:
                try:
                    await self._analyze_pair(pair)
                except Exception as e:
                    logger.error(f"Error analizando {pair}: {e}")
                    self._status = "error"

            # Guardar snapshot del portafolio
            await self._save_portfolio_snapshot()
            await self._publish_status()

            # Recargar modelo si fue actualizado
            self.predictor.reload_if_updated()

            elapsed = asyncio.get_event_loop().time() - start_time
            sleep_time = max(0, config.trading.analysis_interval - elapsed)
            logger.debug(f"Ciclo completado en {elapsed:.1f}s. Siguiente en {sleep_time:.0f}s.")
            await asyncio.sleep(sleep_time)

    async def _analyze_pair(self, pair: str) -> None:
        """Análisis completo de un par: datos → indicadores → features → señal → ejecución."""
        # 1. Obtener velas
        candles = await self.collector.get_latest_candles(pair, limit=config.trading.candles_required)
        if candles.empty or len(candles) < 55:
            logger.debug(f"{pair}: datos insuficientes ({len(candles)} velas)")
            return

        # 2. Indicadores técnicos
        candles_with_indicators = calculate_indicators(candles)
        atr = get_atr(candles_with_indicators)
        current_price = await self.collector.get_current_price(pair) or get_current_price(candles_with_indicators)

        # 3. Features
        features = self.feature_builder.build_features(candles_with_indicators)
        if features is None:
            return

        # 4. Predicción del modelo
        if not self.predictor.is_model_loaded():
            logger.debug(f"{pair}: modelo no cargado, usando señal HOLD")
            signal = {"signal": "HOLD", "confidence": 0.0, "probabilities": {"BUY": 0.0, "SELL": 0.0, "HOLD": 1.0}}
        else:
            signal = self.predictor.predict(features)
            if signal is None:
                return

        logger.info(f"📊 {pair} | precio={current_price:.2f}€ | señal={signal['signal']} ({signal['confidence']:.0%}) | ATR={atr:.2f}")

        # Publicar señal al WebSocket
        await self.redis.publish("bot:live_updates", json.dumps({
            "type": "signal",
            "data": {**signal, "pair": pair, "price": current_price, "atr": atr,
                     "atr_pct": atr / current_price if current_price > 0 else 0}
        }))

        # 5. Verificar posiciones abiertas para este par (gestión de salida)
        db = SessionLocal()
        try:
            open_position = get_open_position_by_pair(db, pair)
        finally:
            db.close()

        executed = False
        rejection_reason = None

        if open_position:
            # Evaluar si cerrar posición
            should_sell, sell_reason = self.risk_manager.can_sell(pair, open_position, signal, current_price)
            if should_sell:
                trade = self.trader.execute_sell(pair, open_position, current_price, sell_reason)
                if trade:
                    executed = True
                    await self.redis.publish("bot:live_updates", json.dumps({"type": "trade_executed", "data": trade}))
                    await self.telegram.notify_position_closed(trade, trade.get("pnl_eur", 0))
        else:
            # Evaluar si abrir posición
            portfolio_state = self.portfolio.get()
            # Actualizar valoraciones con precio actual
            prices = {p: await self.collector.get_current_price(p) or 0 for p in config.trading.pairs}
            portfolio_state = self.portfolio.update_valuations(prices)

            can_buy, reason, amount_eur = self.risk_manager.can_buy(
                pair, signal, portfolio_state, current_price, atr
            )
            if can_buy:
                if config.trading.is_demo():
                    trade = self.trader.execute_buy(pair, amount_eur, current_price, atr)
                else:
                    trade = await self.trader.execute_buy(pair, amount_eur, current_price, atr)

                if trade:
                    executed = True
                    await self.redis.publish("bot:live_updates", json.dumps({"type": "trade_executed", "data": trade}))
                    await self.telegram.notify_trade(trade)
            else:
                rejection_reason = reason
                if signal["signal"] != "HOLD":
                    logger.debug(f"  ↳ Señal {signal['signal']} rechazada: {reason}")

        # 6. Guardar decisión en DB
        db = SessionLocal()
        try:
            save_decision(db, {
                "pair": pair,
                "signal": signal["signal"],
                "confidence": signal["confidence"],
                "prob_buy": signal["probabilities"].get("BUY", 0),
                "prob_sell": signal["probabilities"].get("SELL", 0),
                "prob_hold": signal["probabilities"].get("HOLD", 1),
                "executed": executed,
                "rejection_reason": rejection_reason,
            })
        finally:
            db.close()

    async def _save_portfolio_snapshot(self) -> None:
        """Guarda snapshot del portafolio actual en DB."""
        prices = {}
        for pair in config.trading.pairs:
            price = await self.collector.get_current_price(pair)
            if price:
                prices[pair] = price
        state = self.portfolio.update_valuations(prices)

        db = SessionLocal()
        try:
            save_portfolio_snapshot(db, state)
        finally:
            db.close()

        await self.redis.publish("bot:live_updates", json.dumps({"type": "portfolio_update", "data": state}))

    async def _publish_status(self) -> None:
        await self.redis.set("bot:status", json.dumps({
            "status": self._status,
            "mode": config.trading.mode,
            "pairs": config.trading.pairs,
            "model_loaded": self.predictor.is_model_loaded(),
            "last_update": datetime.utcnow().isoformat(),
        }))
        await self.redis.publish("bot:live_updates", json.dumps({
            "type": "bot_status",
            "data": {"status": self._status, "mode": config.trading.mode}
        }))
```

---

## 15. `bot/notifications/telegram.py`

```python
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
```

---

## 16. `bot/scheduler/jobs.py`

```python
"""Tareas programadas con APScheduler."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from notifications.telegram import TelegramNotifier
from database.crud import get_stats_summary
from database.init_db import SessionLocal
import redis.asyncio as aioredis
import json


def setup_scheduler(redis_client: aioredis.Redis) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    notifier = TelegramNotifier()

    @scheduler.scheduled_job(CronTrigger(hour=8, minute=0))  # 8:00 UTC (10:00 España)
    async def daily_summary():
        logger.info("Ejecutando resumen diario...")
        db = SessionLocal()
        try:
            stats = get_stats_summary(db)
        finally:
            db.close()

        raw = await redis_client.get("portfolio:state")
        portfolio = json.loads(raw) if raw else {}
        await notifier.send_daily_summary(portfolio, stats)

    @scheduler.scheduled_job("interval", hours=6)
    async def cleanup_old_logs():
        """Elimina logs de sistema de más de 7 días para ahorrar espacio en disco."""
        from datetime import datetime, timedelta
        from sqlalchemy import delete
        from database.models import SystemLog
        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=7)
            db.execute(delete(SystemLog).where(SystemLog.timestamp < cutoff))
            db.commit()
            logger.debug("Limpieza de logs completada.")
        finally:
            db.close()

    return scheduler
```

---

## 17. `bot/main.py`

```python
"""Punto de entrada principal del bot."""
import asyncio
import sys
import os
import redis.asyncio as aioredis
from loguru import logger
from config import config
from database.init_db import init_db
from data.historical import initialize_historical_data
from trading.engine import TradingEngine
from scheduler.jobs import setup_scheduler


def setup_logging():
    logger.remove()
    logger.add(sys.stderr, level=config.log.level, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan> | {message}")
    os.makedirs(os.path.dirname(config.log.file), exist_ok=True)
    logger.add(config.log.file, level=config.log.level, rotation=f"{config.log.max_size} MB",
               retention=config.log.backup_count, serialize=True)


async def main():
    setup_logging()
    logger.info("=" * 60)
    logger.info(f"🤖 Crypto Trader Bot arrancando...")
    logger.info(f"   Modo: {config.trading.mode.upper()}")
    logger.info(f"   Pares: {', '.join(config.trading.pairs)}")
    logger.info(f"   Timeframe: {config.trading.timeframe}")
    logger.info("=" * 60)

    # Validar configuración
    config.validate()

    # Inicializar base de datos
    init_db()

    # Conectar Redis
    redis_client = aioredis.from_url(
        f"redis://{config.database.redis_host}:{config.database.redis_port}/{config.database.redis_db}",
        decode_responses=True,
    )
    await redis_client.ping()
    logger.info("Redis conectado.")

    # Descargar datos históricos
    logger.info("Descargando datos históricos (puede tardar varios minutos)...")
    await initialize_historical_data(days=90)

    # Iniciar scheduler
    scheduler = setup_scheduler(redis_client)
    scheduler.start()
    logger.info("Scheduler iniciado.")

    # Iniciar motor de trading
    engine = TradingEngine(redis_client)
    try:
        await engine.start()
    except KeyboardInterrupt:
        logger.info("Interrupción por teclado. Deteniendo bot...")
    finally:
        await engine.stop()
        scheduler.shutdown()
        await redis_client.close()
        logger.info("Bot detenido correctamente.")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 18. `api/main.py` y routers

### `api/main.py`

```python
"""FastAPI backend — REST API y WebSocket para el dashboard."""
import json
import secrets
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import redis.asyncio as aioredis
from config import config
from routers import portfolio, trades, market, bot, logs

app = FastAPI(title="Crypto Trader Bot API", version="1.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

security = HTTPBasic()


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username.encode(), config.api.username.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), config.api.password.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            headers={"WWW-Authenticate": "Basic"})
    return credentials.username


redis_client: aioredis.Redis = None


@app.on_event("startup")
async def startup():
    global redis_client
    redis_client = aioredis.from_url(
        f"redis://{config.database.redis_host}:{config.database.redis_port}",
        decode_responses=True,
    )


@app.on_event("shutdown")
async def shutdown():
    await redis_client.close()


def get_redis():
    return redis_client


# Incluir routers
app.include_router(portfolio.router, prefix="/portfolio", tags=["Portfolio"], dependencies=[Depends(verify_credentials)])
app.include_router(trades.router, prefix="/trades", tags=["Trades"], dependencies=[Depends(verify_credentials)])
app.include_router(market.router, prefix="/market", tags=["Market"], dependencies=[Depends(verify_credentials)])
app.include_router(bot.router, prefix="/bot", tags=["Bot"], dependencies=[Depends(verify_credentials)])
app.include_router(logs.router, prefix="/logs", tags=["Logs"], dependencies=[Depends(verify_credentials)])

# WebSocket (sin auth básica, tiene su propia auth por token)
from websocket.live import router as ws_router
app.include_router(ws_router)


@app.get("/health")
async def health(redis: aioredis.Redis = Depends(get_redis)):
    try:
        await redis.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {"status": "ok", "redis": redis_ok, "version": "1.0.0"}
```

### `api/routers/portfolio.py`

```python
from fastapi import APIRouter, Depends, Query
from database.crud import get_portfolio_history
from database.init_db import SessionLocal
import json, redis.asyncio as aioredis

router = APIRouter()


@router.get("")
async def get_portfolio():
    from api.main import get_redis
    redis = get_redis()
    raw = await redis.get("portfolio:state")
    return json.loads(raw) if raw else {"error": "Portfolio no disponible aún"}


@router.get("/history")
def get_history(days: int = Query(default=30, ge=1, le=365)):
    db = SessionLocal()
    try:
        snapshots = get_portfolio_history(db, days)
        return [{"timestamp": s.timestamp.isoformat(), "total_value_eur": s.total_value_eur,
                 "balance_eur": s.balance_eur, "total_pnl_eur": s.total_pnl_eur,
                 "total_pnl_pct": s.total_pnl_pct} for s in snapshots]
    finally:
        db.close()
```

### `api/routers/trades.py`

```python
from fastapi import APIRouter, Query
from database.crud import get_trades, get_stats_summary
from database.init_db import SessionLocal

router = APIRouter()


@router.get("")
def list_trades(limit: int = Query(default=50, ge=1, le=500), offset: int = 0):
    db = SessionLocal()
    try:
        trades = get_trades(db, limit, offset)
        return [{
            "id": t.id, "pair": t.pair, "side": t.side,
            "amount_crypto": t.amount_crypto, "amount_eur": t.amount_eur,
            "price": t.price, "fee_eur": t.fee_eur,
            "timestamp": t.timestamp.isoformat(), "mode": t.mode,
        } for t in trades]
    finally:
        db.close()


@router.get("/stats")
def trade_stats():
    db = SessionLocal()
    try:
        return get_stats_summary(db)
    finally:
        db.close()
```

### `api/routers/bot.py`

```python
from fastapi import APIRouter
import json, redis.asyncio as aioredis

router = APIRouter()


@router.get("/status")
async def bot_status():
    from api.main import get_redis
    redis = get_redis()
    raw = await redis.get("bot:status")
    return json.loads(raw) if raw else {"status": "unknown"}


@router.get("/config")
async def bot_config():
    from config import config
    return {
        "mode": config.trading.mode,
        "pairs": config.trading.pairs,
        "timeframe": config.trading.timeframe,
        "analysis_interval": config.trading.analysis_interval,
        "risk": {
            "max_risk_per_trade_pct": config.risk.max_risk_per_trade_pct,
            "max_open_positions": config.risk.max_open_positions,
            "min_confidence_threshold": config.risk.min_confidence_threshold,
            "stop_loss_atr_multiplier": config.risk.stop_loss_atr_multiplier,
            "take_profit_atr_multiplier": config.risk.take_profit_atr_multiplier,
        }
    }
```

### `api/routers/market.py`

```python
from fastapi import APIRouter
import json, redis.asyncio as aioredis
from config import config

router = APIRouter()


@router.get("/prices")
async def get_prices():
    from api.main import get_redis
    redis = get_redis()
    prices = {}
    for pair in config.trading.pairs:
        val = await redis.get(f"price:{pair}")
        prices[pair] = float(val) if val else None
    return prices


@router.get("/signals")
async def get_signals():
    from api.main import get_redis
    from database.crud import get_recent_decisions
    from database.init_db import SessionLocal
    db = SessionLocal()
    try:
        decisions = get_recent_decisions(db, limit=len(config.trading.pairs) * 3)
        return [{
            "pair": d.pair, "signal": d.signal, "confidence": d.confidence,
            "prob_buy": d.prob_buy, "prob_sell": d.prob_sell, "prob_hold": d.prob_hold,
            "executed": d.executed, "timestamp": d.timestamp.isoformat(),
        } for d in decisions]
    finally:
        db.close()
```

### `api/routers/logs.py`

```python
from fastapi import APIRouter, Query
from typing import Optional
from database.crud import get_logs
from database.init_db import SessionLocal

router = APIRouter()


@router.get("")
def get_system_logs(level: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500)):
    db = SessionLocal()
    try:
        logs = get_logs(db, level, limit)
        return [{"id": l.id, "timestamp": l.timestamp.isoformat(),
                 "level": l.level, "module": l.module, "message": l.message} for l in logs]
    finally:
        db.close()
```

---

## 19. `api/websocket/live.py`

```python
"""WebSocket que hace streaming en tiempo real de eventos del bot."""
import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import redis.asyncio as aioredis
from loguru import logger

router = APIRouter()
active_connections: list[WebSocket] = []


@router.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    logger.info(f"WebSocket cliente conectado. Total: {len(active_connections)}")

    from api.main import get_redis
    redis = get_redis()

    try:
        # Enviar estado inicial
        bot_status = await redis.get("bot:status")
        if bot_status:
            await websocket.send_text(json.dumps({"type": "bot_status", "data": json.loads(bot_status)}))

        portfolio = await redis.get("portfolio:state")
        if portfolio:
            await websocket.send_text(json.dumps({"type": "portfolio_update", "data": json.loads(portfolio)}))

        # Suscribirse al canal pub/sub de Redis
        pubsub = redis.pubsub()
        await pubsub.subscribe("bot:live_updates", "price_update")

        async def reader():
            async for message in pubsub.listen():
                if message["type"] == "message":
                    await websocket.send_text(message["data"])

        async def ping_loop():
            while True:
                await asyncio.sleep(30)
                await websocket.send_text(json.dumps({"type": "ping"}))

        await asyncio.gather(reader(), ping_loop())

    except WebSocketDisconnect:
        active_connections.remove(websocket)
        logger.info(f"WebSocket cliente desconectado. Total: {len(active_connections)}")
    except Exception as e:
        logger.error(f"Error en WebSocket: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)
    finally:
        await pubsub.unsubscribe()
        await pubsub.close()
```

---

## 20. `frontend/index.html`

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Crypto Trader Bot</title>
  <script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js"></script>
  <link rel="stylesheet" href="/css/style.css">
</head>
<body>
<div id="app">
  <!-- Barra superior -->
  <header class="topbar">
    <div class="topbar-left">
      <span class="logo">🤖 CryptoBot</span>
      <span class="badge" :class="modeClass">{{ botStatus.mode?.toUpperCase() || 'DEMO' }}</span>
      <span class="status-dot" :class="statusClass"></span>
      <span class="status-text">{{ botStatus.status || 'Conectando...' }}</span>
    </div>
    <div class="topbar-prices">
      <span v-for="(price, pair) in prices" :key="pair" class="price-chip">
        {{ pair }}: <strong>{{ formatPrice(price) }}€</strong>
      </span>
    </div>
  </header>

  <!-- Cards de resumen -->
  <section class="cards">
    <div class="card">
      <div class="card-label">Portfolio total</div>
      <div class="card-value">{{ formatPrice(portfolio.total_value_eur) }}€</div>
      <div class="card-sub" :class="portfolio.total_pnl_eur >= 0 ? 'positive' : 'negative'">
        {{ portfolio.total_pnl_eur >= 0 ? '+' : '' }}{{ formatPrice(portfolio.total_pnl_eur) }}€ ({{ portfolio.total_pnl_pct?.toFixed(2) }}%)
      </div>
    </div>
    <div class="card">
      <div class="card-label">Balance libre</div>
      <div class="card-value">{{ formatPrice(portfolio.balance_eur) }}€</div>
    </div>
    <div class="card">
      <div class="card-label">Posiciones abiertas</div>
      <div class="card-value">{{ openPositions.length }}</div>
      <div class="card-sub">máx. {{ botConfig.risk?.max_open_positions || 3 }}</div>
    </div>
    <div class="card">
      <div class="card-label">Win rate</div>
      <div class="card-value">{{ stats.win_rate?.toFixed(1) || '—' }}%</div>
      <div class="card-sub">{{ stats.closed_positions || 0 }} operaciones cerradas</div>
    </div>
  </section>

  <!-- Gráfica portfolio -->
  <section class="chart-section">
    <div class="section-header">
      <h2>Evolución del portfolio</h2>
      <div class="period-selector">
        <button v-for="d in [7,30,90]" :key="d" @click="loadPortfolioHistory(d)" :class="{active: historyDays===d}">{{ d }}d</button>
      </div>
    </div>
    <canvas id="portfolioChart" height="80"></canvas>
  </section>

  <!-- Señales del modelo -->
  <section class="signals-section">
    <h2>Señales actuales</h2>
    <div class="signals-grid">
      <div v-for="sig in latestSignals" :key="sig.pair" class="signal-card">
        <div class="signal-pair">{{ sig.pair }}</div>
        <div class="signal-badge" :class="signalClass(sig.signal)">{{ sig.signal }}</div>
        <div class="signal-confidence">{{ (sig.confidence * 100).toFixed(1) }}% confianza</div>
        <div class="signal-bars">
          <div class="bar-row"><span>BUY</span><div class="bar"><div class="bar-fill buy" :style="{width: (sig.prob_buy*100)+'%'}"></div></div><span>{{ (sig.prob_buy*100).toFixed(0) }}%</span></div>
          <div class="bar-row"><span>HOLD</span><div class="bar"><div class="bar-fill hold" :style="{width: (sig.prob_hold*100)+'%'}"></div></div><span>{{ (sig.prob_hold*100).toFixed(0) }}%</span></div>
          <div class="bar-row"><span>SELL</span><div class="bar"><div class="bar-fill sell" :style="{width: (sig.prob_sell*100)+'%'}"></div></div><span>{{ (sig.prob_sell*100).toFixed(0) }}%</span></div>
        </div>
      </div>
    </div>
  </section>

  <!-- Posiciones abiertas -->
  <section v-if="openPositions.length">
    <h2>Posiciones abiertas</h2>
    <table class="data-table">
      <thead><tr><th>Par</th><th>Entrada</th><th>Actual</th><th>PnL</th><th>Stop-loss</th><th>Take-profit</th></tr></thead>
      <tbody>
        <tr v-for="(pos, pair) in portfolio.positions" :key="pair">
          <td>{{ pair }}</td>
          <td>{{ formatPrice(pos.entry_price) }}€</td>
          <td>{{ formatPrice(pos.current_price) }}€</td>
          <td :class="pos.pnl_eur >= 0 ? 'positive' : 'negative'">{{ pos.pnl_eur >= 0 ? '+' : '' }}{{ formatPrice(pos.pnl_eur) }}€ ({{ pos.pnl_pct?.toFixed(2) }}%)</td>
          <td class="negative">{{ formatPrice(pos.stop_loss_price) }}€</td>
          <td class="positive">{{ formatPrice(pos.take_profit_price) }}€</td>
        </tr>
      </tbody>
    </table>
  </section>

  <!-- Historial de trades -->
  <section>
    <h2>Historial de trades</h2>
    <table class="data-table">
      <thead><tr><th>Fecha</th><th>Par</th><th>Tipo</th><th>Precio</th><th>EUR</th><th>Comisión</th></tr></thead>
      <tbody>
        <tr v-for="t in trades" :key="t.id">
          <td>{{ formatDate(t.timestamp) }}</td>
          <td>{{ t.pair }}</td>
          <td><span class="badge" :class="t.side === 'buy' ? 'badge-buy' : 'badge-sell'">{{ t.side.toUpperCase() }}</span></td>
          <td>{{ formatPrice(t.price) }}€</td>
          <td>{{ formatPrice(t.amount_eur) }}€</td>
          <td>{{ formatPrice(t.fee_eur) }}€</td>
        </tr>
      </tbody>
    </table>
  </section>

  <!-- Log del sistema -->
  <section>
    <h2>Log del sistema</h2>
    <div class="log-container" ref="logContainer">
      <div v-for="log in systemLogs" :key="log.id" class="log-line" :class="'log-'+log.level.toLowerCase()">
        <span class="log-time">{{ formatDate(log.timestamp) }}</span>
        <span class="log-level">{{ log.level }}</span>
        <span class="log-module">{{ log.module }}</span>
        <span class="log-msg">{{ log.message }}</span>
      </div>
    </div>
  </section>
</div>
<script src="/js/app.js"></script>
</body>
</html>
```

---

## 21. `frontend/js/app.js`

```javascript
const { createApp, ref, computed, onMounted, onUnmounted, nextTick } = Vue;

const API_BASE = '/api';
const WS_URL = `ws://${location.host}/ws/live`;

const api = axios.create({
  baseURL: API_BASE,
  auth: { username: 'admin', password: 'changeme' } // leer de localStorage en producción
});

createApp({
  setup() {
    const portfolio = ref({ total_value_eur: 0, balance_eur: 0, total_pnl_eur: 0, total_pnl_pct: 0, positions: {} });
    const botStatus = ref({ status: 'connecting', mode: 'demo' });
    const botConfig = ref({ risk: {} });
    const prices = ref({});
    const trades = ref([]);
    const systemLogs = ref([]);
    const latestSignals = ref([]);
    const stats = ref({});
    const historyDays = ref(30);
    const logContainer = ref(null);
    let portfolioChart = null;
    let ws = null;
    let wsReconnectTimer = null;

    // ── WebSocket ─────────────────────────────────────────────────────────
    const connectWS = () => {
      ws = new WebSocket(WS_URL);
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === 'portfolio_update') portfolio.value = msg.data;
        else if (msg.type === 'bot_status') botStatus.value = msg.data;
        else if (msg.type === 'price_update') prices.value[msg.data.pair] = msg.data.price;
        else if (msg.type === 'signal') updateSignal(msg.data);
        else if (msg.type === 'trade_executed') { trades.value.unshift(msg.data); loadTrades(); }
      };
      ws.onclose = () => {
        wsReconnectTimer = setTimeout(connectWS, 5000);
      };
    };

    const updateSignal = (data) => {
      const idx = latestSignals.value.findIndex(s => s.pair === data.pair);
      if (idx >= 0) latestSignals.value[idx] = data;
      else latestSignals.value.push(data);
    };

    // ── Carga de datos ────────────────────────────────────────────────────
    const loadAll = async () => {
      try {
        const [portRes, tradesRes, statsRes, sigRes, configRes, logsRes, pricesRes] = await Promise.all([
          api.get('/portfolio'),
          api.get('/trades?limit=50'),
          api.get('/trades/stats'),
          api.get('/market/signals'),
          api.get('/bot/config'),
          api.get('/logs?limit=100'),
          api.get('/market/prices'),
        ]);
        portfolio.value = portRes.data;
        trades.value = tradesRes.data;
        stats.value = statsRes.data;
        latestSignals.value = dedupSignals(sigRes.data);
        botConfig.value = configRes.data;
        systemLogs.value = logsRes.data;
        prices.value = pricesRes.data;
      } catch (e) { console.error('Error cargando datos:', e); }
    };

    const loadTrades = async () => {
      const res = await api.get('/trades?limit=50');
      trades.value = res.data;
    };

    const loadPortfolioHistory = async (days) => {
      historyDays.value = days;
      const res = await api.get(`/portfolio/history?days=${days}`);
      renderPortfolioChart(res.data);
    };

    const dedupSignals = (signals) => {
      const map = {};
      signals.forEach(s => { if (!map[s.pair] || s.timestamp > map[s.pair].timestamp) map[s.pair] = s; });
      return Object.values(map);
    };

    // ── Gráfica portfolio ─────────────────────────────────────────────────
    const renderPortfolioChart = (history) => {
      const ctx = document.getElementById('portfolioChart');
      if (!ctx) return;
      if (portfolioChart) portfolioChart.destroy();
      portfolioChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: history.map(h => new Date(h.timestamp).toLocaleDateString('es-ES')),
          datasets: [{
            label: 'Portfolio (€)',
            data: history.map(h => h.total_value_eur),
            borderColor: '#4f8ef7',
            backgroundColor: 'rgba(79,142,247,0.08)',
            fill: true,
            tension: 0.3,
            pointRadius: 2,
          }]
        },
        options: {
          responsive: true,
          plugins: { legend: { display: false } },
          scales: { y: { ticks: { callback: v => v.toFixed(0) + '€' } } }
        }
      });
    };

    // ── Helpers ───────────────────────────────────────────────────────────
    const formatPrice = (v) => v != null ? Number(v).toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
    const formatDate = (ts) => new Date(ts).toLocaleString('es-ES');
    const modeClass = computed(() => botStatus.value.mode === 'real' ? 'badge-real' : 'badge-demo');
    const statusClass = computed(() => ({ 'dot-green': botStatus.value.status === 'running', 'dot-red': botStatus.value.status === 'error', 'dot-gray': botStatus.value.status !== 'running' && botStatus.value.status !== 'error' }));
    const openPositions = computed(() => Object.keys(portfolio.value.positions || {}));
    const signalClass = (s) => ({ 'badge-buy': s === 'BUY', 'badge-sell': s === 'SELL', 'badge-hold': s === 'HOLD' });

    onMounted(async () => {
      await loadAll();
      await loadPortfolioHistory(30);
      connectWS();
      setInterval(loadAll, 60000); // Polling de fallback cada 60s
    });

    onUnmounted(() => {
      if (ws) ws.close();
      if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
    });

    return {
      portfolio, botStatus, botConfig, prices, trades, systemLogs,
      latestSignals, stats, historyDays, logContainer, openPositions,
      formatPrice, formatDate, modeClass, statusClass, signalClass,
      loadPortfolioHistory,
    };
  }
}).mount('#app');
```

---

## 22. `training/fetch_historical_data.py`

```python
"""Descarga masiva de datos históricos para entrenamiento del modelo (ejecutar en PC/Colab)."""
import asyncio
import os
import ccxt.async_support as ccxt
import pandas as pd
from datetime import datetime, timedelta

PAIRS = ["BTC-EUR", "ETH-EUR", "SOL-EUR"]
TIMEFRAME = "5m"
DAYS = 365
OUTPUT_DIR = "training_data"

os.makedirs(OUTPUT_DIR, exist_ok=True)


async def fetch_pair(pair: str) -> pd.DataFrame:
    exchange = ccxt.coinbase({"enableRateLimit": True})
    since = int((datetime.utcnow() - timedelta(days=DAYS)).timestamp() * 1000)
    all_ohlcv = []
    print(f"Descargando {pair}...")
    try:
        while True:
            ohlcv = await exchange.fetch_ohlcv(pair, TIMEFRAME, since=since, limit=300)
            if not ohlcv:
                break
            all_ohlcv.extend(ohlcv)
            since = ohlcv[-1][0] + 1
            print(f"  {pair}: {len(all_ohlcv)} velas descargadas...", end="\r")
            if len(ohlcv) < 300:
                break
            await asyncio.sleep(0.3)
    finally:
        await exchange.close()

    df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    path = os.path.join(OUTPUT_DIR, f"{pair.replace('-', '_')}_{TIMEFRAME}.csv")
    df.to_csv(path, index=False)
    print(f"\n✅ {pair}: {len(df)} velas guardadas en {path}")
    return df


async def main():
    for pair in PAIRS:
        await fetch_pair(pair)

asyncio.run(main())
```

---

## 23. `training/train_model.py`

```python
"""Entrenamiento del modelo LightGBM (ejecutar en PC o Google Colab)."""
import os
import sys
import json
import pickle
from datetime import datetime
import numpy as np
import pandas as pd
import pandas_ta as ta
from sklearn.preprocessing import RobustScaler
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import TimeSeriesSplit
import lightgbm as lgb

# Añadir path del bot para usar FeatureBuilder
sys.path.insert(0, "../bot")
from indicators.technical import calculate_indicators
from indicators.features import FeatureBuilder

DATA_DIR = "training_data"
OUTPUT_DIR = "trained_models"
PAIRS = ["BTC-EUR", "ETH-EUR"]
TIMEFRAME = "5m"
LABEL_THRESHOLD = 0.008   # 0.8% de movimiento para etiquetar BUY/SELL
FORWARD_CANDLES = 3       # Horizonte de predicción (3 velas = 15 min en 5m)
os.makedirs(OUTPUT_DIR, exist_ok=True)

feature_builder = FeatureBuilder()


def create_labels(df: pd.DataFrame) -> pd.Series:
    """Etiqueta: 2=BUY, 0=SELL, 1=HOLD según retorno futuro."""
    future_return = df["close"].shift(-FORWARD_CANDLES) / df["close"] - 1
    labels = pd.Series(1, index=df.index)  # HOLD por defecto
    labels[future_return > LABEL_THRESHOLD] = 2   # BUY
    labels[future_return < -LABEL_THRESHOLD] = 0  # SELL
    return labels


def load_and_prepare(pair: str) -> tuple[pd.DataFrame, pd.Series]:
    path = os.path.join(DATA_DIR, f"{pair.replace('-', '_')}_{TIMEFRAME}.csv")
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = calculate_indicators(df)
    labels = create_labels(df)

    features = feature_builder.build_features_batch(df)
    valid_idx = features.index
    labels = labels.loc[valid_idx]
    # Eliminar filas con NaN en labels (últimas FORWARD_CANDLES filas)
    mask = labels.notna()
    features = features[mask]
    labels = labels[mask]
    return features, labels.astype(int)


# Cargar todos los pares
all_features = []
all_labels = []
for pair in PAIRS:
    print(f"Procesando {pair}...")
    X, y = load_and_prepare(pair)
    all_features.append(X)
    all_labels.append(y)
    print(f"  {len(X)} muestras | BUY: {(y==2).sum()} | SELL: {(y==0).sum()} | HOLD: {(y==1).sum()}")

X = pd.concat(all_features).reset_index(drop=True)
y = pd.concat(all_labels).reset_index(drop=True)
feature_names = X.columns.tolist()
print(f"\nTotal muestras: {len(X)} | Features: {len(feature_names)}")

# Split temporal 70/15/15
n = len(X)
train_end = int(n * 0.70)
val_end = int(n * 0.85)
X_train, y_train = X.iloc[:train_end], y.iloc[:train_end]
X_val, y_val = X.iloc[train_end:val_end], y.iloc[train_end:val_end]
X_test, y_test = X.iloc[val_end:], y.iloc[val_end:]

# Escalar features
scaler = RobustScaler()
X_train_s = scaler.fit_transform(X_train)
X_val_s = scaler.transform(X_val)
X_test_s = scaler.transform(X_test)

# Entrenar LightGBM
print("\nEntrenando LightGBM...")
model = lgb.LGBMClassifier(
    n_estimators=500,
    max_depth=6,
    num_leaves=31,
    learning_rate=0.05,
    n_jobs=2,
    class_weight="balanced",
    random_state=42,
    verbose=-1,
)
model.fit(
    X_train_s, y_train,
    eval_set=[(X_val_s, y_val)],
    callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(100)],
)

# Evaluación
print("\n=== VALIDACIÓN ===")
val_preds = model.predict(X_val_s)
print(classification_report(y_val, val_preds, target_names=["SELL", "HOLD", "BUY"]))

print("\n=== TEST FINAL ===")
test_preds = model.predict(X_test_s)
report = classification_report(y_test, test_preds, target_names=["SELL", "HOLD", "BUY"], output_dict=True)
print(classification_report(y_test, test_preds, target_names=["SELL", "HOLD", "BUY"]))

# Exportar modelo
model_path = os.path.join(OUTPUT_DIR, "trained_model.pkl")
scaler_path = os.path.join(OUTPUT_DIR, "scaler.pkl")
with open(model_path, "wb") as f:
    pickle.dump(model, f)
with open(scaler_path, "wb") as f:
    pickle.dump(scaler, f)

metadata = {
    "trained_at": datetime.utcnow().isoformat(),
    "pairs": PAIRS,
    "timeframe": TIMEFRAME,
    "label_threshold": LABEL_THRESHOLD,
    "forward_candles": FORWARD_CANDLES,
    "feature_names": feature_names,
    "n_train": len(X_train),
    "n_val": len(X_val),
    "n_test": len(X_test),
    "validation_metrics": {
        "buy_precision": report["BUY"]["precision"],
        "sell_precision": report["SELL"]["precision"],
        "accuracy": report["accuracy"],
    },
}
with open(os.path.join(OUTPUT_DIR, "trained_model_metadata.json"), "w") as f:
    json.dump(metadata, f, indent=2)

print(f"\n✅ Modelo exportado a {OUTPUT_DIR}/")
print(f"   Copia a la RPi: scp {OUTPUT_DIR}/* pi@[IP_RPI]:~/crypto-trader/bot/model/")
```

---

## 24. `bot/requirements.txt`

```
ccxt>=4.2.0
pandas>=2.0.0
numpy>=1.24.0
pandas-ta>=0.3.14b
lightgbm>=4.3.0
scikit-learn>=1.4.0
redis>=5.0.0
sqlalchemy>=2.0.0
aioredis>=2.0.0
apscheduler>=3.10.0
loguru>=0.7.0
python-dotenv>=1.0.0
httpx>=0.27.0
```

## 25. `api/requirements.txt`

```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
redis>=5.0.0
sqlalchemy>=2.0.0
python-dotenv>=1.0.0
loguru>=0.7.0
```

---

## 26. `bot/Dockerfile` y `api/Dockerfile`

### `bot/Dockerfile`

```dockerfile
FROM python:3.11-slim-bookworm

# Dependencias del sistema necesarias para pandas-ta y LightGBM en ARM
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data /app/logs /app/model

CMD ["python", "main.py"]
```

### `api/Dockerfile`

```dockerfile
FROM python:3.11-slim-bookworm

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

---

## 27. `scripts/setup_rpi.sh`

```bash
#!/bin/bash
# Script de configuración inicial de la Raspberry Pi 3B
# Ejecutar como: bash scripts/setup_rpi.sh

set -e
echo "🔧 Configurando Raspberry Pi 3B para Crypto Trader Bot..."

# Actualizar sistema
sudo apt-get update && sudo apt-get upgrade -y

# Ampliar swap a 1 GB
echo "Configurando swap..."
sudo dphys-swapfile swapoff
sudo sed -i 's/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
sudo dphys-swapfile setup
sudo dphys-swapfile swapon

# Instalar dependencias
sudo apt-get install -y git curl wget sqlite3

# Instalar Docker
if ! command -v docker &> /dev/null; then
    echo "Instalando Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
fi

# Instalar Docker Compose plugin
sudo apt-get install -y docker-compose-plugin

# Crear estructura de directorios
mkdir -p ~/crypto-trader/{data,logs,bot/model}

echo ""
echo "✅ Configuración completada."
echo ""
echo "Pasos siguientes:"
echo "1. Cerrar sesión y volver a entrar (para que docker funcione sin sudo)"
echo "2. Copiar los modelos entrenados a ~/crypto-trader/bot/model/"
echo "3. Crear el archivo .env basado en .env.example"
echo "4. Ejecutar: cd ~/crypto-trader && docker compose up -d"
echo "5. Acceder al dashboard en http://$(hostname -I | awk '{print $1}')"
```

---

## Notas finales para el agente de codificación

### Orden de implementación recomendado

1. `config.py` → base de toda la configuración
2. `database/models.py` + `init_db.py` + `crud.py` → persistencia
3. `indicators/technical.py` + `indicators/features.py` → pipeline de datos
4. `data/collector.py` + `data/historical.py` → recolección
5. `model/predictor.py` → inferencia (con modelo dummy si aún no está entrenado)
6. `trading/risk_manager.py` + `trading/portfolio.py` → gestión de riesgo
7. `trading/demo_trader.py` → motor demo
8. `trading/engine.py` → orquestador principal
9. `bot/main.py` → punto de entrada
10. `api/` → backend FastAPI
11. `frontend/` → dashboard web
12. `training/` → scripts de entrenamiento (ejecutar en PC externo)

### Tests mínimos antes de arrancar en producción

- Verificar que `DataCollector` recibe datos de Coinbase sin errores (modo sandbox primero)
- Verificar que `FeatureBuilder` produce el mismo número de features que el modelo espera
- Simular un ciclo completo con precio fijo y verificar que `DemoTrader` actualiza el portfolio correctamente
- Verificar que el WebSocket del dashboard recibe actualizaciones en tiempo real
- Verificar que el stop-loss se activa correctamente con un precio de prueba por debajo del umbral

### Límites de recursos en RPi 3B

- Bot: máx 400 MB RAM, 2 núcleos CPU
- API: máx 200 MB RAM, 1 núcleo CPU
- Redis: máx 128 MB RAM
- Total disponible: ~1 GB → margen de ~270 MB para el SO
- Si hay presión de memoria, reducir `MAX_REDIS_CANDLES` de 500 a 200

---

*Documento de implementación v1.0 — Crypto Trader Bot para Raspberry Pi 3B*
