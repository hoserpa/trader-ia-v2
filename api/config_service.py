"""Gestión de configuración en caliente vía Redis.
Permite sobreescribir valores de config.py sin reiniciar el contenedor."""
import json
from typing import Any
from loguru import logger
from config import config

REDIS_KEY = "bot:config_overrides"

EDITABLE_FIELDS: dict[str, dict] = {
    "capital_pct": {"section": "grid", "type": float, "label": "Capital % para grid", "min": 0.1, "max": 1.0, "step": 0.05},
    "min_lot_value_eur": {"section": "grid", "type": float, "label": "Lote mínimo por nivel (€)", "min": 0.5, "max": 50.0, "step": 0.5},
    "leverage": {"section": "grid", "type": int, "label": "Leverage", "min": 1, "max": 5},
    "levels_per_pair": {"section": "grid", "type": int, "label": "Niveles por par", "min": 4, "max": 30},
    "range_pct": {"section": "grid", "type": float, "label": "Rango del grid %", "min": 0.01, "max": 0.20, "step": 0.01},
    "rebalance_threshold": {"section": "grid", "type": float, "label": "Umbral de rebalance %", "min": 0.02, "max": 0.30, "step": 0.01},
    "stop_loss_pct": {"section": "grid", "type": float, "label": "Stop loss global %", "min": 0.02, "max": 0.20, "step": 0.01},
    "poll_interval": {"section": "grid", "type": int, "label": "Intervalo poll (s)", "min": 5, "max": 120, "step": 5},
    "atr_adaptive": {"section": "grid", "type": bool, "label": "ATR-adaptive"},
    "atr_range_mult": {"section": "grid", "type": float, "label": "ATR range multiplier", "min": 1.0, "max": 10.0, "step": 0.5},
    "atr_spacing_divisor": {"section": "grid", "type": float, "label": "ATR spacing divisor", "min": 1.0, "max": 10.0, "step": 0.5},
    "max_levels": {"section": "grid", "type": int, "label": "Max niveles", "min": 4, "max": 30},
    "taker_fee": {"section": "exchange", "type": float, "label": "Comisión taker (Kraken)", "min": 0.0001, "max": 0.05, "step": 0.0001},
    "maker_fee": {"section": "exchange", "type": float, "label": "Comisión maker (Kraken)", "min": 0.0001, "max": 0.05, "step": 0.0001},
}


def get_section(field_key: str) -> str | None:
    info = EDITABLE_FIELDS.get(field_key)
    return info["section"] if info else None


def _cast_value(value: Any, target_type: type) -> Any:
    if target_type == bool:
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "yes")
    return target_type(value)


async def load_overrides(redis) -> dict[str, Any]:
    raw = await redis.get(REDIS_KEY)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Config overrides corruptos en Redis, ignorando")
        return {}


async def save_overrides(redis, overrides: dict[str, Any]) -> None:
    await redis.set(REDIS_KEY, json.dumps(overrides))


async def apply_overrides(redis) -> dict[str, Any]:
    """Carga overrides de Redis y los aplica al objeto config en caliente.
    Retorna el dict de overrides activos."""
    overrides = await load_overrides(redis)
    for key, value in overrides.items():
        info = EDITABLE_FIELDS.get(key)
        if not info:
            continue
        try:
            casted = _cast_value(value, info["type"])
            section_name = info["section"]
            section = getattr(config, section_name, None)
            if section is not None:
                setattr(section, key, casted)
        except (ValueError, TypeError, AttributeError) as e:
            logger.warning(f"Error aplicando override {key}={value}: {e}")
    if overrides:
        logger.debug(f"Config overrides aplicados: {overrides}")
    return overrides


async def set_override(redis, key: str, value: Any) -> dict:
    """Guarda un override y lo aplica inmediatamente."""
    info = EDITABLE_FIELDS.get(key)
    if not info:
        raise ValueError(f"Campo '{key}' no es configurable")
    casted = _cast_value(value, info["type"])
    section = getattr(config, info["section"], None)
    if section is None:
        raise ValueError(f"Sección '{info['section']}' no encontrada en config")
    setattr(section, key, casted)
    overrides = await load_overrides(redis)
    overrides[key] = value
    await save_overrides(redis, overrides)
    logger.info(f"Config override {key}={value} aplicado")
    return overrides


async def delete_override(redis, key: str) -> dict:
    """Elimina un override y restaura el valor del código."""
    info = EDITABLE_FIELDS.get(key)
    if not info:
        raise ValueError(f"Campo '{key}' no es configurable")
    overrides = await load_overrides(redis)
    overrides.pop(key, None)
    await save_overrides(redis, overrides)
    await apply_overrides(redis)
    logger.info(f"Config override {key} eliminado, valor restaurado")
    return overrides
