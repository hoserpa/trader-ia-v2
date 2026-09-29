# AGENTS.md - Crypto Trader Bot

> **Documento de trabajo del agente.** Actualizado a la realidad operativa: **grid demo-only**. El ML/entrenamiento fue **eliminado y archivado** (ver sección final).

## Project Overview

Bot Python de trading **grid** (estrategia de cuadrícula) que corre en **demo** sobre Kraken (BTC/EUR, ETH/EUR, SOL/EUR), con dashboard web integrado en tiempo real.

- **Objective (realista)**: 2-5% mensual a **leverage 1** (sin leverage demo; no usar >1 en demo)
  - Hito de salida a real: 30 días consecutivos con ≥2%/mes neto y máx drawdown ≤2% → revisar despliegue real con capital pequeño. Hasta entonces: demo-only. **Contador reiniciado el 28 Sep 2026** (reset completo con contabilidad corregida: balance = PnL realizado, patrimonio = realizado + no realizado vs entrada; medir sobre patrimonio).
  - Monitor de tendencia (grid bidireccional): no tocar config por un único rebalance. Solo reevaluar umbral (8→6%) o el lado short si hay un 2º rebalance en la misma semana.
- **Python**: 3.11 (via Docker)
- **Main Dependencies**: ccxt, pandas, fastapi, sqlalchemy, redis, loguru
- **Database**: SQLite + Redis (demo)
- **Target**: Raspberry Pi 3 (ARM), Docker deployment
- **Servicios Docker**: `redis` + `api` (el **grid corre dentro del `api`**, no hay servicio `bot` separado)

---

## Directory Structure

```
bot/           - Código principal del bot (motor + estrategia grid + portfolio + broker demo)
api/           - FastAPI backend (contiene y arranca el grid)
redis/         - Servicio Redis (infraestructura)
frontend/      - Dashboard Vue.js (CDN, sin build step)
scripts/       - Utilidades (backup, regen_snapshots, etc.)
```

---

## Build & Run Commands

### Install Dependencies

```bash
# Bot dependencies
pip install -r bot/requirements.txt

# API dependencies
pip install -r api/requirements.txt
```

### Run the Bot (grid dentro del api)

```bash
# Desde api/ (el api arranca y gestiona el grid)
uvicorn main:app --host 0.0.0.0 --port 8000

# O via Docker (recomendado)
docker compose up -d --build
```

---

### Testing

No existe suite formal de tests. Para pruebas ad-hoc:

```bash
# Single test file (pytest)
pytest tests/test_file.py -v

# Single test function
pytest tests/test_file.py::test_function_name -v

# Run with coverage
pytest --cov=bot --cov-report=term-missing
```

---

## Environment Variables

Crea `.env` en la raíz del proyecto:

```env
# Modo
TRADING_MODE=demo

# Pares
TRADING_PAIRS=BTC/EUR,ETH/EUR,SOL/EUR

# Exchange (demo no necesita API keys; usa demo_trader)
EXCHANGE=kraken

# -----------------------------------------------
# GRID (operativa activa)
# -----------------------------------------------
GRID_ENABLED=true
GRID_PAIRS=BTC/EUR,ETH/EUR,SOL/EUR
GRID_LEVERAGE=1          # realista: 1× (2-5%/mes)
GRID_LEVELS=15           # niveles por par (mas niveles -> mas trades)
GRID_MIN_LOT_VALUE_EUR=8 # >= ordermin real (SOL 0.06 SOL manda); Kraken rechaza lotes menores
GRID_RANGE_PCT=0.08      # rango total 8% (spacing ≈ rango/(n-1): 15 niveles → 1.14% > 2×fee 0.52%)
GRID_CAPITAL_PCT=0.90
# Margen pata corta (fiel a Kraken): opening fee del importe prestado + rollover por 4h
GRID_MARGIN_OPEN_FEE_PCT=0.0002
GRID_MARGIN_ROLLOVER_PCT=0.00025
GRID_MARGIN_ROLLOVER_HOURS=4
GRID_REBALANCE_THRESHOLD=0.08
GRID_STOP_LOSS_PCT=0.05
GRID_POLL_INTERVAL=15
# ATR-adaptive DESACTIVADO: medido en 15m (BTC 0.18%, ETH 0.23%, SOL 0.27%)
# colapsa el spacing al suelo 2×fee (0.52%) -> margen ~0. El grid FIJO lo evita.
GRID_ATR_ADAPTIVE=false

# Database
SQLITE_DB_PATH=/app/data/crypto_trader.db

# API
API_PORT=8000
API_USERNAME=admin
API_PASSWORD=changeme
```

---

## API Endpoints

- `GET /api/portfolio` - Estado actual del portafolio
- `GET /api/trades` - Historial de operaciones
- `GET /api/operations` - Historial de operaciones (ciclo grid)
- `GET /api/trades/stats` - Estadísticas de trading
- `GET /api/bot/status` - Estado del bot
- `WS /ws` - Actualizaciones en tiempo real

---

## Docker Development

### Requisitos

- Docker y Docker Compose instalados
- Raspberry Pi 3 con Raspberry Pi OS (Bullseye/Bookworm)

