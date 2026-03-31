# AGENTS.md - Crypto Trader Bot

## Project Overview

This is a Python-based cryptocurrency trading bot using LightGBM ML model, FastAPI backend, and Vue.js frontend. It connects to Binance API for live trading.

- **Objective**: 2-4% monthly return (realistic target)
- **Python**: 3.11 (via Docker)
- **Main Dependencies**: ccxt, pandas, lightgbm, fastapi, sqlalchemy, redis, loguru
- **Database**: SQLite + Redis
- **Target**: Raspberry Pi 3 (ARM), Docker deployment

---

## Directory Structure

```
bot/           - Main trading bot code
api/           - FastAPI backend
training/      - Model training scripts
frontend/      - Vue.js dashboard (CDN-based, no build step)
scripts/       - Utility scripts (backup, etc.)
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

### Run the Bot

```bash
# From bot/ directory
python main.py

# Or via Docker
docker-compose up -d
```

### Run the API

```bash
cd api
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Testing

No formal test suite exists. To run ad-hoc tests:

```bash
# Single test file (pytest)
pytest tests/test_file.py -v

# Single test function
pytest tests/test_file.py::test_function_name -v

# Run with coverage
pytest --cov=bot --cov-report=term-missing
```

---

## Code Style Guidelines

### Imports

- Standard library first, then third-party, then local
- Group by: stdlib → external → project
- Example:
```python
import os
from datetime import datetime

import ccxt
import pandas as pd
from sqlalchemy import Column

from bot.database.models import Base
```

### Formatting

- **Line length**: 100 characters max
- **Indentation**: 4 spaces (no tabs)
- **Blank lines**: 2 between top-level definitions, 1 between methods
- Use Black for formatting if available

### Types

- Use type hints for all function signatures
- Use dataclasses for configuration objects
- Example:
```python
from dataclasses import dataclass, field

@dataclass
class Config:
    api_key: str = field(default_factory=lambda: os.getenv("API_KEY", ""))
    timeout: int = 30

def fetch_data(pair: str, limit: int) -> pd.DataFrame:
    ...
```

### Naming Conventions

- **Modules/Files**: snake_case (e.g., `risk_manager.py`, `trading_engine.py`)
- **Classes**: PascalCase (e.g., `RiskManager`, `TradingEngine`)
- **Functions/Variables**: snake_case (e.g., `calculate_position_size`, `max_risk_pct`)
- **Constants**: UPPER_SNAKE_CASE (e.g., `MAX_POSITIONS`, `DEFAULT_TIMEOUT`)
- **Private methods**: prefix with `_` (e.g., `_validate_config`)

### Error Handling

- Use custom exceptions for domain errors
- Log errors with context using loguru
- Never expose raw exceptions to API clients
- Example:
```python
class TradingError(Exception):
    pass

try:
    await execute_trade(order)
except InsufficientBalanceError as e:
    logger.warning(f"Insufficient balance: {e}")
    raise HTTPException(status_code=400, detail=str(e))
```

### Docstrings

- Use Google-style docstrings for modules and public functions
- Minimal comments - code should be self-documenting
```python
def calculate_pnl(entry_price: float, current_price: float, size: float) -> float:
    """Calculate profit/loss for a position.
    
    Args:
        entry_price: Price at which position was opened
        current_price: Current market price
        size: Position size in base currency
        
    Returns:
        PnL in quote currency
    """
    return (current_price - entry_price) * size
```

### Database

- Use SQLAlchemy 2.0 with declarative base
- Always use async sessions with aioredis
- migrations via Alembic (if needed)

### Async Code

- Use `async`/`await` for I/O-bound operations
- Use `httpx` for async HTTP requests
- Use ` asyncio` for concurrency

### Configuration

- All config via environment variables
- Use `python-dotenv` for local development
- Use dataclasses in `bot/config.py` to validate on startup
- Never hardcode secrets in code

### Logging

- Use loguru for structured logging
- Log levels: DEBUG (dev), INFO (normal), WARNING (recoverable), ERROR (failures)
- Include context in log messages
```python
logger.info(f"Trade executed: {side} {size} {pair} @ {price}")
```

---

## Environment Variables

Create `.env` file in project root:

```env
# Trading
TRADING_MODE=demo
TRADING_PAIRS=BTC/EUR,ETH/EUR,SOL/EUR
EXCHANGE=binance

# Exchange API
BINANCE_API_KEY=
BINANCE_API_SECRET=

# Database
SQLITE_DB_PATH=/app/data/crypto_trader.db

# Model
MODEL_PATH=/app/model/trained_model.pkl

# API
API_PORT=8000
API_USERNAME=admin
API_PASSWORD=changeme
```

---

## API Endpoints

- `GET /api/portfolio` - Current portfolio state
- `GET /api/trades` - Trade history
- `GET /api/signals` - Recent trading signals
- `GET /api/status` - Bot status
- `WS /ws` - Real-time updates

---

## Docker Development

### Requisitos

- Docker y Docker Compose instalados
- Raspberry Pi 3 con Raspberry Pi OS (Bullseye/Bookworm)

### Archivos Docker

- `Dockerfile` - Imagen Python 3.11 slim con dependencias
- `docker-compose.yml` - Servicios: redis, bot, api

### Comandos

```bash
# 1. Configurar variables de entorno
cp .env.example .env
nano .env

# 2. Construir y ejecutar todos los servicios
docker-compose up -d --build

# 3. Ver logs
docker-compose logs -f        # todos los servicios
docker-compose logs -f bot    # solo el bot
docker-compose logs -f api    # solo la API

# 4. Estado de servicios
docker-compose ps

# 5. Reiniciar servicio específico
docker-compose restart bot
docker-compose restart api

# 6. Detener servicios
docker-compose down

# 7. Reconstruir un servicio
docker-compose build bot
docker-compose up -d bot
```

### Raspberry Pi - Notas especiales

```bash
# Verificar arquitectura ARM
uname -m  # debe mostrar armv7l o aarch64

# Raspberry Pi 3 (ARM32): puede requerir compilación de dependencias
# Solución: usar imagen python:3.11-slim y confiar en wheels precompilados

# Si hay problemas de dependencias nativas, instalar build-essential:
sudo apt install build-essential libffi-dev libssl-dev
```

### Desarrollo

```bash
# Modo desarrollo con live reload
docker-compose up -d --build

# Editar código en host y los cambios se reflejan automáticamente
# (API tiene --reload, Bot requiere reiniciar)

# Acceder a contenedores
docker exec -it crypto_bot /bin/bash
docker exec -it crypto_api /bin/bash
docker exec -it crypto_redis redis-cli
```

### Troubleshooting

```bash
# Verificar que Redis esté funcionando
docker-compose logs redis

# Reiniciar Redis
docker-compose restart redis

# Ver uso de recursos
docker stats

# Limpiar volúmenes si hay problemas
docker-compose down -v
docker-compose up -d --build
```