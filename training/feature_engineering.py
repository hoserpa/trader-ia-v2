"""Feature engineering: calcula indicadores, genera features y etiquetas.

Usage:
    python feature_engineering.py --data output/data --output output/features
"""
import argparse
from pathlib import Path
import sys
import pandas as pd
import numpy as np
from loguru import logger

Path("logs").mkdir(exist_ok=True)

sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
from indicators.technical import calculate_indicators, _atr
from indicators.features import FeatureBuilder


LABEL_LOOKAHEAD = 3
LABEL_THRESHOLD = 0.008
LABEL_ATR_MULTIPLIER = 1.5

# Labeling event-based (resultado real de la estrategia TP/SL)
DEFAULT_HORIZON = 32  # 8h en velas de 15m (máx. horas de posición del bot)
DEFAULT_SL_MULT = 2.5
DEFAULT_TP_MULT = 3.0


def load_data(data_dir: Path) -> dict[str, pd.DataFrame]:
    """Carga todos los archivos parquet del directorio."""
    data = {}
    for f in data_dir.glob("*.parquet"):
        stem = f.stem
        pair_code = stem.split("_")[0] if "_" in stem else stem
        pair = pair_code.replace("-", "/")
        df = pd.read_parquet(f)
        data[pair] = df
        logger.info(f"Cargado {pair}: {len(df)} velas")
    return data


def generate_labels_v1(df: pd.DataFrame) -> pd.Series:
    """Etiquetas con threshold fijo (legacy, para comparación).
    BUY: precio sube > LABEL_THRESHOLD en las próximas LABEL_LOOKAHEAD velas
    SELL: precio baja > LABEL_THRESHOLD
    HOLD: resto
    """
    labels = []
    for i in range(len(df)):
        if i + LABEL_LOOKAHEAD >= len(df):
            labels.append(np.nan)
            continue
        current_price = df["close"].iloc[i]
        future_price = df["close"].iloc[i + LABEL_LOOKAHEAD]
        pct_change = (future_price - current_price) / current_price
        if pct_change > LABEL_THRESHOLD:
            labels.append(2)
        elif pct_change < -LABEL_THRESHOLD:
            labels.append(0)
        else:
            labels.append(1)
    return pd.Series(labels, index=df.index)


def generate_labels_atr(df: pd.DataFrame, atr_col: str = "atr_14") -> pd.Series:
    """Etiquetas con threshold adaptativo por ATR.
    El umbral de BUY/SELL es: ATR_pct × LABEL_ATR_MULTIPLIER
    En baja volatilidad el threshold es más pequeño (más señales).
    En alta volatilidad el threshold es más grande (evita ruido).
    """
    labels = []
    atr_values = df[atr_col].values if atr_col in df.columns else None

    for i in range(len(df)):
        if i + LABEL_LOOKAHEAD >= len(df):
            labels.append(np.nan)
            continue

        current_price = df["close"].iloc[i]
        future_price = df["close"].iloc[i + LABEL_LOOKAHEAD]
        pct_change = (future_price - current_price) / current_price

        if atr_values is not None and not pd.isna(atr_values[i]) and atr_values[i] > 0 and current_price > 0:
            atr_pct = atr_values[i] / current_price
            threshold = max(atr_pct * LABEL_ATR_MULTIPLIER, 0.002)
        else:
            threshold = LABEL_THRESHOLD

        if pct_change > threshold:
            labels.append(2)
        elif pct_change < -threshold:
            labels.append(0)
        else:
            labels.append(1)

    return pd.Series(labels, index=df.index)


