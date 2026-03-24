# Crypto Trader Bot

Bot de trading automatizado de criptomonedas usando aprendizaje automático (LightGBM) con interfaz web integrada.

## Características

- **Trading automático** en Coinbase Advanced Trade
- **Modelo ML** LightGBM para señales de compra/venta
- **Modo demo** para pruebas sin riesgo
- **Gestión de riesgo** integrada (stop-loss, take-profit, límites de posición)
- **Dashboard web** en tiempo real
- **Notificaciones Telegram**
- **Docker** listo para Raspberry Pi 3

## Requisitos

- Python 3.11+ (incluido en Docker)
- Redis (incluido en Docker)
- Docker + Docker Compose
- (Opcional) Git para clonar el repositorio

## Instalación

### Opción 1: Docker (Recomendado para Raspberry Pi)

```bash
# 1. Clonar repositorio
git clone https://github.com/hoserpa/trader-ia-v2.git
cd trader-ia-v2

# 2. Copiar configuración
cp .env.example .env
nano .env  # Editar con tus valores

# 3. Ejecutar servicios
docker-compose up -d --build

# 4. Verificar estado
docker-compose ps
docker-compose logs -f
```

### Opción 2: Local (Desarrollo)

```bash
# Instalar dependencias
pip install -r bot/requirements.txt
pip install -r api/requirements.txt

# Ejecutar Redis
docker run -d -p 6379:6379 redis:7-alpine

# Ejecutar bot
cd bot && python main.py

# Ejecutar API (en otra terminal)
cd api && uvicorn main:app --reload
```

## Configuración

Editar `.env` con tus valores:

```env
# Modo: demo o real
TRADING_MODE=demo

# Pares a operar
TRADING_PAIRS=BTC-EUR,ETH-EUR

# Claves Coinbase (solo para modo real)
COINBASE_API_KEY=tu_api_key
COINBASE_API_SECRET=tu_api_secret

# Contraseña API web
API_USERNAME=admin
API_PASSWORD=changeme
```

## Ejecución

### Bot de trading

```bash
cd bot
python main.py
```

### API + Dashboard

```bash
cd api
uvicorn main:app --host 0.0.0.0 --port 8000
```

Acceder a `http://localhost:8000` para ver el dashboard.

### Docker (Recomendado)

```bash
# Iniciar todos los servicios
docker-compose up -d --build

# Ver logs en tiempo real
docker-compose logs -f

# Detener servicios
docker-compose down

# Reiniciar un servicio específico
docker-compose restart bot
```

### Raspberry Pi

El proyecto está optimizado para Raspberry Pi 3 (ARM). Todos los servicios se ejecutan via Docker:

```bash
# En la Raspberry Pi
docker-compose up -d --build

# Verificar recursos
docker stats

# Acceso a contenedores
docker exec -it crypto_bot /bin/bash
docker exec -it crypto_api /bin/bash
docker exec -it crypto_redis redis-cli
```

**Nota**: El directorio `./data` se monta automáticamente para persistencia de la base de datos SQLite.

## Estructura del Proyecto

```
bot/
├── main.py              # Punto de entrada
├── config.py            # Configuración
├── trading/
│   ├── engine.py        # Motor de trading
│   ├── risk_manager.py  # Gestión de riesgo
│   ├── portfolio.py     # Portafolio
│   ├── demo_trader.py   # Trading demo
│   └── real_trader.py   # Trading real
├── model/
│   └── predictor.py     # Inferencia ML
├── data/
│   ├── collector.py      # Recolección de datos
│   └── historical.py    # Datos históricos
├── indicators/
│   ├── technical.py     # Indicadores técnicos
│   └── features.py      # Features para modelo
├── database/            # Modelos y CRUD
├── scheduler/           # Tareas programadas
└── notifications/      # Telegram

api/
├── main.py              # FastAPI
├── routers/             # Endpoints REST
└── websocket/           # WebSocket en tiempo real

frontend/
├── index.html           # Dashboard
├── css/style.css
└── js/app.js
```

## Modos de Operación

| Modo | Descripción |
|------|-------------|
| `demo` | Simulación con dinero virtual |
| `real` | Trading real con dinero |

## API Endpoints

| Endpoint | Descripción |
|----------|-------------|
| `GET /health` | Estado del sistema |
| `GET /portfolio` | Estado del portafolio |
| `GET /trades` | Historial de operaciones |
| `GET /market` | Datos de mercado |
| `GET /bot/status` | Estado del bot |
| `GET /logs` | Logs del sistema |
| `WS /ws` | Actualizaciones en tiempo real |

## Métricas de Riesgo

- Máximo 2% por operación
- Máximo 3 posiciones abiertas
- Máximo 60% del portafolio en crypto
- Stop-loss: 1.5x ATR
- Take-profit: 3x ATR
- Confianza mínima: 70%

## Entrenamiento del Modelo

El modelo LightGBM debe entrenarse externamente (PC o Google Colab) y los archivos resultantes copiarse a la Raspberry Pi.

### Pipeline de Entrenamiento

```bash
# 1. Instalar dependencias
pip install -r training/requirements_training.txt

# 2. Descargar datos históricos (mínimo 1 año)
python training/fetch_historical_data.py --pairs BTC-EUR,ETH-EUR --days 365

# 3. Generar features y etiquetas
python training/feature_engineering.py

# 4. Entrenar modelo
python training/train_model.py

# 5. Evaluar (métricas, backtest)
python training/evaluate_model.py

# 6. Exportar a bot/model/
python training/export_model.py --target bot/model
```

### Métricas Mínimas (para producción)

| Métrica | Mínimo |
|---------|--------|
| Precision BUY/SELL | ≥ 0.60 |
| Sharpe Ratio | ≥ 1.0 |
| Max Drawdown | ≤ 15% |

### Google Colab (Gratuito)

1. Sube los scripts de `training/` a Colab
2. Ejecuta los pasos 1-5
3. Descarga: `trained_model.pkl`, `scaler.pkl`, `model_metadata.json`
4. Copia a la RPi: `scp *.pkl pi@[ip]:~/trader-ia-v2/bot/model/`

### Archivos del Modelo

Los archivos deben ubicarse en:
- `bot/model/trained_model.pkl` - Modelo entrenado
- `bot/model/scaler.pkl` - Normalizador RobustScaler
- `bot/model/model_metadata.json` - Métricas y configuración

## Licencia

MIT
