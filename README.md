# Crypto Trader Bot - Grid (Demo)

Bot Python de trading **grid** (estrategia de cuadrícula) que corre en modo **demo** sobre **Kraken** (pares BTC/EUR, ETH/EUR, SOL/EUR), con dashboard web en tiempo real y API FastAPI. Desplegado en **Raspberry Pi 3** (ARM) vía Docker.

> **Grid-only**: la operativa real es un grid con **leverage 1** (sin leverage) en demo. Todo lo de ML/LightGBM pertenece a una fase anterior, **desactivada** (ver [Legado ML](#legado-ml-actividad-ml-desactivada)).

---

## Vista Rápida

- **Servicios Docker**: `redis` + `api` (el grid corre **dentro** del `api`; no hay servicio `bot` separado)
- **Objetivo**: **2-5% mensual** (realista) a 1×
- **Estrategia**: grid sobre 3 pares, 6 niveles/par, rango 8%, spacing~3.2%, leverage 1, capital 90%, stop-loss 5%, poll 15s, ATR-adaptive off
- **PnL real (demo)**: +2.49€ neto en ~13.5 días sobre 100€ demo (≈5% mensualizado en mercado volátil)
- **Dashboard**: `http://<IP>:8000` · API REST + WS en tiempo real

---

## Índice

- [Características](#características)
- [Requisitos](#requisitos)
- [Instalación](#instalación)
  - [Docker (Recomendado)](#docker-recomendado)
  - [Local (Desarrollo)](#local-desarrollo)
- [Configuración](#configuración)
- [Grid: qué controla cada variable](#grid-qué-controla-cada-variable)
- [Dashboard / API / Endpoints](#dashboard--api--endpoints)
- [Docker Development](#docker-development)
- [Raspberry Pi](#raspberry-pi)
- [Troubleshooting](#troubleshooting)
- [Legado ML (actividad ML desactivada)](#legado-ml-actividad-ml-desactivada)

---

## Características

- **Grid demo automático** en 3 pares (BTC/EUR, ETH/EUR, SOL/EUR)
- **Modo demo** con balance simulado sin riesgo (PnL de papel, sin tocar cuenta real)
- **Reconciliación de PnL**: el total del grid cuadra con el balance real del portfolio (la BD es la fuente de verdad)
- **Snapshots regenerados** desde los trades reales (historial limpio, ~1/hora con throttle)
- **Rebalance automático**: cuando el precio se desvía del centro >8%, el grid liquida y recentra
- **Stop-loss** por par (5%) ante fuga fuera de rango
- **Dashboard web** en tiempo real (Vue.js, sin build step)
- **API REST + WebSocket** para el dashboard
- **Docker** listo para Raspberry Pi 3

---

## Requisitos

- Docker y Docker Compose instalados
- Raspberry Pi 3 con Raspberry Pi OS (Bullseye/Bookworm)
- Redis (incluido en Docker)

---

## Instalación

### Docker (Recomendado)

```bash
# 1. Clonar repositorio
git clone https://github.com/hoserpa/trader-ia-v2.git
cd trader-ia-v2

# 2. Configurar variables de entorno
cp .env.example .env
nano .env

# 3. Construir y ejecutar todos los servicios
docker compose up -d --build

# 4. Verificar estado
docker compose ps
```

### Local (Desarrollo)

```bash
# Instalar dependencias
pip install -r bot/requirements.txt
pip install -r api/requirements.txt

# Ejecutar Redis
docker run -d -p 6379:6379 redis:7-alpine

# Ejecutar API (el grid arranca junto al api)
cd api
uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## Configuración

Crear `.env` en la raíz del proyecto:

```env
# Trading (grid en demo; sin ML)
TRADING_MODE=demo
TRADING_PAIRS=BTC/EUR,ETH/EUR,SOL/EUR
EXCHANGE=kraken

# Exchange API (solo rellena para modo real; demo usa demo_trader)
KRAKEN_API_KEY=
KRAKEN_API_SECRET=

# Grid
GRID_ENABLED=true
GRID_PAIRS=BTC/EUR,ETH/EUR,SOL/EUR
GRID_LEVERAGE=1            # sin leverage (realista, 2-5%/mes)
GRID_LEVELS=6              # niveles por par (GRID_LEVELS, no GRID_LEVELS_PER_PAIR)
GRID_RANGE_PCT=0.08        # rango total del grid (8%)
GRID_CAPITAL_PCT=0.90      # % del balance en el grid
# spacing derivado: range/levels ≈ 0.08/6 ≈ 1.33% ...
GRID_REBALANCE_THRESHOLD=0.08   # desviación para rebalancear
GRID_STOP_LOSS_PCT=0.05    # stop-loss por fuga de rango
GRID_POLL_INTERVAL=15      # segundos entre checks
GRID_ATR_ADAPTIVE=false    # adaptación por volatilidad (desactivada)

# Database
SQLITE_DB_PATH=/app/data/crypto_trader.db
REDIS_HOST=redis
REDIS_PORT=6379

# API
API_PORT=8000
API_USERNAME=admin
API_PASSWORD=changeme
```

---

## Grid: qué controla cada variable

| Variable | Efecto |
|----------|--------|
| `GRID_PAIRS` | Pares a operar (capital repartido entre ellos) |
| `GRID_LEVERAGE` | Multiplica el nocional por nivel. **1** = realista (2-5%/mes); >1 infla el demo y el drawdown (no recomendado en demo) |
| `GRID_LEVELS` | Densidad de niveles; más niveles = más ciclos pero más fees por unidad de rango |
| `GRID_RANGE_PCT` | Amplitud del rango alrededor del precio. Rango estrecho = grid más denso, se llena más rápido pero sale el precio con más frecuencia |
| `GRID_SPACING_PCT` | Distancia entre niveles; el PnL por ciclo es el spread capturado menos 2×fee. **No fijar spacing menor que ~2×fee** o cada ciclo pierde |
| `GRID_REBALANCE_THRESHOLD` | Si el precio se desvía del centro más de este %, recéntralo (liquida y abre niveles nuevos) |
| `GRID_CAPITAL_PCT` | Fracción del balance asignada al grid (90%) |
| `GRID_STOP_LOSS_PCT` | Si la fuga supera este %, cierra con pérdida en vez de rebalancear |

**Economía clave del grid**: cada ciclo lleno captura el spread entre niveles; el PnL neto por ciclo = spread − 2×fee. Con spacing 3.2% y nivel de ~5€, cada cierre rinde ~0.09-0.23€ netos. La frecuencia de ciclos la pone el mercado (volatilidad), no el bot; por eso el rango **% mensual no se "configura": lo limita cuánta volatilidad cruce el grid** y queda en 2-5%/mes a 1×.

---

## Dashboard / API / Endpoints

| Endpoint | Descripción |
|----------|-------------|
| `GET /` | Dashboard web |
| `GET /api/portfolio` | Estado del portafolio |
| `GET /api/bot/status` | Estado del bot |
| `GET /api/trades` | Historial de operaciones |
| `GET /api/trades/stats` | Estadísticas de trading |
| `GET /api/bot/grid` | Estado del grid por par |
| `GET /api/logs` | Logs del sistema |
| `WS /ws` | Actualizaciones en tiempo real |

---

## Docker Development

### Services

El `docker-compose.yml` define **2 servicios**:

- `redis` - Almacenamiento de estado y caché
- `api` - FastAPI backend **+ motor de grid** (el grid corre aquí, no hay servicio `bot` separado)

### Comandos

```bash
# Todos los servicios
docker compose up -d --build

# Ver logs
docker compose logs -f
docker compose logs -f api

# Estado
docker compose ps

# Reiniciar el api (y con ello el grid)
docker compose restart api

# Detener
docker compose down
```

> **Nota**: como no hay servicio `bot`, reiniciar el `api` reinicia también el grid (restaura niveles e histórico desde Redis + BD).

---

## Raspberry Pi

```bash
# Verificar arquitectura ARM
uname -m  # debe mostrar armv7l o aarch64

# Raspberry Pi 3 (ARM32): puede requerir build-essential para dependencias nativas
sudo apt install build-essential libffi-dev libssl-dev
```

El directorio `./data` se monta automáticamente para persistir la base de datos SQLite.

---

## Troubleshooting

```bash
# Redis no conecta
docker compose logs redis
docker compose restart redis

# El grid no arranca (corre dentro del api)
docker compose logs -f api
docker compose restart api

# Limpiar y reconstruir
docker compose down -v
docker compose up -d --build
```

---

## Legado ML (actividad ML desactivada)

El proyecto arrancó como un bot **LightGBM** con señales de compra/venta entrenadas en Colab. Esa ruta está **desactivada**: el grid corre sin modelo y la operativa real no usa ML. El código ML sigue existiendo en el repo pero **no se usa** en la operativa grid.

- **Archivos legado (no usados en grid)**: `bot/model/`, `bot/trading/predictor.py` (LightGBM), `training/`, `bot/scheduler/` (ML), `Dockerfile` bot.
- **Para reactivar (no recomendado)**: entrenar en `training/`, colocar `model/trained_model.pkl` y conectar el `TradingEngine`. No encender ML junto al grid sin validar antes en paper-trading.
- `GRID_ATR_ADAPTIVE` es la única pieza de "adaptabilidad" vigente.

---

## Licencia

MIT
