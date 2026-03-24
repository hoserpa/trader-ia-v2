# Scripts de Entrenamiento del Modelo

Pipeline completo para entrenar el modelo LightGBM del bot de trading.

## Requisitos

```bash
pip install -r requirements_training.txt
```

Opcional (para backtest avanzado):
```bash
# Instalar TA-Lib desde fuente
# https://ta-lib.org/
```

## Pipeline de Entrenamiento

### 1. Descargar datos históricos

```bash
python fetch_historical_data.py \
    --pairs BTC-EUR,ETH-EUR,SOL-EUR \
    --timeframe 5m \
    --days 365 \
    --output output/data
```

Esto descarga 1 año de datos OHLCV en timeframe 5m y los guarda en formato Parquet.

### 2. Generar features y etiquetas

```bash
python feature_engineering.py \
    --data output/data \
    --output output/features \
    --lookahead 3 \
    --threshold 0.008
```

- `lookahead`: número de velas hacia adelante para calcular labels (default: 3 = 15 min)
- `threshold`: % mínimo de cambio para BUY/SELL (default: 0.008 = 0.8%)

### 3. Entrenar modelo

```bash
python train_model.py \
    --data output/features/features_with_labels.parquet \
    --output output/model
```

El modelo usa:
- Split temporal: 70% train, 15% val, 15% test
- Normalización: RobustScaler
- LightGBM multiclase (SELL=0, HOLD=1, BUY=2)
- Optimización de umbral de confianza

### 4. Evaluar modelo

```bash
python evaluate_model.py \
    --model output/model/trained_model.pkl \
    --data output/features/features_with_labels.parquet \
    --output output/evaluation
```

Genera:
- Matriz de confusión (`confusion_matrix.png`)
- Curvas ROC (`roc_curves.png`)
- Métricas (`evaluation_results.json`)

### 5. Exportar a Raspberry Pi

```bash
python export_model.py \
    --model output/model \
    --target ../bot/model
```

Copia los archivos necesarios al directorio del bot.

## Métricas Mínimas (Spec)

| Métrica | Mínimo |
|---------|--------|
| Precision BUY | ≥ 0.60 |
| Precision SELL | ≥ 0.60 |
| Sharpe Ratio | ≥ 1.0 |
| Max Drawdown | ≤ 15% |

## Uso con Google Colab

1. Sube los scripts a Colab
2. Ejecuta los pasos 1-4
3. Descarga los archivos:
   - `output/model/trained_model.pkl`
   - `output/model/scaler.pkl`
   - `output/model/model_metadata.json`
4. Copia a la Raspberry Pi

## Estructura de Directorios

```
training/
├── requirements_training.txt
├── fetch_historical_data.py    # Descarga datos
├── feature_engineering.py      # Features + labels
├── train_model.py              # Entrenamiento
├── evaluate_model.py           # Evaluación + backtest
├── export_model.py             # Export a RPi
├── output/
│   ├── data/                   # Datos parquet
│   ├── features/               # Features + labels
│   ├── model/                  # Modelo entrenado
│   └── evaluation/             # Métricas y gráficos
└── logs/                       # Logs de ejecución
```

## Notas

- Los datos descargados se guardan en formato Parquet (más eficiente que CSV)
- El FeatureBuilder reutiliza el código del bot (`bot/indicators/`)
- El backtest usa vectorbt con comisiones reales de Coinbase (0.6% taker)
