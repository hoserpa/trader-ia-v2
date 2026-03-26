# Crypto Trader Bot

Bot de trading automatizado de criptomonedas usando aprendizaje automático (LightGBM) con interfaz web integrada.

## Características

- **Trading automático** en Binance
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

### Docker (Recomendado para Raspberry Pi)

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

### Local (Desarrollo)

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
TRADING_PAIRS=BTC/EUR,ETH/EUR,SOL/EUR

# Exchange (binance, coinbase, kraken, etc)
EXCHANGE=binance

# Claves API (solo para modo real)
BINANCE_API_KEY=tu_api_key
BINANCE_API_SECRET=tu_api_secret

# Thresholds de señal (optimizado para el modelo entrenado)
BUY_THRESHOLD=0.40
SELL_THRESHOLD=0.40

# Contraseña API web
API_USERNAME=admin
API_PASSWORD=changeme
```

## Acceso al Dashboard

### URLs de acceso

| Entorno | URL |
|---------|-----|
| Local | `http://localhost:8000` |
| Red local (IP) | `http://<TU_IP>:8000` |
| Docker Host | `http://127.0.0.1:8000` |

### Obtener IP de la Raspberry Pi

```bash
# Linux/Raspberry Pi
hostname -I | awk '{print $1}'

# Windows
ipconfig | findstr /i "IPv4"
```

## Ejecución

### Iniciar servicios

```bash
# Iniciar todos los servicios
docker-compose up -d --build

# Ver logs en tiempo real
docker-compose logs -f

# Ver logs de un servicio específico
docker-compose logs -f bot
docker-compose logs -f api
docker-compose logs -f redis
```

### Detener servicios

```bash
# Detener todos los servicios (preserva datos)
docker-compose down

# Detener y eliminar volúmenes (LIMPIA TODO)
docker-compose down -v

# Detener un servicio específico
docker-compose stop bot

# Reiniciar un servicio específico
docker-compose restart bot
```

### Estado y debug

```bash
# Ver estado de contenedores
docker-compose ps

# Ver recursos usados
docker stats

# Acceder a contenedor bash
docker exec -it crypto_bot /bin/bash
docker exec -it crypto_api /bin/bash
docker exec -it crypto_redis redis-cli
```

### Raspberry Pi

El proyecto está optimizado para Raspberry Pi 3 (ARM):

```bash
# En la Raspberry Pi
docker-compose up -d --build

# Acceso desde otro dispositivo
# http://<IP_RASPBERRY_PI>:8000
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
│   ├── collector.py     # Recolección de datos
│   └── historical.py    # Datos históricos
├── indicators/
│   ├── technical.py     # Indicadores técnicos
│   └── features.py      # Features para modelo
├── database/            # Modelos y CRUD
├── scheduler/           # Tareas programadas
└── notifications/       # Telegram

api/
├── main.py              # FastAPI
├── routers/             # Endpoints REST
└── websocket/           # WebSocket en tiempo real

frontend/
├── index.html           # Dashboard
├── css/style.css
└── js/app.js

training/
├── train_model.py       # Entrenamiento
├── evaluate_model.py    # Evaluación y backtest
├── feature_engineering.py
└── fetch_historical_data.py
```

## Modos de Operación

| Modo | Descripción |
|------|-------------|
| `demo` | Simulación con dinero virtual |
| `real` | Trading real con dinero |

## API Endpoints

| Endpoint | Descripción |
|----------|-------------|
| `GET /` | Dashboard web |
| `GET /health` | Estado del sistema |
| `GET /api/portfolio` | Estado del portafolio |
| `GET /api/trades` | Historial de operaciones |
| `GET /api/market` | Datos de mercado |
| `GET /api/bot/status` | Estado del bot |
| `GET /api/logs` | Logs del sistema |
| `WS /ws` | Actualizaciones en tiempo real |

## Parámetros de Riesgo

- Máximo 2% por operación
- Máximo 3 posiciones abiertas
- Máximo 60% del portafolio en crypto
- Stop-loss: 1.5x ATR
- Take-profit: 3x ATR
- Threshold BUY: 40% (configurable)
- Threshold SELL: 40% (configurable)

## Entrenamiento del Modelo

El modelo LightGBM debe entrenarse externamente (PC o Google Colab) y los archivos resultantes copiarse al directorio `bot/model/`.

### Pipeline de Entrenamiento

```bash
# 1. Instalar dependencias
pip install -r training/requirements.txt

# 2. Descargar datos históricos (mínimo 90 días)
python training/fetch_historical_data.py --pairs BTC/EUR,ETH/EUR --days 90

# 3. Generar features y etiquetas
python training/feature_engineering.py

# 4. Entrenar modelo
python training/train_model.py

# 5. Evaluar (métricas, backtest)
python training/evaluate_model.py --buy-threshold 0.40 --sell-threshold 0.40
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
4. Copia a la Raspberry Pi:
   ```bash
   scp *.pkl *.json pi@[IP]:~/trader-ia-v2/bot/model/
   ```

### Archivos del Modelo

Los archivos deben ubicarse en:
- `bot/model/trained_model.pkl` - Modelo entrenado
- `bot/model/scaler.pkl` - Normalizador RobustScaler
- `bot/model/model_metadata.json` - Métricas y configuración

## Troubleshooting

### Problemas comunes

```bash
# Redis no conecta
docker-compose logs redis
docker-compose restart redis

# El bot no arranca
docker-compose logs bot

# Verificar puertos ocupados
netstat -tuln | grep 8000

# Limpiar y recreate
docker-compose down -v
docker-compose up -d --build
```

### Logs

```bash
# Todos los logs
docker-compose logs -f

# Solo errores
docker-compose logs -f | grep ERROR
```

## Licencia

MIT
