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


def load_data(data_dir: Path) -> dict[str, pd.DataFrame]:
    """Carga todos los archivos parquet del directorio."""
    data = {}
    for f in data_dir.glob("*.parquet"):
        pair = f.stem.replace("_5m", "").replace("_", "/")
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


def process_pair_data(df: pd.DataFrame, pair: str) -> pd.DataFrame:
    """Procesa datos de un par: indicadores + features + etiquetas."""
    logger.info(f"Procesando {pair}...")

    min_rows = 220
    if len(df) < min_rows:
        logger.warning(f"Datos insuficientes para {pair}: {len(df)} < {min_rows}")
        return pd.DataFrame()

    df = df.copy()
    df = calculate_indicators(df)

    builder = FeatureBuilder()
    features_df = builder.build_features_batch(df)

    if features_df.empty:
        logger.warning(f"No se pudieron generar features para {pair}")
        return pd.DataFrame()

    labels = generate_labels_atr(df)
    valid_indices = features_df.index

    features_df = features_df.loc[valid_indices].copy()

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

    features_df = features_df.dropna(subset=["label"])
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
    logger.info(f"Labeling: lookahead={LABEL_LOOKAHEAD}, atr_multiplier={LABEL_ATR_MULTIPLIER}, fallback_threshold={LABEL_THRESHOLD}")
    data = load_data(data_dir)

    if not data:
        logger.error("No se encontraron datos")
        return

    all_features = []
    for pair, df in data.items():
        features_df = process_pair_data(df, pair)
        if not features_df.empty:
            all_features.append(features_df)

    if not all_features:
        logger.error("No se generaron features")
        return

    combined = pd.concat(all_features, ignore_index=True)

    output_file = output_dir / "features_with_labels.parquet"
    combined.to_parquet(output_file, index=False)

    logger.info(f"Total muestras: {len(combined)}")
    for la in [3, 6, 12]:
        col = f"label_{la}"
        if col in combined.columns:
            logger.info(f"Distribución label_{la}:")
            label_counts = combined[col].value_counts().sort_index()
            for label, count in label_counts.items():
                pct = count / len(combined) * 100
                name = {0: "SELL", 1: "HOLD", 2: "BUY"}.get(label, "UNK")
                logger.info(f"  {name}: {count} ({pct:.1f}%)")

    logger.info(f"Guardado en {output_file}")


if __name__ == "__main__":
    logger.add("logs/feature_engineering.log", rotation="50 MB", level="INFO")
    main()
