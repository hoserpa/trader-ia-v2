"""Cálculo de indicadores técnicos usando pandas/numpy (sin pandas-ta)."""
import pandas as pd
import numpy as np
from loguru import logger


def _ema(series: pd.Series, length: int) -> pd.Series:
    """Calcula EMA (Exponential Moving Average)."""
    return series.ewm(span=length, adjust=False).mean()


def _sma(series: pd.Series, length: int) -> pd.Series:
    """Calcula SMA (Simple Moving Average)."""
    return series.rolling(window=length).mean()


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Calcula RSI (Relative Strength Index)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = (-delta).where(delta < 0, 0)
    
    avg_gain = gain.ewm(span=length, adjust=False).mean()
    avg_loss = loss.ewm(span=length, adjust=False).mean()
    
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Calcula ATR (Average True Range)."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=length, adjust=False).mean()


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Calcula MACD. Retorna DataFrame con MACD, signal, histogram."""
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    
    return pd.DataFrame({
        "MACD": macd_line,
        "MACDs": signal_line,
        "MACDh": histogram
    })


def _bollinger_bands(series: pd.Series, length: int = 20, std_mult: float = 2.0):
    """Calcula Bollinger Bands."""
    sma = _sma(series, length)
    std = series.rolling(window=length).std()
    
    upper = sma + (std * std_mult)
    lower = sma - (std * std_mult)
    
    return pd.DataFrame({
        "BBU": upper,
        "BBM": sma,
        "BBL": lower
    })


def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3):
    """Calcula Stochastic Oscillator."""
    lowest_low = low.rolling(window=k).min()
    highest_high = high.rolling(window=k).max()
    
    k_percent = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    d_percent = k_percent.rolling(window=d).mean()
    
    return pd.DataFrame({
        "STOCHk": k_percent,
        "STOCHd": d_percent
    })


def _williams_r(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14):
    """Calcula Williams %R."""
    highest_high = high.rolling(window=length).max()
    lowest_low = low.rolling(window=length).min()
    
    wr = -100 * (highest_high - close) / (highest_high - lowest_low).replace(0, np.nan)
    return wr


def _cci(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 20):
    """Calcula CCI (Commodity Channel Index)."""
    typical_price = (high + low + close) / 3
    sma_tp = typical_price.rolling(window=length).mean()
    mad = typical_price.rolling(window=length).apply(lambda x: np.abs(x - x.mean()).mean())
    
    cci = (typical_price - sma_tp) / (0.015 * mad)
    return cci


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """Calcula OBV (On-Balance Volume)."""
    sign = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (sign * volume).cumsum()


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Recibe DataFrame con columnas: timestamp, open, high, low, close, volume.
    Retorna el mismo DataFrame enriquecido con todos los indicadores.
    Requiere mínimo 200 filas para indicadores estables.
    """
    if len(df) < 50:
        logger.warning(f"Datos insuficientes para indicadores: {len(df)} velas (mínimo 50)")
        return df

    df = df.copy()

    df["rsi_14"] = _rsi(df["close"], 14)
    df["rsi_14_slope"] = df["rsi_14"].diff()

    macd = _macd(df["close"])
    df["macd"] = macd["MACD"]
    df["macd_signal"] = macd["MACDs"]
    df["macd_hist"] = macd["MACDh"]
    df["macd_cross"] = np.sign(macd["MACD"] - macd["MACDs"])

    bb = _bollinger_bands(df["close"])
    df["bb_upper"] = bb["BBU"]
    df["bb_mid"] = bb["BBM"]
    df["bb_lower"] = bb["BBL"]
    df["bb_pct_b"] = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)
    df["bb_bandwidth"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

    df["atr_14"] = _atr(df["high"], df["low"], df["close"], 14)
    df["atr_pct"] = df["atr_14"] / df["close"]

    df["ema_9"] = _ema(df["close"], 9)
    df["ema_21"] = _ema(df["close"], 21)
    df["sma_50"] = _sma(df["close"], 50)
    df["sma_200"] = _sma(df["close"], 200)

    df["price_vs_ema9"] = df["close"] / df["ema_9"] - 1
    df["price_vs_ema21"] = df["close"] / df["ema_21"] - 1
    df["price_vs_sma50"] = df["close"] / df["sma_50"] - 1
    df["ema9_vs_ema21"] = df["ema_9"] / df["ema_21"] - 1

    stoch = _stochastic(df["high"], df["low"], df["close"])
    df["stoch_k"] = stoch["STOCHk"]
    df["stoch_d"] = stoch["STOCHd"]

    df["williams_r"] = _williams_r(df["high"], df["low"], df["close"])
    df["cci_20"] = _cci(df["high"], df["low"], df["close"])

    df["obv"] = _obv(df["close"], df["volume"])
    df["obv_slope"] = df["obv"].diff()

    df["volume_change"] = df["volume"].pct_change()
    df["volume_sma_20"] = _sma(df["volume"], 20)
    df["volume_ratio"] = df["volume"] / df["volume_sma_20"].replace(0, np.nan)

    return df


def get_atr(df: pd.DataFrame) -> float:
    """Retorna el ATR actual (última fila)."""
    if "atr_14" not in df.columns or df["atr_14"].isna().all():
        return 0.0
    return float(df["atr_14"].iloc[-1])


def get_current_price(df: pd.DataFrame) -> float:
    """Retorna el precio de cierre de la última vela."""
    return float(df["close"].iloc[-1])
