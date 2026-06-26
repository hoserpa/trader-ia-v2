"""Endpoint de configuración en caliente vía Redis."""
import os
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from api.main import get_redis
import redis.asyncio as aioredis
from config_service import EDITABLE_FIELDS, load_overrides, set_override, delete_override
from config import config

router = APIRouter()


class OverrideRequest(BaseModel):
    value: float | int | bool | str


def _current_value(key: str) -> float | int | bool | str | None:
    info = EDITABLE_FIELDS.get(key)
    if not info:
        return None
    section = getattr(config, info["section"], None)
    if section is None:
        return None
    raw = getattr(section, key, None)
    return raw


def _build_full_config(overrides: dict) -> dict:
    """Construye el objeto de configuración completo para el frontend."""
    fields = []
    for key, info in EDITABLE_FIELDS.items():
        current = _current_value(key)
        def_val = current
        meta = {
            "key": key,
            "label": info["label"],
            "type": "bool" if info["type"] == bool else "number",
            "section": info["section"],
            "current": current,
            "overridden": key in overrides,
            "min": info.get("min"),
            "max": info.get("max"),
            "step": info.get("step"),
        }
        fields.append(meta)
    return {"fields": fields, "overrides": overrides}


@router.get("")
async def get_config(redis: aioredis.Redis = Depends(get_redis)):
    overrides = await load_overrides(redis)
    return _build_full_config(overrides)


@router.put("/{key}")
async def update_config(key: str, body: OverrideRequest, redis: aioredis.Redis = Depends(get_redis)):
    try:
        await set_override(redis, key, body.value)
        overrides = await load_overrides(redis)
        return {"ok": True, "overrides": overrides}
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{key}")
async def remove_config(key: str, redis: aioredis.Redis = Depends(get_redis)):
    try:
        overrides = await delete_override(redis, key)
        return {"ok": True, "overrides": overrides}
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/fields")
async def list_fields():
    """Devuelve los metadatos de todos los campos editables (sin valores actuales)."""
    return {
        key: {k: v for k, v in info.items() if k != "section"}
        for key, info in EDITABLE_FIELDS.items()
    }
