"""Cálculo de indicadores técnicos usando pandas-ta."""
import pandas as pd
import pandas_ta as ta
from loguru import logger


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Recibe DataFrame con columnas: timestamp, open, high, low, close, volume.
    Retorna el mismo DataFrame enriquecido con todos los indicadores.
    Requiere mínimo 200 filas para indicadores estables.
    """
    if len(df) < 50:
        logger.warning(f"Datos insuficientes para indicadores: {len(df)} velas (mínimo 50)")
        return df

    df = df.copy()

    df["rsi_14"] = ta.rsi(df["close"], length=14)

    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        df["macd"] = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_hist"] = macd["MACDh_12_26_9"]

    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None:
        df["bb_upper"] = bb["BBU_20_2.0"]
        df["bb_mid"] = bb["BBM_20_2.0"]
        df["bb_lower"] = bb["BBL_20_2.0"]
        df["bb_pct_b"] = bb["BBP_20_2.0"]
        df["bb_bandwidth"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

    df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    df["ema_9"] = ta.ema(df["close"], length=9)
    df["ema_21"] = ta.ema(df["close"], length=21)
    df["sma_50"] = ta.sma(df["close"], length=50)
    df["sma_200"] = ta.sma(df["close"], length=200)

    stoch = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3)
    if stoch is not None:
        df["stoch_k"] = stoch["STOCHk_14_3_3"]
        df["stoch_d"] = stoch["STOCHd_14_3_3"]

    df["williams_r"] = ta.willr(df["high"], df["low"], df["close"], length=14)

    df["cci_20"] = ta.cci(df["high"], df["low"], df["close"], length=20)

    df["obv"] = ta.obv(df["close"], df["volume"])

    df["volume_sma_20"] = ta.sma(df["volume"], length=20)
    df["volume_ratio"] = df["volume"] / df["volume_sma_20"].replace(0, 1)

    return df


def get_atr(df: pd.DataFrame) -> float:
    """Retorna el ATR actual (última fila)."""
    if "atr_14" not in df.columns or df["atr_14"].isna().all():
        return 0.0
    return float(df["atr_14"].iloc[-1])


def get_current_price(df: pd.DataFrame) -> float:
    """Retorna el precio de cierre de la última vela."""
    return float(df["close"].iloc[-1])
