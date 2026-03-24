from fastapi import APIRouter, Query
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))
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
