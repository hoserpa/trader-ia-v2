"""Datos de mercado reales de Kraken para el broker (nunca modifica el portfolio).

El modo demo necesita usar los mismos datos de mercado reales que el modo real para
decidir si un par permite margen/short y con qué leverage. Esta capa consulta el
exchange real (solo lectura) y expone el soporte de margen por par.
"""
import asyncio
from typing import Optional

from loguru import logger


def _parse_leverage(raw) -> Optional[int]:
    """Extrae el maximo leverage numerico de un valor de Kraken (str, int, float o lista)."""
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
        if raw is None:
            return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def margin_support_from_markets(markets: dict, pairs: list) -> dict:
    """Deriva el soporte de margen por par desde los markets de ccxt.

    Kraken expone en cada market la informacion nativa 'leverage_buy'/'leverage_sell'.
    Un par soporta short con margen solo si permite leverage de venta.

    Args:
        markets: dict ccxt de markets cargados ({symbol: market}).
        pairs: lista de pares en formato unificado ccxt (p.ej. "BTC/EUR").

    Returns:
        dict {pair: {"symbol": str, "in_markets": bool, "long_leverage": int|None,
                      "short_leverage": int|None, "margin_ok": bool, "short_ok": bool}}
    """
    result = {}
    for pair in pairs:
        market = markets.get(pair)
        if not market:
            result[pair] = {
                "symbol": pair,
                "in_markets": False,
                "long_leverage": None,
                "short_leverage": None,
                "margin_ok": False,
                "short_ok": False,
            }
            continue
        info = market.get("info", {})
        long_lev = _parse_leverage(info.get("leverage_buy"))
        short_lev = _parse_leverage(info.get("leverage_sell"))
        result[pair] = {
            "symbol": pair,
            "in_markets": True,
            "long_leverage": long_lev,
            "short_leverage": short_lev,
            "margin_ok": bool(long_lev or short_lev),
            "short_ok": bool(short_lev and short_lev > 0),
        }
    return result


async def fetch_margin_support(exchange, pairs: list, load_markets: bool = True) -> dict:
    """Consulta los markets reales del exchange y retorna el soporte de margen por par.

    Args:
        exchange: instancia ccxt async (kraken).
        pairs: lista de pares en formato ccxt unificado.
        load_markets: si True, carga los markets desde el exchange (solo lectura).

    Returns:
        dict en el mismo formato que margin_support_from_markets.
    """
    try:
        if load_markets:
            markets = await exchange.load_markets()
        else:
            markets = getattr(exchange, "markets", {})
    except Exception as e:
        logger.error(f"No se pudieron cargar markets reales para validar margen: {e}")
        return {p: {
            "symbol": p, "in_markets": False, "long_leverage": None,
            "short_leverage": None, "margin_ok": False, "short_ok": False,
        } for p in pairs}
    return margin_support_from_markets(markets, pairs)


async def demo_sync() -> None:
    """No-op: presente para paridad de firma si se necesita en demo."""
    pass
