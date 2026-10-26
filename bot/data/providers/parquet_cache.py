"""Proveedor de datos con caché Parquet para backtests reproducibles.

Descarga y cachea OHLCV diarios en archivos Parquet.
Si los datos ya están cacheados, los lee localmente sin descarga.
Soporta merge incremental (descarga solo lo que falta).
"""
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger


class ParquetCacheProvider:
    """Lee/escribe OHLCV en caché Parquet local."""

    def __init__(self, cache_dir: str = "data/trend_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol}.parquet"

    def _validate_bars(self, df: pd.DataFrame) -> pd.DataFrame:
        """Valida OHLCV según §12."""
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(set(df.columns)):
            missing = required - set(df.columns)
            raise ValueError(f"Columnas faltantes: {missing}")

        if df.index.duplicated().any():
            df = df[~df.index.duplicated(keep="last")]

        df = df.sort_index()

        bad_high = df["high"] < df[["open", "close"]].max(axis=1)
        bad_low = df["low"] > df[["open", "close"]].min(axis=1)
        if bad_high.any() or bad_low.any():
            n_bad = int(bad_high.sum() + bad_low.sum())
            logger.warning(f"{n_bad} velas con OHLC inválido en {df.index.name}, corrigiendo")
            df["high"] = df[["open", "high", "close"]].max(axis=1)
            df["low"] = df[["open", "low", "close"]].min(axis=1)

        df["volume"] = df["volume"].clip(lower=0)

        return df

    def load(self, symbol: str) -> Optional[pd.DataFrame]:
        """Carga datos cacheados. Retorna None si no existe."""
        path = self._path(symbol)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            if df.empty:
                return None
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            return self._validate_bars(df)
        except Exception as e:
            logger.warning(f"Error leyendo caché {symbol}: {e}")
            return None

    def save(self, symbol: str, df: pd.DataFrame) -> int:
        """Guarda/merge datos en caché. Retorna número de filas."""
        if df.empty:
            return 0

        df = self._validate_bars(df)

        existing = self.load(symbol)
        if existing is not None:
            combined = pd.concat([existing, df])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined = combined.sort_index()
        else:
            combined = df

        combined.to_parquet(self._path(symbol))
        return len(combined)

    def get_last_date(self, symbol: str) -> Optional[date]:
        """Retorna la última fecha cacheada para un símbolo."""
        df = self.load(symbol)
        if df is None or df.empty:
            return None
        return df.index[-1].date()

    def fetch_missing(
        self,
        symbol: str,
        start: date,
        end: date,
        download_fn=None,
    ) -> pd.DataFrame:
        """Si faltan datos en el rango [start, end], descarga y cachea.

        download_fn: callable(symbol, start, end) -> pd.DataFrame
        Retorna DataFrame con todos los datos (cacheados + descargados).
        """
        cached = self.load(symbol)
        if cached is not None:
            cached_start = cached.index[0].date()
            cached_end = cached.index[-1].date()

            if cached_start <= start and cached_end >= end:
                mask = (cached.index >= pd.Timestamp(start)) & (cached.index <= pd.Timestamp(end))
                return cached.loc[mask]

            new_start = cached_end + timedelta(days=1)
            if new_start <= end and download_fn:
                new_data = download_fn(symbol, new_start, end)
                if new_data is not None and not new_data.empty:
                    self.save(symbol, new_data)
                    cached = self.load(symbol)

            if cached is not None:
                mask = (cached.index >= pd.Timestamp(start)) & (cached.index <= pd.Timestamp(end))
                return cached.loc[mask]

        if download_fn:
            new_data = download_fn(symbol, start, end)
            if new_data is not None and not new_data.empty:
                self.save(symbol, new_data)
                cached = self.load(symbol)
                if cached is not None:
                    mask = (cached.index >= pd.Timestamp(start)) & (cached.index <= pd.Timestamp(end))
                    return cached.loc[mask]

        return pd.DataFrame()
