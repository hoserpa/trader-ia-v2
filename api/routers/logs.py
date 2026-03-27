from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import sys
import os
import re
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))
from config import config

router = APIRouter()

LOG_FILE = config.log.file
MAX_LINES = 500


@router.get("")
def get_system_logs(
    level: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500)
):
    if not os.path.exists(LOG_FILE):
        return []
    
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        logs = []
        for i, line in enumerate(reversed(lines)):
            line = line.strip()
            if not line:
                continue
            
            if " | " in line:
                parts = line.split(" | ", 2)
                if len(parts) >= 3:
                    timestamp_str = parts[0].strip()
                    level_log = parts[1].strip().upper()
                    message = parts[2].strip() if len(parts) > 2 else ""
                    
                    if level and level.upper() not in level_log:
                        continue
                    
                    if level_log not in ["INFO", "ERROR", "WARNING", "DEBUG"]:
                        continue
                    
                    logs.append({
                        "id": i,
                        "timestamp": timestamp_str,
                        "level": level_log,
                        "message": message[:200]
                    })
            
            if len(logs) >= limit:
                break
        
        if level and level.upper() == "ERROR":
            logs = [l for l in logs if l["level"] == "ERROR"]
        elif level and level.upper() == "WARNING":
            logs = [l for l in logs if l["level"] in ["WARNING", "ERROR"]]
        
        return logs[:limit]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
