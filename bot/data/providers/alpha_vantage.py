"""Proveedor Alpha Vantage para bootstrap histórico de ETFs.

Opcional: solo se usa si se necesita descargar datos históricos
sin IBKR. Requiere API key (gratuita, 25 req/día sin key premium).
"""
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from loguru import logger


class AlphaVantageProvider:
    """Descarga OHLCV diarios desde Alpha Vantage (FUNCTION=TIME_SERIES_DAILY)."""

    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """Descarga OHLCV diarios. Limitado a ~20 años de datos."""
        if not self.api_key:
            logger.warning("Alpha Vantage: API key no configurada")
            return pd.DataFrame()

        try:
            import httpx
        except ImportError:
            logger.error("httpx no instalado, no se puede usar Alpha Vantage")
            return pd.DataFrame()

        params = {
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "apikey": self.api_key,
            "outputsize": "full",
            "datatype": "json",
        }

        try:
            resp = httpx.get(self.BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Alpha Vantage error para {symbol}: {e}")
            return pd.DataFrame()

        ts_key = "Time Series (Daily)"
        if ts_key not in data:
            logger.warning(f"Alpha Vantage: sin datos para {symbol}")
            return pd.DataFrame()

        rows = []
        for ts, vals in data[ts_key].items():
            dt = pd.Timestamp(ts)
            if dt.date() < start or dt.date() > end:
                continue
            rows.append({
                "date": dt,
                "open": float(vals["1. open"]),
                "high": float(vals["2. high"]),
                "low": float(vals["3. low"]),
                "close": float(vals["4. close"]),
                "volume": float(vals["6. volume"]),
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df.index = pd.DatetimeIndex([r["date"] for r in rows])
        df.index.name = "date"
        df = df.drop(columns=["date"], errors="ignore")
        df = df.sort_index()

        return df

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Último precio de cierre disponible."""
        bars = self.get_daily_bars(symbol, date.today() - timedelta(days=10), date.today())
        if bars.empty:
            return None
        return float(bars["close"].iloc[-1])