### Archivos Docker

- `Dockerfile` - Imagen Python 3.11 slim con dependencias
- `docker-compose.yml` - Servicios: `redis`, `api`

### Comandos

```bash
# 1. Configurar variables de entorno
cp .env.example .env
nano .env

# 2. Construir y ejecutar todos los servicios
docker compose up -d --build

# 3. Ver logs
docker compose logs -f        # todos los servicios
docker compose logs -f api    # solo la API/grid
docker compose logs -f redis  # solo Redis

# 4. Estado de servicios
docker compose ps
```

---

## Grid Strategy (referencia rápida)

- **Engine**: `bot/trading/engine.py` - bucle de monitoreo y coordinación; reconcilia el PnL total con el balance real del portfolio (BD como fuente de verdad), regenera snapshots con throttle (1/hora o cambio >0.01), restaura posiciones abiertas al reiniciar.
- **Grid**: `bot/strategies/grid_strategy.py` - 3 pares, 15 niveles/par, rango 8%, spacing ~1.14% (margen 0.6pp > suelo 2×fee 0.52%), leverage 1, capital 90%, min-lot 8€ (>= ordermin SOL), stop-loss 5%, poll 15s, ATR-adaptive desactivado.
- **Bidireccional en demo**: el grid opera en ambos lados (compra abajo, vende arriba). `_pair_short_ok` en demo devuelve `broker.has_short_support` directamente (el check de `EXCHANGE_ALLOW_SHORT` solo aplica en modo real), así que abre shorts simulados si la API los soporta. Las posiciones "short" del dashboard son los contra-lados pendientes de recomprar (sin desplazo de nocional, leverage 1).
- **Margen pata corta (fiel a Kraken)**: la apertura de un short debita `GRID_MARGIN_OPEN_FEE_PCT` (0.02%) sobre el importe prestado (fill×amount) y el cierre debita `GRID_MARGIN_ROLLOVER_PCT` (0.025%) proporcional al tiempo abierto en tramos de `GRID_MARGIN_ROLLOVER_HOURS` (4h); ambos descuentan del PnL realizado y se acumulan en `margin_fees_eur` por par. En real Kraken presta con margen (leverage mín. 2, `margin_call=80/margin_stop=40`); el demo modela los costes de margen, no la liquidación.
- **Lote mínimo real**: al inicializar cada par se compara `GRID_MIN_LOT_VALUE_EUR` contra `ordermin×precio` y `costmin` de AssetPairs (cargados en `_margin_support`); si el lote es menor, se loguea un warning de que Kraken rechazaría el lote (SOL manda: 0.06 SOL ≈ 6.2-7.8€).
- **Fees**: el grid calcula el PnL neto de cada fill con `broker.fee_rate` (maker 0.16% en demo; si se activara modo real usaría taker 0.26%, conservador). Cada fill guarda su `fee_eur`; el balance acredita `pnl` neto por pierna y descuenta TODAS las comisiones (trade + margen).
- **Demo**: `bot/trading/demo_trader.py` - sin ejecución real; el PnL se acredita al balance simulado.
- **Clave grid**: cada ciclo lleno captura el spread entre niveles; el PnL por ciclo = spread − 2×fee. No es rentable fijar spacing menor que ~2×fee.
- Cuando el precio se desvía del centro >8% (rebalance threshold), el grid **liquida posiciones y recentra** (rebalance). Si la fuga supera el stop-loss, cierra con pérdida.
- **Reinicios**: `start()` restaura el estado desde Redis y fuerza `enabled=true` + `_running=true` al arrancar (independiente de lo que haya quedado grabado en `grid:global`), para que un reinicio del contenedor nunca deje el grid pausado en silencio.

---

## Code Style Guidelines (vigentes)

### Imports

- Standard library first, then third-party, then local
- Group by: stdlib → external → local
- Example:
```python
import os
from datetime import datetime

import ccxt
import pandas as pd
from sqlalchemy import Column

from bot.database.models import Base
```

### Types

- Use type hints for all function signatures
- Use dataclasses for configuration objects
- Example:
```python
from dataclasses import dataclass, field

@dataclass
class Config:
    initial_balance: float = field(default_factory=lambda: float(os.getenv("DEMO_INITIAL_BALANCE", "100")))
```

### Error Handling

- Use custom exceptions for domain errors
- Log errors with context using loguru
- Never expose raw exceptions to API clients

### Docstrings

- Use Google-style docstrings for modules and public functions

---

## Legado ML (eliminado y archivado)

El proyecto arrancó como un bot LightGBM con señales de compra/venta entrenadas en Colab. **Esa ruta fue eliminada del repo**: el grid corre sin modelo y no hay código ML en la operativa.

- Todo lo ML (entrenamiento, modelo, predictor, scheduler legacy, simulador, señales, config del modelo) fue borrado. Punto de restauración: tag `archive/ml-legacy`.
- **No mezclar**: no reintroduzcas ML junto al grid sin validar previamente en paper-trading partiendo de ese tag.
- Nada en `bot/`, `api/` ni `frontend/` hace referencia a ML; `/api/simulate` y `/api/signals` ya no existen.