def generate_labels_event(
    df: pd.DataFrame,
    horizon: int = DEFAULT_HORIZON,
    sl_mult: float = DEFAULT_SL_MULT,
    tp_mult: float = DEFAULT_TP_MULT,
    atr_col: str = "atr_14",
) -> pd.Series:
    """Etiquetas basadas en el resultado real de la estrategia (event-based).

    Simula el resultado de una entrada con TP/SL como el bot real:
      - Long:  TP = entry + tp_mult*ATR, SL = entry - sl_mult*ATR
      - Short: TP = entry - tp_mult*ATR, SL = entry + sl_mult*ATR
    Dentro de `horizon` velas hacia adelante:
      - BUY (2):  el precio toca TP_long antes que SL_long (long rentable)
      - SELL (0): el precio toca TP_short antes que SL_short (short rentable)
      - HOLD (1): ninguna de las anteriores (SL primero o sin toque)
    """
    labels = np.full(len(df), 1, dtype=float)
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    atrs = df[atr_col].values if atr_col in df.columns else np.full(len(df), np.nan)
    n = len(df)

    for i in range(n):
        entry = closes[i]
        atr = atrs[i]
        if not np.isfinite(entry) or entry <= 0 or not np.isfinite(atr) or atr <= 0:
            labels[i] = np.nan
            continue

        end = min(n, i + 1 + horizon)
        window_h = highs[i + 1:end]
        window_l = lows[i + 1:end]

        tp_l = entry + tp_mult * atr
        sl_l = entry - sl_mult * atr
        tp_s = entry - tp_mult * atr
        sl_s = entry + sl_mult * atr

        idx_tp_l = np.where(window_h >= tp_l)[0]
        idx_sl_l = np.where(window_l <= sl_l)[0]
        first_tp_l = idx_tp_l[0] if len(idx_tp_l) else None
        first_sl_l = idx_sl_l[0] if len(idx_sl_l) else None

        idx_tp_s = np.where(window_l <= tp_s)[0]
        idx_sl_s = np.where(window_h >= sl_s)[0]
        first_tp_s = idx_tp_s[0] if len(idx_tp_s) else None
        first_sl_s = idx_sl_s[0] if len(idx_sl_s) else None

        long_ok = first_tp_l is not None and (first_sl_l is None or first_tp_l <= first_sl_l)
        short_ok = first_tp_s is not None and (first_sl_s is None or first_tp_s <= first_sl_s)

        if long_ok and not short_ok:
            labels[i] = 2
        elif short_ok and not long_ok:
            labels[i] = 0
        elif long_ok and short_ok:
            first_tp = min(first_tp_l, first_tp_s)
            if first_tp == first_tp_l:
                labels[i] = 2
            else:
                labels[i] = 0
        else:
            labels[i] = 1

    return pd.Series(labels, index=df.index)


def process_pair_data(
    df: pd.DataFrame,
    pair: str,
    labeling: str = "event",
    horizon: int = DEFAULT_HORIZON,
    sl_mult: float = DEFAULT_SL_MULT,
    tp_mult: float = DEFAULT_TP_MULT,
) -> pd.DataFrame:
    """Procesa datos de un par: indicadores + features + etiquetas."""
    logger.info(f"Procesando {pair}...")

    min_rows = 220
    if len(df) < min_rows:
        logger.warning(f"Datos insuficientes para {pair}: {len(df)} < {min_rows}")
        return pd.DataFrame()

    df = df.copy()
    df = calculate_indicators(df)

    builder = FeatureBuilder()
    features_df = builder.build_features_batch(df, pair=pair)

    if features_df.empty:
        logger.warning(f"No se pudieron generar features para {pair}")
        return pd.DataFrame()

    valid_indices = features_df.index

    if labeling == "event":
        for h in [8, 16, horizon]:
            label_la = generate_labels_event(df, horizon=h, sl_mult=sl_mult, tp_mult=tp_mult)
            features_df[f"label_{h}"] = label_la.loc[valid_indices].values
        features_df["label"] = features_df[f"label_{horizon}"].copy()
    else:
        labels = generate_labels_atr(df)
        for lookahead in [3, 6, 12]:
            global LABEL_LOOKAHEAD
            original_lookahead = LABEL_LOOKAHEAD
            LABEL_LOOKAHEAD = lookahead
            labels_la = generate_labels_atr(df)
            LABEL_LOOKAHEAD = original_lookahead
            features_df[f"label_{lookahead}"] = labels_la.loc[valid_indices].values
        features_df["label"] = labels.loc[valid_indices].values

    features_df["pair"] = pair
    features_df["timestamp"] = df["timestamp"].iloc[valid_indices].values
    features_df["close"] = df["close"].iloc[valid_indices].values
    features_df["high"] = df["high"].iloc[valid_indices].values
    features_df["low"] = df["low"].iloc[valid_indices].values
    features_df["atr_14"] = df["atr_14"].iloc[valid_indices].values
    features_df["volume"] = df["volume"].iloc[valid_indices].values

    features_df = features_df.dropna(subset=["label"])
    if labeling == "event":
        features_df = features_df.dropna(subset=[f"label_{h}" for h in [8, 16, horizon]])
    else:
        features_df = features_df.dropna(subset=[f"label_{la}" for la in [3, 6, 12]])

    logger.info(f"  Features generadas: {len(features_df)} muestras")
    return features_df


