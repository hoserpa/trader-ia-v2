"""Inicialización de la base de datos SQLite."""
import os
from sqlalchemy import create_engine, text
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

    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        try:
            conn.execute(text("ALTER TABLE positions ADD COLUMN position_type VARCHAR(5) DEFAULT 'long'"))
            logger.info("Migración: columna position_type añadida a positions")
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE positions ADD COLUMN stop_loss_order_id VARCHAR(100)"))
            logger.info("Migración: columna stop_loss_order_id añadida a positions")
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE positions ADD COLUMN take_profit_order_id VARCHAR(100)"))
            logger.info("Migración: columna take_profit_order_id añadida a positions")
        except Exception:
            pass
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
