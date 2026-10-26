"""Inicialización de la base de datos SQLite."""
import os
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from .models import Base
from config import config
from loguru import logger


def get_engine():
    os.makedirs(os.path.dirname(config.database.sqlite_path), exist_ok=True)
    engine = create_engine(
        f"sqlite:///{config.database.sqlite_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


def _column_names(conn, table: str) -> set:
    return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    """Añade una columna solo si falta (migración idempotente)."""
    if column in _column_names(conn, table):
        return
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
    logger.info(f"Migración: columna {column} añadida a {table}")


def init_db() -> sessionmaker:
    engine = get_engine()
    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        _ensure_column(conn, "positions", "position_type", "position_type VARCHAR(5) DEFAULT 'long'")
        _ensure_column(conn, "positions", "stop_loss_order_id", "stop_loss_order_id VARCHAR(100)")
        _ensure_column(conn, "positions", "take_profit_order_id", "take_profit_order_id VARCHAR(100)")
        _ensure_column(conn, "positions", "realized_pnl_eur", "realized_pnl_eur FLOAT DEFAULT 0.0")
        _ensure_column(conn, "trades", "pnl_eur", "pnl_eur FLOAT")
        _ensure_column(conn, "trades", "cycle_id", "cycle_id VARCHAR(36)")
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_trades_cycle ON trades(cycle_id)"))
        _ensure_column(conn, "trades", "reason", "reason VARCHAR(20) DEFAULT 'grid'")
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_trades_reason ON trades(reason)"))
        _ensure_column(conn, "trades", "entry_price", "entry_price FLOAT")
        _ensure_column(conn, "trades", "margin_fee_eur", "margin_fee_eur FLOAT")
        _ensure_column(conn, "trades", "open_trade_id", "open_trade_id INTEGER")
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_trades_open_trade ON trades(open_trade_id)"))
        conn.commit()

    logger.info(f"Base de datos inicializada en {config.database.sqlite_path}")
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


SessionLocal = init_db()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
