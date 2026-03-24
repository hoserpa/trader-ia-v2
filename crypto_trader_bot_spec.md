# Crypto Trader Bot — Especificación Completa para Agente de Codificación

> **Plataforma objetivo:** Raspberry Pi 3 Model B (ARMv8, 1 GB RAM, Linux 64-bit)  
> **Modo inicial:** DEMO (portafolio ficticio, datos reales de mercado)  
> **Exchange:** Coinbase Advanced Trade API (disponible en España/UE)  
> **Objetivo:** Máxima rentabilidad autónoma sin incurrir en pérdidas mediante gestión estricta de riesgo

---

## Índice

1. [Resumen del sistema](#1-resumen-del-sistema)
2. [Requisitos de hardware y SO](#2-requisitos-de-hardware-y-so)
3. [Stack tecnológico completo](#3-stack-tecnológico-completo)
4. [Arquitectura del proyecto](#4-arquitectura-del-proyecto)
5. [Estructura de directorios](#5-estructura-de-directorios)
6. [Módulo 1 — Recolección de datos](#6-módulo-1--recolección-de-datos)
7. [Módulo 2 — Indicadores técnicos y features](#7-módulo-2--indicadores-técnicos-y-features)
8. [Módulo 3 — Modelo de IA](#8-módulo-3--modelo-de-ia)
9. [Módulo 4 — Motor de trading (demo y real)](#9-módulo-4--motor-de-trading-demo-y-real)
10. [Módulo 5 — Gestión de riesgo](#10-módulo-5--gestión-de-riesgo)
11. [Módulo 6 — Base de datos](#11-módulo-6--base-de-datos)
12. [Módulo 7 — API backend (FastAPI)](#12-módulo-7--api-backend-fastapi)
13. [Módulo 8 — Dashboard web (Vue.js)](#13-módulo-8--dashboard-web-vuejs)
14. [Módulo 9 — Notificaciones Telegram](#14-módulo-9--notificaciones-telegram)
15. [Docker Compose — despliegue completo](#15-docker-compose--despliegue-completo)
16. [Scripts de entrenamiento del modelo (PC externo)](#16-scripts-de-entrenamiento-del-modelo-pc-externo)
17. [Configuración y variables de entorno](#17-configuración-y-variables-de-entorno)
18. [Hoja de ruta de implementación](#18-hoja-de-ruta-de-implementación)
19. [Consideraciones legales y de seguridad](#19-consideraciones-legales-y-de-seguridad)

---

## 1. Resumen del sistema

El sistema es un bot de trading de criptomonedas completamente autónomo que:

- Se conecta a la API de **Coinbase Advanced Trade** para obtener datos de mercado en tiempo real
- Usa un modelo de **machine learning (LightGBM)** entrenado con indicadores técnicos para generar señales de trading
- Opera en **modo DEMO** por defecto: simula compras y ventas con datos reales del mercado pero usando un portafolio ficticio configurable
- Implementa **gestión estricta de riesgo** para minimizar pérdidas (stop-loss obligatorio, límite de posiciones, límite de riesgo por operación)
- Expone un **dashboard web en tiempo real** accesible desde la red local
- Registra todas las decisiones, operaciones y métricas en una base de datos local
- Envía **alertas por Telegram** ante eventos importantes

El modo REAL (operaciones reales) está implementado en el código pero desactivado por defecto mediante variable de entorno. Solo debe activarse después de validar el rendimiento en demo durante al menos 2-3 meses.

---

## 2. Requisitos de hardware y SO

### Hardware
- **Dispositivo:** Raspberry Pi 3 Model B
- **RAM:** 1 GB (LPDDR2)
- **CPU:** Cortex-A53 ARMv8 64-bit quad-core 1.2 GHz
- **Almacenamiento:** MicroSD de mínimo 32 GB (Clase 10 / A1 recomendada) + disco USB externo opcional para backups
- **Red:** Ethernet (recomendado sobre WiFi para estabilidad)
- **Refrigeración:** Disipador pasivo obligatorio; ventilador recomendado si se hace overclock

### Sistema operativo
- **OS:** Raspberry Pi OS Lite 64-bit (sin escritorio) — Debian Bookworm base
- **Versión Python:** 3.11 (instalar via deadsnakes PPA o compilar desde fuente)
- **Sin entorno gráfico** — todo se gestiona via SSH y web

### Configuración inicial del SO

```bash
# Ampliar swap a 1 GB (necesario para entrenamiento ligero y picos de memoria)
sudo dphys-swapfile swapoff
sudo sed -i 's/CONF_SWAPSIZE=100/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
sudo dphys-swapfile setup
sudo dphys-swapfile swapon

# Overclock moderado (editar /boot/config.txt)
# arm_freq=1350
# over_voltage=4
# gpu_mem=16  # Mínimo GPU ya que no hay escritorio

# Instalar dependencias del sistema
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y \
    python3.11 python3.11-dev python3.11-venv \
    git curl wget build-essential \
    libhdf5-dev libatlas-base-dev \
    redis-server sqlite3 \
    nginx

# Instalar Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
sudo apt-get install -y docker-compose-plugin
```

---

## 3. Stack tecnológico completo

| Capa | Herramienta | Versión | Licencia | Justificación |
|------|-------------|---------|----------|---------------|
| Lenguaje principal | Python | 3.11 | PSF | Ecosistema ML, soporte ARM |
| Exchange API | ccxt | ≥4.2 | MIT | Abstracción Coinbase AT, WebSocket |
| Indicadores técnicos | pandas-ta | ≥0.3.14b | MIT | Más ligero que TA-Lib en ARM |
| ML modelo | LightGBM | ≥4.3 | MIT | Eficiente en CPU, bajo RAM |
| Procesamiento datos | pandas + numpy | latest | BSD | Estándar industria |
| Backtest | vectorbt | ≥0.26 | AGPL | Solo en PC externo para entrenamiento |
| Backend API | FastAPI | ≥0.110 | MIT | Async, WebSocket nativo |
| ASGI server | Uvicorn | ≥0.29 | BSD | Ligero en ARM |
| Base de datos | SQLite | 3.x | Public Domain | Zero overhead, perfecto para RPi |
| Caché / pub-sub | Redis | 7.x | BSD | Estado en tiempo real, pub/sub |
| ORM | SQLAlchemy | ≥2.0 | MIT | Migrations, type-safe |
| Scheduler | APScheduler | ≥3.10 | MIT | Ciclos periódicos de análisis |
| Frontend | Vue.js 3 | CDN | MIT | Sin build step necesario |
| Gráficas | Chart.js | CDN | MIT | Ligero, sin dependencias |
| Contenedores | Docker + Compose | latest | Apache 2.0 | Aislamiento, fácil despliegue |
| Proxy | Nginx | latest | BSD | Servir frontend + proxy API |
| Notificaciones | python-telegram-bot | ≥21 | MIT | Alertas al móvil |
| Logging | Loguru | ≥0.7 | MIT | Logs estructurados JSON |
| Config | python-dotenv | ≥1.0 | BSD | Variables de entorno |
| HTTP client | httpx | ≥0.27 | BSD | Async requests |

---

## 4. Arquitectura del sistema

```
┌─────────────────────────────────────────────────────────────────┐
│                    FUENTES DE DATOS EXTERNAS                    │
│  Coinbase Advanced Trade API  │  CoinGecko API  │  NewsAPI RSS  │
└──────────────────┬──────────────────┬──────────────────────────┘
                   │ WebSocket OHLCV  │ Históricos REST
                   ▼                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                   RECOLECTOR DE DATOS (Python)                  │
│   ccxt WebSocket listener  │  Historical fetcher  │  Scheduler  │
└──────────────────────────────────┬──────────────────────────────┘
                                   │ OHLCV + orderbook
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                   MOTOR DE INDICADORES (Python)                 │
│   RSI · MACD · Bollinger · ATR · EMA/SMA · Volume · Momentum   │
└──────────────────────────────────┬──────────────────────────────┘
                                   │ Feature vector
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                     MODELO DE IA (LightGBM)                     │
│   Clasificación: BUY / HOLD / SELL  │  Confianza mínima: 70%   │
└──────────────────────────────────┬──────────────────────────────┘
                                   │ Señal + confianza
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                    GESTOR DE RIESGO (Python)                    │
│  Max 2% riesgo/op  │  Stop-loss ATR×1.5  │  Max 3 posiciones   │
└─────────────┬──────────────────────────────────────────────────┘
              │                              │
              ▼ (TRADING_MODE=demo)          ▼ (TRADING_MODE=real)
┌─────────────────────────┐    ┌─────────────────────────────────┐
│    MOTOR DEMO           │    │    MOTOR REAL                   │
│  Portafolio SQLite      │    │  Coinbase Advanced Trade API    │
│  Simula órdenes reales  │    │  Órdenes reales de mercado      │
└─────────────┬───────────┘    └────────────────┬────────────────┘
              └──────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                     PERSISTENCIA                                │
│          SQLite (trades, portfolio)  │  Redis (cache)           │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                  FASTAPI BACKEND (puerto 8000)                  │
│   REST endpoints  │  WebSocket /ws/live  │  Auth básica         │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│              NGINX + DASHBOARD VUE.JS (puerto 80)               │
│  Portfolio  │  Gráficas P&L  │  Historial trades  │  Logs bot   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. Estructura de directorios

```
crypto-trader/
├── docker-compose.yml
├── .env                          # Variables de entorno (NO commitear)
├── .env.example                  # Plantilla de variables
├── README.md
│
├── bot/                          # Servicio principal del bot
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                   # Punto de entrada, orquestador
│   ├── config.py                 # Carga de configuración
│   │
│   ├── data/
│   │   ├── collector.py          # Recolección WebSocket + REST
│   │   ├── historical.py         # Descarga de datos históricos
│   │   └── preprocessor.py      # Limpieza y normalización
│   │
│   ├── indicators/
│   │   ├── technical.py          # RSI, MACD, BB, ATR, etc.
│   │   └── features.py           # Construcción del feature vector
│   │
│   ├── model/
│   │   ├── predictor.py          # Inferencia LightGBM
│   │   ├── trained_model.pkl     # Modelo serializado (generado en PC)
│   │   └── scaler.pkl            # Scaler de features (generado en PC)
│   │
│   ├── trading/
│   │   ├── engine.py             # Orquestador de decisiones
│   │   ├── demo_trader.py        # Motor de trading simulado
│   │   ├── real_trader.py        # Motor de trading real (Coinbase)
│   │   ├── risk_manager.py       # Gestión de riesgo
│   │   └── portfolio.py          # Estado del portafolio
│   │
│   ├── database/
│   │   ├── models.py             # Modelos SQLAlchemy
│   │   ├── crud.py               # Operaciones CRUD
│   │   └── init_db.py            # Inicialización de tablas
│   │
│   ├── notifications/
│   │   └── telegram.py           # Alertas Telegram
│   │
│   └── scheduler/
│       └── jobs.py               # Tareas programadas (APScheduler)
│
├── api/                          # Servicio FastAPI
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                   # App FastAPI
│   ├── routers/
│   │   ├── portfolio.py          # GET /portfolio, /portfolio/history
│   │   ├── trades.py             # GET /trades, /trades/{id}
│   │   ├── market.py             # GET /market/prices, /market/signals
│   │   ├── bot.py                # GET/POST /bot/status, /bot/config
│   │   └── logs.py               # GET /logs
│   └── websocket/
│       └── live.py               # WS /ws/live — stream en tiempo real
│
├── frontend/                     # Dashboard Vue.js (sin build step)
│   ├── index.html                # App principal
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── app.js                # Vue 3 CDN app
│       ├── components/
│       │   ├── PortfolioCard.js
│       │   ├── TradesTable.js
│       │   ├── PriceChart.js
│       │   ├── PnLChart.js
│       │   ├── SignalIndicator.js
│       │   └── BotLog.js
│       └── api.js                # Llamadas a FastAPI
│
├── nginx/
│   └── nginx.conf
│
├── training/                     # Scripts para ejecutar en PC externo
│   ├── requirements_training.txt
│   ├── fetch_historical_data.py  # Descarga datos históricos masivos
│   ├── feature_engineering.py   # Construcción features de entrenamiento
│   ├── train_model.py            # Entrenamiento LightGBM + backtest
│   ├── evaluate_model.py         # Métricas, curvas, validación
│   └── export_model.py           # Exporta .pkl para copiar a la RPi
│
└── scripts/
    ├── setup_rpi.sh              # Script de configuración inicial RPi
    ├── deploy.sh                 # Despliegue via Docker Compose
    └── backup_db.sh              # Backup automático SQLite
```

---

## 6. Módulo 1 — Recolección de datos

### `bot/data/collector.py`

**Responsabilidades:**
- Mantener conexión WebSocket persistente con Coinbase Advanced Trade
- Suscribirse a los canales: `ticker`, `level2` (orderbook), `matches` (trades recientes)
- Almacenar velas OHLCV en Redis (caché de las últimas 500 velas) y en SQLite (histórico completo)
- Reconectar automáticamente ante caídas de conexión (backoff exponencial)
- Publicar nuevas velas en canal Redis `new_candle` para que el motor las consuma

**Pares a operar (configurable via .env):**
```
DEFAULT_PAIRS = ["BTC-EUR", "ETH-EUR", "SOL-EUR"]
DEFAULT_TIMEFRAME = "5m"   # velas de 5 minutos
```

**Interfaz pública esperada:**

```python
class DataCollector:
    def __init__(self, exchange_config: dict, redis_client, db_session)
    
    async def start(self) -> None:
        """Inicia WebSocket y loop de recolección."""
    
    async def stop(self) -> None:
        """Cierra conexiones limpiamente."""
    
    def get_latest_candles(self, pair: str, limit: int = 200) -> pd.DataFrame:
        """Retorna las últimas N velas desde Redis/SQLite.
        Columnas: timestamp, open, high, low, close, volume
        """
    
    async def fetch_historical(self, pair: str, since: datetime, timeframe: str) -> pd.DataFrame:
        """Descarga histórico completo via REST para inicialización."""
```

**Dependencias:** ccxt (>=4.2), redis-py, pandas, loguru

---

### `bot/data/historical.py`

**Responsabilidades:**
- Descarga inicial de datos históricos al arrancar el bot (últimos 90 días mínimo)
- Relleno de gaps en SQLite si el bot estuvo offline
- Fuente secundaria: CoinGecko API (gratuita, sin autenticación) para datos OHLCV diarios

**Notas de implementación:**
- Respetar rate limits de Coinbase: 10 req/s en endpoints REST
- CoinGecko free tier: 30 req/min — usar solo para inicialización, no tiempo real
- Almacenar en tabla `candles` de SQLite con índice en `(pair, timeframe, timestamp)`

---

## 7. Módulo 2 — Indicadores técnicos y features

### `bot/indicators/technical.py`

**Indicadores a calcular usando pandas-ta:**

| Indicador | Parámetros | Uso |
|-----------|-----------|-----|
| RSI | período=14 | Sobrecompra/sobreventa |
| MACD | fast=12, slow=26, signal=9 | Momento y dirección |
| Bollinger Bands | período=20, std=2 | Volatilidad, rangos |
| ATR | período=14 | Volatilidad absoluta, stop-loss |
| EMA | períodos=9,21,50 | Tendencia corto/medio plazo |
| SMA | períodos=20,50,200 | Tendencia largo plazo |
| Stochastic | k=14, d=3 | Confirmación reversiones |
| OBV | — | Volumen acumulado |
| Williams %R | período=14 | Momentum |
| CCI | período=20 | Fuerza de tendencia |

### `bot/indicators/features.py`

**Vector de features para el modelo (total: ~35 features):**

```python
FEATURE_NAMES = [
    # Precio normalizado
    "price_change_1",      # Variación % última vela
    "price_change_3",      # Variación % últimas 3 velas  
    "price_change_6",      # Variación % últimas 6 velas
    
    # RSI
    "rsi_14",
    "rsi_slope",           # Pendiente del RSI (rsi[0] - rsi[3])
    
    # MACD
    "macd",
    "macd_signal",
    "macd_hist",
    "macd_cross",          # 1 si cruce alcista, -1 bajista, 0 neutral
    
    # Bollinger
    "bb_pct_b",            # Posición del precio en las bandas [0-1]
    "bb_bandwidth",        # Anchura de las bandas (volatilidad)
    
    # ATR normalizado
    "atr_pct",             # ATR / precio (volatilidad relativa)
    
    # Medias móviles
    "price_vs_ema9",       # (precio - ema9) / precio
    "price_vs_ema21",
    "price_vs_sma50",
    "ema9_vs_ema21",       # Cruce de medias cortas
    
    # Volumen
    "volume_change",       # Variación % volumen vs media 20 períodos
    "volume_ratio",        # Volumen actual / volumen medio 20
    "obv_slope",           # Tendencia OBV últimas 5 velas
    
    # Momentum
    "stoch_k",
    "stoch_d", 
    "williams_r",
    "cci_20",
    
    # Contexto temporal (patrones horarios en crypto)
    "hour_sin",            # sin(hora * 2π/24)
    "hour_cos",            # cos(hora * 2π/24)
    "day_of_week",         # 0-6
    "is_weekend",          # 0/1
    
    # Orderbook (si disponible)
    "bid_ask_spread_pct",  # Spread relativo
    "orderbook_imbalance", # (bid_vol - ask_vol) / (bid_vol + ask_vol)
    
    # Volatilidad de mercado
    "volatility_regime",   # 0=baja, 1=media, 2=alta (basado en ATR histórico)
]
```

**Interfaz pública:**

```python
class FeatureBuilder:
    def build_features(self, candles: pd.DataFrame) -> pd.Series:
        """
        Recibe DataFrame de velas (≥200 filas para indicadores estables).
        Retorna Series con todos los features normalizados para la última vela.
        Retorna None si no hay suficientes datos.
        """
    
    def build_features_batch(self, candles: pd.DataFrame) -> pd.DataFrame:
        """Para entrenamiento: retorna features de todas las velas válidas."""
```

---

## 8. Módulo 3 — Modelo de IA

### Diseño del modelo

**Tipo:** Clasificador multiclase LightGBM  
**Clases de salida:** `0=SELL`, `1=HOLD`, `2=BUY`  
**Umbral de confianza mínima:** 0.70 (configurable) — si ninguna clase supera el umbral, se emite HOLD  
**Horizonte de predicción:** próximas 3 velas (15 minutos con timeframe 5m)

**Lógica de etiquetado para entrenamiento:**
```
Si el precio sube > 0.8% en las próximas 3 velas → BUY
Si el precio baja > 0.8% en las próximas 3 velas → SELL
En otro caso → HOLD
```
El umbral del 0.8% debe ajustarse según el par y la comisión del exchange.

### `bot/model/predictor.py`

```python
class ModelPredictor:
    def __init__(self, model_path: str, scaler_path: str)
    
    def predict(self, features: pd.Series) -> dict:
        """
        Retorna:
        {
            "signal": "BUY" | "SELL" | "HOLD",
            "confidence": float,          # Probabilidad de la clase ganadora
            "probabilities": {            # Probabilidad de cada clase
                "BUY": float,
                "SELL": float,
                "HOLD": float
            },
            "timestamp": datetime
        }
        """
    
    def is_model_loaded(self) -> bool:
        """Verifica que el modelo está disponible."""
    
    def get_model_metadata(self) -> dict:
        """Retorna fecha de entrenamiento, versión, métricas de validación."""
```

**Notas de inferencia en RPi 3B:**
- LightGBM con `n_jobs=2` para no saturar los 4 núcleos
- Tiempo de inferencia esperado: < 50ms por predicción
- El modelo serializado en `.pkl` no debe superar 50 MB
- Recargar el modelo si se detecta un archivo nuevo (para actualizaciones sin reiniciar)

---

## 9. Módulo 4 — Motor de trading (demo y real)

### `bot/trading/engine.py`

El orquestador principal que coordina todos los módulos. Se ejecuta en un ciclo periódico configurado por `ANALYSIS_INTERVAL_SECONDS` (default: 300 = 5 minutos).

**Ciclo principal:**
```
1. Obtener últimas velas desde caché Redis
2. Calcular indicadores y features
3. Obtener predicción del modelo
4. Consultar al gestor de riesgo si la señal es ejecutable
5. Si ejecutable → ejecutar en DemoTrader o RealTrader según modo
6. Actualizar portafolio en base de datos
7. Publicar estado en Redis para WebSocket del API
8. Enviar notificación Telegram si hubo trade
9. Registrar todo en logs
```

### `bot/trading/demo_trader.py`

**Responsabilidades:**
- Mantener portafolio ficticio en SQLite
- Simular órdenes de mercado con el precio actual real de Coinbase
- Aplicar comisiones simuladas (usar la tarifa real de Coinbase: 0.6% taker para plan básico)
- Calcular slippage estimado basado en el spread del orderbook
- NO llamar a ningún endpoint de órdenes de Coinbase

**Interfaz:**
```python
class DemoTrader:
    def __init__(self, initial_balance_eur: float, db_session, redis_client)
    
    def execute_buy(self, pair: str, amount_eur: float, current_price: float) -> dict:
        """
        Simula compra. Retorna:
        { "trade_id", "pair", "side": "buy", "amount_eur", 
          "amount_crypto", "price", "fee", "timestamp" }
        """
    
    def execute_sell(self, pair: str, amount_crypto: float, current_price: float) -> dict:
        """Simula venta."""
    
    def get_portfolio(self) -> dict:
        """
        Retorna estado actual del portafolio:
        { "balance_eur", "positions": {pair: {amount, avg_buy_price, current_value, pnl_pct}},
          "total_value_eur", "total_pnl_eur", "total_pnl_pct" }
        """
    
    def get_open_positions(self) -> list[dict]:
        """Lista de posiciones abiertas con PnL en tiempo real."""
```

### `bot/trading/real_trader.py`

**Solo se instancia cuando `TRADING_MODE=real` en .env**

**Responsabilidades:**
- Ejecutar órdenes reales via ccxt en Coinbase Advanced Trade
- Usar exclusivamente órdenes de tipo `market` para garantizar ejecución
- Confirmar ejecución antes de registrar en base de datos
- Gestionar errores de API (rate limits, fondos insuficientes, mercado cerrado)

**Interfaz idéntica a DemoTrader** para que el engine los use de forma intercambiable (patrón Strategy).

**Notas importantes:**
- Implementar circuit breaker: si hay 3 errores consecutivos de API → parar el bot y alertar por Telegram
- Nunca reintentar una orden de compra sin confirmar que la anterior no se ejecutó
- Log de todas las llamadas a la API con timestamp y respuesta completa

---

## 10. Módulo 5 — Gestión de riesgo

### `bot/trading/risk_manager.py`

Este es el módulo más crítico del sistema. Actúa como filtro final antes de cualquier ejecución.

**Reglas de riesgo implementadas:**

```python
class RiskManager:
    # Parámetros configurables via .env
    MAX_RISK_PER_TRADE_PCT = 0.02      # Max 2% del portafolio por operación
    MAX_OPEN_POSITIONS = 3              # Max 3 posiciones simultáneas
    MAX_PORTFOLIO_IN_CRYPTO_PCT = 0.60  # Max 60% del portafolio en crypto
    MIN_CONFIDENCE_THRESHOLD = 0.70    # Confianza mínima del modelo
    STOP_LOSS_ATR_MULTIPLIER = 1.5     # Stop-loss = precio_entrada - ATR*1.5
    TAKE_PROFIT_ATR_MULTIPLIER = 3.0   # Take-profit = precio_entrada + ATR*3.0
    MIN_TRADE_EUR = 10.0               # Mínimo de Coinbase
    MAX_DAILY_TRADES = 20              # Max operaciones por día (anti-overtrading)
    HIGH_VOLATILITY_ATR_THRESHOLD = 0.05  # No operar si ATR% > 5%
    
    def can_buy(self, pair: str, signal: dict, portfolio: dict, current_price: float, atr: float) -> tuple[bool, str, float]:
        """
        Evalúa si se puede comprar.
        Retorna: (puede_comprar: bool, razón: str, cantidad_eur: float)
        
        Checks:
        1. Confianza del modelo >= MIN_CONFIDENCE_THRESHOLD
        2. No superar MAX_OPEN_POSITIONS
        3. No superar MAX_PORTFOLIO_IN_CRYPTO_PCT
        4. No superar MAX_DAILY_TRADES
        5. Volatilidad no está en régimen alto (ATR% < HIGH_VOLATILITY_ATR_THRESHOLD)
        6. Balance EUR suficiente (>= MIN_TRADE_EUR)
        7. No hay posición abierta ya en ese par
        """
    
    def can_sell(self, pair: str, position: dict, current_price: float, atr: float) -> tuple[bool, str]:
        """
        Evalúa si se debe vender (señal del modelo + checks de stop-loss y take-profit).
        Retorna: (debe_vender: bool, razón: str)
        
        Checks:
        1. Señal SELL del modelo con confianza suficiente, O
        2. Stop-loss alcanzado: current_price <= position.stop_loss_price, O
        3. Take-profit alcanzado: current_price >= position.take_profit_price
        """
    
    def calculate_position_size(self, portfolio_value: float, atr: float, current_price: float) -> float:
        """
        Kelly Criterion simplificado:
        risk_amount = portfolio_value * MAX_RISK_PER_TRADE_PCT
        position_size_crypto = risk_amount / (ATR * STOP_LOSS_ATR_MULTIPLIER)
        Retorna el importe en EUR a invertir, capped por las reglas de portfolio.
        """
    
    def calculate_stop_loss(self, entry_price: float, atr: float) -> float:
        """entry_price - (atr * STOP_LOSS_ATR_MULTIPLIER)"""
    
    def calculate_take_profit(self, entry_price: float, atr: float) -> float:
        """entry_price + (atr * TAKE_PROFIT_ATR_MULTIPLIER)"""
```

---

## 11. Módulo 6 — Base de datos

### `bot/database/models.py`

**Esquema SQLite completo:**

```sql
-- Velas OHLCV históricas
CREATE TABLE candles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pair        TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    timestamp   DATETIME NOT NULL,
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      REAL NOT NULL,
    UNIQUE(pair, timeframe, timestamp)
);
CREATE INDEX idx_candles_pair_ts ON candles(pair, timeframe, timestamp DESC);

-- Portafolio (snapshot cada ciclo)
CREATE TABLE portfolio_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       DATETIME NOT NULL,
    balance_eur     REAL NOT NULL,
    total_value_eur REAL NOT NULL,
    total_pnl_eur   REAL NOT NULL,
    total_pnl_pct   REAL NOT NULL,
    positions_json  TEXT NOT NULL  -- JSON con posiciones abiertas
);

-- Posiciones abiertas
CREATE TABLE positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pair                TEXT NOT NULL,
    amount_crypto       REAL NOT NULL,
    entry_price         REAL NOT NULL,
    entry_timestamp     DATETIME NOT NULL,
    stop_loss_price     REAL NOT NULL,
    take_profit_price   REAL NOT NULL,
    amount_eur_invested REAL NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open',  -- open | closed
    close_price         REAL,
    close_timestamp     DATETIME,
    pnl_eur             REAL,
    pnl_pct             REAL,
    close_reason        TEXT  -- signal | stop_loss | take_profit | manual
);

-- Historial de trades ejecutados
CREATE TABLE trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER REFERENCES positions(id),
    pair            TEXT NOT NULL,
    side            TEXT NOT NULL,  -- buy | sell
    amount_crypto   REAL NOT NULL,
    amount_eur      REAL NOT NULL,
    price           REAL NOT NULL,
    fee_eur         REAL NOT NULL,
    timestamp       DATETIME NOT NULL,
    mode            TEXT NOT NULL,  -- demo | real
    exchange_order_id TEXT          -- NULL en modo demo
);
CREATE INDEX idx_trades_timestamp ON trades(timestamp DESC);

-- Log de decisiones del modelo
CREATE TABLE model_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       DATETIME NOT NULL,
    pair            TEXT NOT NULL,
    signal          TEXT NOT NULL,   -- BUY | SELL | HOLD
    confidence      REAL NOT NULL,
    prob_buy        REAL NOT NULL,
    prob_sell       REAL NOT NULL,
    prob_hold       REAL NOT NULL,
    executed        INTEGER NOT NULL DEFAULT 0,  -- 1 si se ejecutó trade
    rejection_reason TEXT                        -- Razón si no se ejecutó
);
CREATE INDEX idx_decisions_timestamp ON model_decisions(timestamp DESC);

-- Configuración del bot (clave-valor)
CREATE TABLE bot_config (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    updated_at DATETIME NOT NULL
);

-- Log de errores y eventos del sistema
CREATE TABLE system_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   DATETIME NOT NULL,
    level       TEXT NOT NULL,   -- INFO | WARNING | ERROR | CRITICAL
    module      TEXT NOT NULL,
    message     TEXT NOT NULL,
    extra_json  TEXT
);
CREATE INDEX idx_logs_timestamp ON system_logs(timestamp DESC);
```

---

## 12. Módulo 7 — API backend (FastAPI)

### `api/main.py`

**Base URL:** `http://[ip-raspberry]:8000`  
**Autenticación:** HTTP Basic Auth (usuario/contraseña en .env)  
**CORS:** Habilitado solo para origen del dashboard (localhost o IP local)

### Endpoints REST

```
GET  /health                        → Estado del sistema
GET  /portfolio                     → Portfolio actual completo
GET  /portfolio/history?days=30     → Snapshots históricos para gráfica
GET  /trades?limit=50&offset=0      → Historial de trades paginado
GET  /trades/{trade_id}             → Detalle de un trade
GET  /positions                     → Posiciones abiertas actuales
GET  /market/prices                 → Precios actuales de los pares configurados
GET  /market/signals                → Últimas señales del modelo por par
GET  /market/indicators/{pair}      → Valores actuales de indicadores técnicos
GET  /bot/status                    → Estado del bot (running/paused/error)
POST /bot/start                     → Arrancar el bot (requiere auth admin)
POST /bot/stop                      → Pausar el bot
GET  /bot/config                    → Configuración actual
PUT  /bot/config                    → Actualizar configuración (ej: pares activos)
GET  /logs?level=INFO&limit=100     → Logs del sistema
GET  /stats/summary                 → Estadísticas generales (winrate, sharpe, etc.)
```

### WebSocket

```
WS   /ws/live                       → Stream en tiempo real

Mensajes emitidos por el servidor (JSON):
{
  "type": "price_update",
  "data": { "pair": "BTC-EUR", "price": 65000.50, "change_24h": 2.3 }
}
{
  "type": "signal",
  "data": { "pair": "BTC-EUR", "signal": "BUY", "confidence": 0.82, "timestamp": "..." }
}
{
  "type": "trade_executed",
  "data": { "pair": "BTC-EUR", "side": "buy", "amount_eur": 100, "price": 65000 }
}
{
  "type": "portfolio_update",
  "data": { "total_value_eur": 1050.30, "pnl_pct": 5.03 }
}
{
  "type": "bot_status",
  "data": { "status": "running", "mode": "demo", "last_cycle": "2024-01-15T10:30:00" }
}
```

**Implementación del WebSocket:** El API lee de un canal Redis pub/sub (`bot:live_updates`) donde el bot publica todos los eventos. El WebSocket hace fan-out a todos los clientes conectados.

---

## 13. Módulo 8 — Dashboard web (Vue.js)

### Diseño de la interfaz

La interfaz es una SPA (Single Page Application) servida por Nginx, sin necesidad de Node.js ni build step. Usa Vue.js 3 via CDN y Chart.js para gráficas.

### Secciones del dashboard

**1. Barra superior (siempre visible)**
- Modo actual (DEMO / REAL) con badge de color
- Estado del bot (● Running / ● Paused / ● Error)
- Precios en tiempo real de los pares configurados
- Botón de pausa/arranque del bot

**2. Panel de resumen (cards superiores)**
- Balance total (EUR) con variación desde inicio
- PnL total (EUR y %)
- Número de trades ejecutados hoy
- Win rate (% de trades rentables)
- Posiciones abiertas activas
- Drawdown máximo

**3. Gráfica de evolución del portafolio**
- Chart.js, línea temporal, últimos 30/7/1 días (selector)
- Muestra valor total del portafolio en EUR a lo largo del tiempo
- Marcas en los puntos donde se ejecutaron trades (buy=verde, sell=rojo)

**4. Posiciones abiertas**
- Tabla: Par | Entrada | Precio actual | PnL% | Stop-loss | Take-profit | Tiempo abierto
- Actualización en tiempo real via WebSocket

**5. Historial de trades**
- Tabla paginada con filtros por par, tipo (buy/sell), fecha
- Columnas: Timestamp | Par | Tipo | Precio | EUR | Comisión | PnL | Razón cierre

**6. Panel de señales y modelo**
- Por cada par: semáforo de señal actual (BUY/HOLD/SELL) con porcentaje de confianza
- Barra de probabilidades de las 3 clases
- Últimas 10 decisiones del modelo (ejecutadas y rechazadas)
- Valores actuales de indicadores técnicos principales

**7. Log del sistema**
- Últimas 100 líneas del log en tiempo real
- Filtros por nivel (INFO/WARNING/ERROR)
- Auto-scroll con toggle para pausar

### Archivos clave del frontend

**`frontend/index.html`** — estructura HTML principal, imports CDN:
```html
<!-- Vue 3 -->
<script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
<!-- Chart.js -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<!-- Axios para HTTP -->
<script src="https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js"></script>
```

**`frontend/js/app.js`** — app Vue principal con:
- WebSocket connection management con reconexión automática
- Estado global del dashboard (portfolio, trades, signals, logs)
- Polling de fallback cada 30s si WebSocket falla

---

## 14. Módulo 9 — Notificaciones Telegram

### `bot/notifications/telegram.py`

**Configuración:** Crear un bot en @BotFather de Telegram y obtener token + chat_id

**Mensajes a enviar:**

```python
class TelegramNotifier:
    async def notify_trade(self, trade: dict) -> None:
        """
        Ejemplo mensaje:
        🟢 COMPRA ejecutada [DEMO]
        Par: BTC-EUR
        Cantidad: 0.00154 BTC
        Precio: €64,900
        Inversión: €100
        Stop-loss: €63,520
        Take-profit: €67,950
        Confianza modelo: 84%
        """
    
    async def notify_position_closed(self, trade: dict, pnl: float) -> None:
        """
        🔴 VENTA ejecutada [DEMO]
        Par: BTC-EUR
        Precio: €66,200
        PnL: +€20.30 (+2.03%)
        Razón: take_profit
        """
    
    async def notify_stop_loss_hit(self, position: dict) -> None:
        """⚠️ STOP-LOSS activado en BTC-EUR"""
    
    async def notify_bot_error(self, error: str) -> None:
        """🔴 ERROR CRÍTICO: [descripción]"""
    
    async def send_daily_summary(self) -> None:
        """
        📊 Resumen diario [DEMO]
        Trades hoy: 5
        PnL hoy: +€45.20 (+4.52%)
        Win rate hoy: 80%
        Portfolio total: €1,045.20
        """
```

---

## 15. Docker Compose — despliegue completo

### `docker-compose.yml`

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    container_name: crypto_redis
    restart: unless-stopped
    command: redis-server --maxmemory 128mb --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    networks:
      - crypto_net

  bot:
    build: ./bot
    container_name: crypto_bot
    restart: unless-stopped
    env_file: .env
    depends_on:
      - redis
    volumes:
      - ./data:/app/data          # SQLite y modelos
      - ./logs:/app/logs
    networks:
      - crypto_net
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 400M

  api:
    build: ./api
    container_name: crypto_api
    restart: unless-stopped
    env_file: .env
    depends_on:
      - redis
      - bot
    volumes:
      - ./data:/app/data          # Acceso read-only a SQLite
      - ./logs:/app/logs
    ports:
      - "8000:8000"
    networks:
      - crypto_net
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 200M

  nginx:
    image: nginx:alpine
    container_name: crypto_nginx
    restart: unless-stopped
    volumes:
      - ./frontend:/usr/share/nginx/html:ro
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    ports:
      - "80:80"
    depends_on:
      - api
    networks:
      - crypto_net

volumes:
  redis_data:

networks:
  crypto_net:
    driver: bridge
```

### `nginx/nginx.conf`

```nginx
events { worker_processes 1; }

http {
    upstream api {
        server api:8000;
    }

    server {
        listen 80;
        
        # Servir frontend estático
        location / {
            root /usr/share/nginx/html;
            index index.html;
            try_files $uri $uri/ /index.html;
        }
        
        # Proxy al API FastAPI
        location /api/ {
            rewrite ^/api/(.*) /$1 break;
            proxy_pass http://api;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
        
        # WebSocket proxy
        location /ws/ {
            proxy_pass http://api;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_read_timeout 86400;
        }
    }
}
```

---

## 16. Scripts de entrenamiento del modelo (PC externo)

**Estos scripts se ejecutan en un ordenador con más recursos (o Google Colab).** El resultado (archivos `.pkl`) se copia a la Raspberry Pi.

### `training/train_model.py`

**Pipeline completo:**

```python
# 1. Cargar datos históricos (mínimo 1 año de datos en timeframe 5m)
# 2. Calcular todos los indicadores técnicos
# 3. Construir features con FeatureBuilder
# 4. Generar etiquetas (BUY/SELL/HOLD) según la lógica descrita en sección 8
# 5. Split temporal: 70% train, 15% validation, 15% test (NO shuffle - datos de serie temporal)
# 6. Normalizar features con RobustScaler (más robusto a outliers)
# 7. Entrenar LightGBM con parámetros optimizados para RPi:
#    - n_estimators: 500
#    - max_depth: 6
#    - num_leaves: 31
#    - learning_rate: 0.05
#    - n_jobs: 2
#    - class_weight: "balanced" (para manejar desbalance BUY/SELL vs HOLD)
# 8. Optimizar umbral de confianza en validation set para maximizar precision (minimizar falsos positivos)
# 9. Evaluar en test set: accuracy, precision, recall, F1 por clase, curva ROC
# 10. Backtesting con vectorbt usando las señales del modelo
# 11. Exportar modelo.pkl + scaler.pkl + metadata.json
```

**Métricas mínimas aceptables para usar el modelo en producción:**
- Precision en BUY >= 0.60 (de cada 10 señales buy, mínimo 6 son correctas)
- Precision en SELL >= 0.60
- Sharpe ratio en backtest >= 1.0
- Max drawdown en backtest <= 15%

### Usar Google Colab para entrenamiento (gratuito)

```python
# Instalar en Colab:
# !pip install lightgbm pandas-ta ccxt vectorbt scikit-learn joblib

# Descargar datos históricos (1 año BTC-EUR, ETH-EUR en 5m)
# NOTA: ccxt permite descargar hasta 300 velas por request, hacer loop

# Al finalizar, descargar:
# files.download('trained_model.pkl')
# files.download('scaler.pkl')
# files.download('model_metadata.json')

# Copiar a la RPi:
# scp trained_model.pkl pi@[ip-rpi]:~/crypto-trader/bot/model/
```

---

## 17. Configuración y variables de entorno

### `.env.example`

```bash
# ==========================================
# MODO DE OPERACIÓN
# ==========================================
TRADING_MODE=demo               # demo | real (NUNCA cambiar a real sin validación)
ANALYSIS_INTERVAL_SECONDS=300   # Ciclo de análisis cada 5 minutos

# ==========================================
# COINBASE ADVANCED TRADE API
# ==========================================
COINBASE_API_KEY=your_api_key_here
COINBASE_API_SECRET=your_api_secret_here
COINBASE_SANDBOX=false          # true para usar el entorno de pruebas de Coinbase

# ==========================================
# PARES Y CONFIGURACIÓN DE TRADING
# ==========================================
TRADING_PAIRS=BTC-EUR,ETH-EUR,SOL-EUR
BASE_CURRENCY=EUR
DEMO_INITIAL_BALANCE=1000.0     # Balance inicial ficticio en EUR

# ==========================================
# PARÁMETROS DE RIESGO (ver RiskManager)
# ==========================================
MAX_RISK_PER_TRADE_PCT=0.02
MAX_OPEN_POSITIONS=3
MAX_PORTFOLIO_IN_CRYPTO_PCT=0.60
MIN_CONFIDENCE_THRESHOLD=0.70
STOP_LOSS_ATR_MULTIPLIER=1.5
TAKE_PROFIT_ATR_MULTIPLIER=3.0
MAX_DAILY_TRADES=20
HIGH_VOLATILITY_ATR_THRESHOLD=0.05

# ==========================================
# BASE DE DATOS
# ==========================================
SQLITE_DB_PATH=/app/data/crypto_trader.db

# ==========================================
# REDIS
# ==========================================
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# ==========================================
# MODELO IA
# ==========================================
MODEL_PATH=/app/model/trained_model.pkl
SCALER_PATH=/app/model/scaler.pkl
MODEL_TIMEFRAME=5m
MODEL_CANDLES_REQUIRED=200      # Velas mínimas para predicción estable

# ==========================================
# API BACKEND
# ==========================================
API_HOST=0.0.0.0
API_PORT=8000
API_USERNAME=admin
API_PASSWORD=change_this_password_immediately

# ==========================================
# TELEGRAM (opcional)
# ==========================================
TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# ==========================================
# FUENTES DE DATOS ADICIONALES (opcionales)
# ==========================================
COINGECKO_API_KEY=               # Dejar vacío para plan gratuito
NEWS_API_KEY=                    # Para análisis de sentimiento (opcional)

# ==========================================
# LOGGING
# ==========================================
LOG_LEVEL=INFO
LOG_FILE=/app/logs/bot.log
LOG_MAX_SIZE_MB=50
LOG_BACKUP_COUNT=5
```

---

## 18. Hoja de ruta de implementación

### Semana 1–2: Infraestructura base
- [ ] Instalar Raspberry Pi OS Lite 64-bit y configurar sistema (swap, overclock, paquetes)
- [ ] Instalar Docker y Docker Compose
- [ ] Clonar repositorio, configurar `.env` con credenciales
- [ ] Levantar servicios Redis, Nginx con `docker compose up`
- [ ] Verificar que el dashboard HTML vacío es accesible desde la red local
- [ ] Configurar backup automático de SQLite (cron + `scripts/backup_db.sh`)

### Semana 3–4: Recolección de datos
- [ ] Implementar `DataCollector` con ccxt WebSocket para Coinbase
- [ ] Descargar histórico de 90 días de BTC-EUR y ETH-EUR
- [ ] Verificar almacenamiento correcto en SQLite
- [ ] Implementar `FeatureBuilder` y verificar que los indicadores se calculan correctamente
- [ ] Test unitarios para `technical.py` y `features.py`

### Semana 5–7: Modelo de IA (en PC externo / Google Colab)
- [ ] Ejecutar `training/fetch_historical_data.py` para 1 año de datos en 5m
- [ ] Ejecutar `training/train_model.py` y revisar métricas
- [ ] Si métricas no alcanzan umbral mínimo: ajustar features, parámetros, umbral de etiquetado
- [ ] Exportar `trained_model.pkl` y `scaler.pkl`
- [ ] Copiar modelos a la RPi y verificar que `ModelPredictor` los carga correctamente

### Semana 8–9: Motor de trading demo
- [ ] Implementar `DemoTrader` con portafolio ficticio
- [ ] Implementar `RiskManager` con todas las reglas
- [ ] Implementar ciclo principal en `engine.py`
- [ ] Ejecutar bot en modo demo durante 48h y verificar logs
- [ ] Comprobar que stop-loss y take-profit se activan correctamente

### Semana 10–12: Dashboard web
- [ ] Implementar todos los endpoints REST en FastAPI
- [ ] Implementar WebSocket `/ws/live` con pub/sub Redis
- [ ] Desarrollar dashboard Vue.js con todas las secciones
- [ ] Integrar Chart.js para gráficas de portafolio y P&L
- [ ] Test de carga: verificar uso de CPU/RAM con bot activo + dashboard con 2 clientes

### Semana 13+: Optimización y monitorización
- [ ] Activar notificaciones Telegram
- [ ] Ejecutar modo demo durante 2–3 meses registrando métricas reales
- [ ] Re-entrenar modelo con datos más recientes cada mes
- [ ] Evaluar si win rate > 55% y Sharpe > 1.0 de forma consistente antes de considerar modo real
- [ ] Implementar `RealTrader` y probar con cantidades mínimas si se decide activar modo real

---

## 19. Consideraciones legales y de seguridad

### Seguridad del sistema
- **Credenciales:** Nunca commitear el archivo `.env` al repositorio. Añadir a `.gitignore`
- **Claves API Coinbase:** Crear claves con el mínimo de permisos necesarios. En modo demo, crear claves solo con permiso de lectura (view)
- **Acceso al dashboard:** Cambiar las credenciales por defecto inmediatamente. Considerar acceso via VPN (Tailscale gratuito) en lugar de exponer el puerto 80 a internet
- **Backup:** Realizar backup diario de la base de datos SQLite en almacenamiento externo o cloud (rclone + Google Drive gratuito)
- **Actualizaciones:** Mantener el sistema operativo y dependencias actualizadas

### Consideraciones legales (España / UE)
- Las ganancias obtenidas con trading de criptomonedas están sujetas a tributación en España (IRPF, tratadas como ganancias patrimoniales)
- Coinbase Advanced Trade está disponible en España y cumple con la normativa MiCA de la UE
- El bot opera en nombre del usuario — la responsabilidad de las operaciones y obligaciones fiscales recae sobre el usuario
- En modo demo no hay implicaciones legales ni fiscales (es simulación)

### Descargo de responsabilidad
- **Ningún sistema de trading garantiza ausencia de pérdidas.** La gestión de riesgo reduce pero no elimina el riesgo
- El modelo de ML se basa en patrones históricos que pueden no repetirse
- El modo real solo debe activarse con capital que el usuario esté dispuesto a perder en su totalidad
- Se recomienda encarecidamente ejecutar el modo demo durante un mínimo de 3 meses antes de considerar operaciones reales

---

*Documento generado para ser usado por un agente de codificación. Versión 1.0.*
