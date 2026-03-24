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
