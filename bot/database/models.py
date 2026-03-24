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
    status = Column(String(10), nullable=False, default="open")
    close_price = Column(Float, nullable=True)
    close_timestamp = Column(DateTime, nullable=True)
    pnl_eur = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    close_reason = Column(String(20), nullable=True)
    trades = relationship("Trade", back_populates="position")


class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(Integer, ForeignKey("positions.id"), nullable=True)
    pair = Column(String(20), nullable=False)
    side = Column(String(4), nullable=False)
    amount_crypto = Column(Float, nullable=False)
    amount_eur = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    fee_eur = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    mode = Column(String(4), nullable=False)
    exchange_order_id = Column(String(100), nullable=True)
    position = relationship("Position", back_populates="trades")
    __table_args__ = (Index("idx_trades_timestamp", "timestamp"),)


class ModelDecision(Base):
    __tablename__ = "model_decisions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    pair = Column(String(20), nullable=False)
    signal = Column(String(4), nullable=False)
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