def main():
    parser = argparse.ArgumentParser(description="Genera features y etiquetas")
    parser.add_argument("--data", type=str, default="output/data", help="Directorio con datos parquet")
    parser.add_argument("--output", type=str, default="output/features", help="Directorio de salida")
    parser.add_argument("--lookahead", type=int, default=3, help="Velas hacia adelante para labeling")
    parser.add_argument("--threshold", type=float, default=0.008, help="Umbral % para BUY/SELL (fallback)")
    parser.add_argument("--atr-multiplier", type=float, default=1.5, help="Multiplicador ATR para threshold adaptativo")
    parser.add_argument("--labeling", type=str, default="event", choices=["event", "atr"],
                        help="Tipo de labeling: 'event' (TP/SL real de la estrategia) o 'atr' (legacy)")
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON, help="Velas de horizonte para event labeling")
    parser.add_argument("--sl-mult", type=float, default=DEFAULT_SL_MULT, help="Multiplicador ATR del stop-loss")
    parser.add_argument("--tp-mult", type=float, default=DEFAULT_TP_MULT, help="Multiplicador ATR del take-profit")
    args = parser.parse_args()

    global LABEL_LOOKAHEAD, LABEL_THRESHOLD, LABEL_ATR_MULTIPLIER
    LABEL_LOOKAHEAD = args.lookahead
    LABEL_THRESHOLD = args.threshold
    LABEL_ATR_MULTIPLIER = args.atr_multiplier

    data_dir = Path(args.data)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Cargando datos de {data_dir}")
    logger.info(f"Archivos parquet encontrados: {list(data_dir.glob('*.parquet'))}")
    logger.info(f"Labeling: {args.labeling} | lookahead={LABEL_LOOKAHEAD}, atr_multiplier={LABEL_ATR_MULTIPLIER}, fallback_threshold={LABEL_THRESHOLD}")
    if args.labeling == "event":
        logger.info(f"Event labeling: horizon={args.horizon}, sl_mult={args.sl_mult}, tp_mult={args.tp_mult}")
    data = load_data(data_dir)

    if not data:
        logger.error("No se encontraron datos")
        return

    all_features = []
    for pair, df in data.items():
        features_df = process_pair_data(
            df, pair, labeling=args.labeling,
            horizon=args.horizon, sl_mult=args.sl_mult, tp_mult=args.tp_mult,
        )
        if not features_df.empty:
            all_features.append(features_df)

    if not all_features:
        logger.error("No se generaron features")
        return

    combined = pd.concat(all_features, ignore_index=True)

    output_file = output_dir / "features_with_labels.parquet"
    combined.to_parquet(output_file, index=False)

    logger.info(f"Total muestras: {len(combined)}")
    label_cols = [f"label_{h}" for h in [8, 16, args.horizon]] if args.labeling == "event" else [f"label_{la}" for la in [3, 6, 12]]
    for col in label_cols + ["label"]:
        if col in combined.columns:
            logger.info(f"Distribución {col}:")
            label_counts = combined[col].value_counts().sort_index()
            for label, count in label_counts.items():
                pct = count / len(combined) * 100
                name = {0: "SELL", 1: "HOLD", 2: "BUY"}.get(label, "UNK")
                logger.info(f"  {name}: {count} ({pct:.1f}%)")

    logger.info(f"Guardado en {output_file}")


if __name__ == "__main__":
    logger.add("logs/feature_engineering.log", rotation="50 MB", level="INFO")
    main()
