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

- Python 3.11+
- Redis
- SQLite
- (Opcional) Docker para despliegue

## Instalación

```bash
# Clonar repositorio
git clone https://github.com/hoserpa/trader-ia-v2.git
cd trader-ia-v2

# Copiar configuración
cp .env.example .env

# Instalar dependencias
pip install -r bot/requirements.txt
pip install -r api/requirements.txt
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

### Docker

```bash
docker-compose up -d
```

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

El modelo debe entrenarse con datos históricos. Ubicar `trained_model.pkl` y `scaler.pkl` en la ruta configurada en `MODEL_PATH`.

## Licencia

MIT
