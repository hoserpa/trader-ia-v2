"""Tareas programadas con APScheduler."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from notifications.telegram import TelegramNotifier
from database.crud import get_stats_summary, get_open_positions
from database.init_db import SessionLocal
import redis.asyncio as aioredis
import json


def setup_scheduler(redis_client: aioredis.Redis) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    notifier = TelegramNotifier()

    @scheduler.scheduled_job(CronTrigger(hour=8, minute=0))
    async def daily_summary():
        logger.info("Ejecutando resumen diario...")
        db = SessionLocal()
        try:
            stats = get_stats_summary(db)
            open_positions = get_open_positions(db)
        finally:
            db.close()

        raw = await redis_client.get("portfolio:state")
        portfolio = json.loads(raw) if raw else {}
        portfolio["open_positions"] = len(open_positions)
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
