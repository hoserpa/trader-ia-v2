"""Tareas programadas con APScheduler."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from database.init_db import SessionLocal


def setup_scheduler(redis_client) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

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
