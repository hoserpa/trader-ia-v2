# AGENTS.md - Crypto Trader Bot

> **Documento de trabajo del agente.** Actualizado a la realidad operativa: **grid demo-only**. El ML/entrenamiento es **legado** (ver sección final).

## Project Overview

Bot Python de trading **grid** (estrategia de cuadrícula) que corre en **demo** sobre Kraken (BTC/EUR, ETH/EUR, SOL/EUR), con dashboard web integrado en tiempo real.

- **Objective (realista)**: 2-5% mensual a **leverage 1** (sin leverage demo; no usar >1 en demo)
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
training/      - Scripts de entrenamiento (LEGADO ML - no se usan en grid)
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
GRID_LEVELS=6            # niveles por par
GRID_RANGE_PCT=0.08      # rango total 8% (spacing derivado ≈ range/levels >2×fee)
GRID_CAPITAL_PCT=0.90
GRID_REBALANCE_THRESHOLD=0.08
GRID_STOP_LOSS_PCT=0.05
GRID_POLL_INTERVAL=15
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
- `GET /api/signals` - Señales recientes (legacy ML; vacío en grid)
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
- **Grid**: `bot/strategies/grid_strategy.py` - 3 pares, 6 niveles/par, rango 8%, spacing ~3.2%, leverage 1, capital 90%, stop-loss 5%, poll 15s, ATR-adaptive desactivado.
- **Demo**: `bot/trading/demo_trader.py` - sin ejecución real; el PnL se acredita al balance simulado.
- **Clave grid**: cada ciclo lleno captura el spread entre niveles; el PnL por ciclo = spread − 2×fee. No es rentable fijar spacing menor que ~2×fee.
- Cuando el precio se desvía del centro >8% (rebalance threshold), el grid **liquida posiciones y recentra** (rebalance). Si la fuga supera el stop-loss, cierra con pérdida.

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

## Legado ML (desactivado)

El proyecto arrancó como un bot LightGBM con señales de compra/venta entrenadas en Colab. **Esa ruta está desactivada**: el grid corre sin modelo, y la operativa real no usa ML.

- **No mezclar**: no enciendas señales ML junto al grid sin validar en paper-trading primero.
- Archivos que siguen existiendo pero NO se usan en la operativa grid: `bot/model/predictor.py`, `training/*`, `bot/scheduler/*` (ML).
- Para reactivar (no recomendado): entrenar `training/train_model.py`, colocar `model/trained_model.pkl` y conectar el `TradingEngine` al interpretator del modelo.
