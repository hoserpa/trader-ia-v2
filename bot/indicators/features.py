"""Construcción del vector de features para el modelo de ML."""
import math
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np
from loguru import logger

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "bot"))
from indicators.technical import calculate_indicators


PAIR_MAP = {"BTC/EUR": 0, "ETH/EUR": 1, "SOL/EUR": 2}

HTF_COLS = [
    "rsi_14", "macd_hist", "bb_pct_b", "price_vs_ema21",
    "atr_pct", "volume_ratio", "volatility_regime",
]


def _safe_htf_float(val, default: float = 0.0) -> float:
    """Safe float extraction for HTF features, handling None and NaN."""
    if val is None:
        return default
    try:
        f = float(val)
        return default if (f != f) else f  # NaN check: x != x
    except (TypeError, ValueError):
        return default


def resample_15m_to_htf(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Resamplea velas 15m a una timeframe mayor (1h, 4h)."""
    cols = [c for c in ["timestamp", "open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[cols].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    agg = df.resample(freq).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna().reset_index()
    return agg


def build_htf_features_for_live(candles_15m: pd.DataFrame, pair: str) -> dict:
    """Genera features HTF (1h/4h) a partir de velas 15m para inferencia live.

    Extrae indicadores directamente del DataFrame HTF (sin pasar por FeatureBuilder
    que requiere MIN_ROWS=55). Necesita al menos 20 velas HTF para MACD/RSI.

    Returns: dict con keys h1_rsi_14, h1_macd_hist, etc. Valores 0.0 si no disponibles.
    """
    MIN_HTF_CANDLES = 20
    htf_features = {}
    for freq, prefix in [("1h", "h1_"), ("4h", "h4_")]:
        try:
            df_htf = resample_15m_to_htf(candles_15m, freq)
            if len(df_htf) < MIN_HTF_CANDLES:
                logger.debug(f"HTF {freq}: {len(df_htf)} < {MIN_HTF_CANDLES}, zero-filling")
                for col in HTF_COLS:
                    htf_features[f"{prefix}{col}"] = 0.0
                continue
            df_htf = calculate_indicators(df_htf)
            last = df_htf.iloc[-1]
            price = last["close"]
            if price <= 0 or not np.isfinite(price):
                for col in HTF_COLS:
                    htf_features[f"{prefix}{col}"] = 0.0
                continue
            htf_features[f"{prefix}rsi_14"] = _safe_htf_float(last.get("rsi_14"), 50.0) / 100.0
            htf_features[f"{prefix}macd_hist"] = _safe_htf_float(last.get("macd_hist"), 0.0) / (price + 1e-10)
            htf_features[f"{prefix}bb_pct_b"] = _safe_htf_float(last.get("bb_pct_b"), 0.5)
            ema21_val = _safe_htf_float(last.get("ema_21"), price)
            htf_features[f"{prefix}price_vs_ema21"] = (price / ema21_val - 1.0) if price > 0 and ema21_val > 0 else 0.0
            atr = _safe_htf_float(last.get("atr_14"), 0.0)
            htf_features[f"{prefix}atr_pct"] = atr / price if price > 0 else 0.0
            htf_features[f"{prefix}volume_ratio"] = min(_safe_htf_float(last.get("volume_ratio"), 1.0), 10.0)
            atr_pct = htf_features[f"{prefix}atr_pct"]
            if atr_pct < 0.01:
                htf_features[f"{prefix}volatility_regime"] = 0.0
            elif atr_pct < 0.03:
                htf_features[f"{prefix}volatility_regime"] = 0.5
            else:
                htf_features[f"{prefix}volatility_regime"] = 1.0
        except Exception as e:
            logger.warning(f"HTF features {freq} failed for {pair}: {e}")
            for col in HTF_COLS:
                htf_features[f"{prefix}{col}"] = 0.0
    return htf_features


class FeatureBuilder:
    """Transforma velas + indicadores en el vector de features para LightGBM."""

    MIN_ROWS = 55

    def build_features(self, df: pd.DataFrame, pair: Optional[str] = None, htf_features: Optional[dict] = None) -> Optional[pd.Series]:
        """Recibe DataFrame con indicadores ya calculados.
        Retorna Series de features para la última vela, o None si hay datos insuficientes.
        """
        if len(df) < self.MIN_ROWS:
            logger.warning(f"Datos insuficientes para features: {len(df)} < {self.MIN_ROWS}")
            return None

        last = df.iloc[-1]
        prev3 = df.iloc[-4] if len(df) >= 4 else df.iloc[0]
        prev6 = df.iloc[-7] if len(df) >= 7 else df.iloc[0]

        price = last["close"]
        if price == 0:
            return None

        features = {}

        features["price_change_1"] = self._safe_pct(price, df.iloc[-2]["close"] if len(df) >= 2 else price)
        features["price_change_3"] = self._safe_pct(price, prev3["close"])
        features["price_change_6"] = self._safe_pct(price, prev6["close"])

        features["rsi_14"] = self._safe_float(last.get("rsi_14"), 50.0) / 100.0
        rsi_slope = 0.0
        if len(df) >= 4 and "rsi_14" in df.columns:
            rsi_vals = df["rsi_14"].dropna()
            if len(rsi_vals) >= 4:
                rsi_slope = (rsi_vals.iloc[-1] - rsi_vals.iloc[-4]) / 100.0
        features["rsi_slope"] = rsi_slope

        features["macd"] = self._safe_float(last.get("macd"), 0.0) / (price + 1e-10)
        features["macd_signal"] = self._safe_float(last.get("macd_signal"), 0.0) / (price + 1e-10)
        features["macd_hist"] = self._safe_float(last.get("macd_hist"), 0.0) / (price + 1e-10)
        if len(df) >= 2 and "macd" in df.columns and "macd_signal" in df.columns:
            prev_macd = self._safe_float(df["macd"].iloc[-2] if pd.notna(df["macd"].iloc[-2]) else None)
            prev_sig = self._safe_float(df["macd_signal"].iloc[-2] if pd.notna(df["macd_signal"].iloc[-2]) else None)
            curr_macd = self._safe_float(last.get("macd"))
            curr_sig = self._safe_float(last.get("macd_signal"))
            if prev_macd is not None and prev_sig is not None and curr_macd is not None and curr_sig is not None:
                if prev_macd < prev_sig and curr_macd >= curr_sig:
                    features["macd_cross"] = 1.0
                elif prev_macd > prev_sig and curr_macd <= curr_sig:
                    features["macd_cross"] = -1.0
                else:
                    features["macd_cross"] = 0.0
            else:
                features["macd_cross"] = 0.0
        else:
            features["macd_cross"] = 0.0

        features["bb_pct_b"] = self._safe_float(last.get("bb_pct_b"), 0.5)
        features["bb_bandwidth"] = self._safe_float(last.get("bb_bandwidth"), 0.0)

        atr = self._safe_float(last.get("atr_14"), 0.0)
        features["atr_pct"] = atr / price if price > 0 else 0.0

        features["price_vs_ema9"] = self._safe_ratio(price, last.get("ema_9", price))
        features["price_vs_ema21"] = self._safe_ratio(price, last.get("ema_21", price))
        features["price_vs_sma50"] = self._safe_ratio(price, last.get("sma_50", price))
        features["ema9_vs_ema21"] = self._safe_ratio(
            self._safe_float(last.get("ema_9"), price),
            self._safe_float(last.get("ema_21"), price)
        )

        features["volume_ratio"] = min(self._safe_float(last.get("volume_ratio"), 1.0), 10.0)
        if len(df) >= 2:
            prev_vol = df["volume"].iloc[-2]
            features["volume_change"] = self._safe_pct(last["volume"], prev_vol)
        else:
            features["volume_change"] = 0.0

        if "obv" in df.columns and len(df) >= 5:
            obv_vals = df["obv"].dropna()
            if len(obv_vals) >= 5:
                obv_slope = (obv_vals.iloc[-1] - obv_vals.iloc[-5]) / (abs(obv_vals.iloc[-5]) + 1e-10)
                features["obv_slope"] = max(min(obv_slope, 5.0), -5.0)
            else:
                features["obv_slope"] = 0.0
        else:
            features["obv_slope"] = 0.0

        features["stoch_k"] = self._safe_float(last.get("stoch_k"), 50.0) / 100.0
        features["stoch_d"] = self._safe_float(last.get("stoch_d"), 50.0) / 100.0
        features["williams_r"] = (self._safe_float(last.get("williams_r"), -50.0) + 100) / 100.0
        features["cci_20"] = max(min(self._safe_float(last.get("cci_20"), 0.0) / 200.0, 2.0), -2.0)

        ts = last.get("timestamp", datetime.utcnow())
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        hour = ts.hour if hasattr(ts, "hour") else 0
        features["hour_sin"] = math.sin(hour * 2 * math.pi / 24)
        features["hour_cos"] = math.cos(hour * 2 * math.pi / 24)
        features["day_of_week"] = ts.weekday() / 6.0 if hasattr(ts, "weekday") else 0.0
        features["is_weekend"] = 1.0 if hasattr(ts, "weekday") and ts.weekday() >= 5 else 0.0

        atr_pct = features["atr_pct"]
        if atr_pct < 0.01:
            features["volatility_regime"] = 0.0
        elif atr_pct < 0.03:
            features["volatility_regime"] = 0.5
        else:
            features["volatility_regime"] = 1.0

        features["pair_id"] = PAIR_MAP.get(pair, 0)

        if htf_features:
            for col in HTF_COLS:
                features[f"h1_{col}"] = htf_features.get(f"h1_{col}", 0.0)
                features[f"h4_{col}"] = htf_features.get(f"h4_{col}", 0.0)

        return pd.Series(features)

    def build_features_batch(self, df: pd.DataFrame, pair: Optional[str] = None) -> pd.DataFrame:
        """Para entrenamiento: construye features de todas las filas válidas."""
        feature_rows = []
        for i in range(self.MIN_ROWS, len(df)):
            subset = df.iloc[:i+1]
            row = self.build_features(subset, pair=pair)
            if row is not None:
                row["index"] = i
                feature_rows.append(row)
        return pd.DataFrame(feature_rows).set_index("index") if feature_rows else pd.DataFrame()

    @staticmethod
    def _safe_float(val, default: float = 0.0) -> float:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return default
        return float(val)

    @staticmethod
    def _safe_pct(current, previous) -> float:
        if not previous or previous == 0:
            return 0.0
        return (float(current) - float(previous)) / float(previous)

    @staticmethod
    def _safe_ratio(a, b) -> float:
        b = float(b) if b else 0
        if b == 0:
            return 1.0
        return float(a) / b - 1.0
