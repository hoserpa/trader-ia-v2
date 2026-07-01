"""Gestión de configuración en caliente vía Redis.
Permite sobreescribir valores de config.py sin reiniciar el contenedor."""
import json
from typing import Any
from loguru import logger
from config import config

REDIS_KEY = "bot:config_overrides"

EDITABLE_FIELDS: dict[str, dict] = {
    "max_risk_per_trade_pct": {"section": "risk", "type": float, "label": "Riesgo máx. por trade", "min": 0.001, "max": 0.05, "step": 0.001},
    "max_open_positions": {"section": "risk", "type": int, "label": "Máx. posiciones abiertas", "min": 1, "max": 10},
    "max_portfolio_in_crypto_pct": {"section": "risk", "type": float, "label": "Máx. portfolio en crypto %", "min": 0.1, "max": 1.0, "step": 0.05},
    "stop_loss_atr_multiplier": {"section": "risk", "type": float, "label": "Multiplicador ATR para SL", "min": 0.5, "max": 5.0, "step": 0.5},
    "take_profit_atr_multiplier": {"section": "risk", "type": float, "label": "Multiplicador ATR para TP", "min": 0.5, "max": 5.0, "step": 0.5},
    "max_daily_trades": {"section": "risk", "type": int, "label": "Trades máximos por día", "min": 1, "max": 50},
    "min_confidence_threshold": {"section": "risk", "type": float, "label": "Confianza mínima para abrir", "min": 0.01, "max": 0.50, "step": 0.01},
    "close_confidence_threshold": {"section": "risk", "type": float, "label": "Confianza para cerrar por modelo", "min": 0.10, "max": 0.70, "step": 0.05},
    "max_position_hours": {"section": "risk", "type": int, "label": "Horas máximas por posición", "min": 1, "max": 24},
    "cooldown_minutes": {"section": "risk", "type": int, "label": "Cooldown entre trades (min)", "min": 0, "max": 480, "step": 5},
    "trailing_stop_activation_pct": {"section": "risk", "type": float, "label": "Activación trailing stop %", "min": 0.001, "max": 0.05, "step": 0.001},
    "trailing_stop_distance_atr": {"section": "risk", "type": float, "label": "Distancia trailing stop (ATR)", "min": 0.5, "max": 5.0, "step": 0.5},
    "partial_exit_pct": {"section": "risk", "type": float, "label": "% salida parcial", "min": 0.1, "max": 1.0, "step": 0.1},
    "partial_exit_r_multiple": {"section": "risk", "type": float, "label": "R múltiple para salida parcial", "min": 0.5, "max": 5.0, "step": 0.5},
    "rsi_oversold": {"section": "risk", "type": float, "label": "RSI sobreventa", "min": 10, "max": 50},
    "rsi_overbought": {"section": "risk", "type": float, "label": "RSI sobrecompra", "min": 50, "max": 90},
    "high_volatility_atr_threshold": {"section": "risk", "type": float, "label": "Umbral alta volatilidad ATR %", "min": 0.01, "max": 0.10, "step": 0.005},
    "exchange_stop_loss": {"section": "risk", "type": bool, "label": "Stop-loss en exchange"},
    "limit_order_timeout": {"section": "risk", "type": int, "label": "Timeout orden límite (s)", "min": 5, "max": 120, "step": 5},
    "min_volatility_atr_pct": {"section": "risk", "type": float, "label": "ATR % mínimo para operar", "min": 0.0005, "max": 0.01, "step": 0.0005},
    "analysis_interval": {"section": "trading", "type": int, "label": "Intervalo de análisis (s)", "min": 60, "max": 7200, "step": 30},
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
