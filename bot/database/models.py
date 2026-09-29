"""Modelos SQLAlchemy para la base de datos SQLite."""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, DateTime, Text,
    ForeignKey, UniqueConstraint, Index
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
    position_type = Column(String(5), nullable=False, default="long")
    status = Column(String(10), nullable=False, default="open")
    close_price = Column(Float, nullable=True)
    close_timestamp = Column(DateTime, nullable=True)
    pnl_eur = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    close_reason = Column(String(20), nullable=True)
    realized_pnl_eur = Column(Float, nullable=False, default=0.0)
    stop_loss_order_id = Column(String(100), nullable=True)
    take_profit_order_id = Column(String(100), nullable=True)
    trades = relationship("Trade", back_populates="position")


class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(Integer, ForeignKey("positions.id"), nullable=True)
    pair = Column(String(20), nullable=False)
    side = Column(String(13), nullable=False)
    amount_crypto = Column(Float, nullable=False)
    amount_eur = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    fee_eur = Column(Float, nullable=False)
    pnl_eur = Column(Float, nullable=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    mode = Column(String(4), nullable=False)
    exchange_order_id = Column(String(100), nullable=True)
    cycle_id = Column(String(36), nullable=True)
    reason = Column(String(20), nullable=False, default="grid")
    position = relationship("Position", back_populates="trades")
    __table_args__ = (
        Index("idx_trades_timestamp", "timestamp"),
        Index("idx_trades_cycle", "cycle_id"),
        Index("idx_trades_reason", "reason"),
    )
