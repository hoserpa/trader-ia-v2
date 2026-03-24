# Crypto Trader Bot — Adenda: Convivencia con CasaOS y Home Assistant

> **Este documento modifica y complementa** `crypto_trader_bot_spec.md` y `crypto_trader_bot_implementation.md`.  
> Todas las instrucciones aquí prevalecen sobre los documentos anteriores en los puntos que contradigan.

---

## Contexto del sistema existente

La Raspberry Pi ya tiene en producción los siguientes servicios que **NO deben ser alterados bajo ningún concepto**:

| Servicio | Puerto | Gestor | Notas |
|----------|--------|--------|-------|
| **CasaOS** | **80** (HTTP) y **443** (HTTPS) | Docker (gestionado por CasaOS) | Panel de control principal de la RPi |
| **Home Assistant** | **8123** | Docker (gestionado por CasaOS) | Sistema domótica |

### Implicaciones críticas

1. **El puerto 80 está ocupado por CasaOS** — el Nginx del bot no puede usarlo.
2. **El puerto 443 está ocupado por CasaOS** — tampoco disponible para el bot.
3. **El puerto 8123 está ocupado por Home Assistant** — el bot no puede usarlo.
4. **CasaOS gestiona su propio stack Docker** — el bot debe instalarse como una app más de CasaOS o en una red Docker separada que no interfiera.
5. **NO instalar Nginx como servicio del sistema** (`apt install nginx`) — CasaOS ya gestiona el proxy de puertos y podría entrar en conflicto.

---

## Puertos asignados al bot (nuevos)

| Servicio | Puerto nuevo | Descripción |
|----------|-------------|-------------|
| Dashboard web (frontend) | **7000** | Interfaz visual del bot |
| FastAPI backend (REST + WS) | **7001** | API interna del bot |
| Redis del bot | **6380** | Separado del Redis de CasaOS si lo usa |

> **Regla:** Usar el rango 7000–7099 para todos los servicios del bot. Este rango no colisiona con CasaOS (80, 443), Home Assistant (8123), ni puertos estándar de sistema.

---

## Sección 2 modificada — Requisitos de hardware y SO

### ⚠️ Reemplaza la sección "Configuración inicial del SO" del documento original

El sistema operativo **ya está configurado** con CasaOS. No ejecutar el script de setup completo. Solo aplicar lo siguiente:

```bash
# SOLO si el swap no está ya en 1 GB (verificar primero con: free -h)
# Si CasaOS ya lo amplió, omitir este paso
sudo dphys-swapfile swapoff
sudo sed -i 's/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
sudo dphys-swapfile setup
sudo dphys-swapfile swapon

# NO instalar nginx via apt — CasaOS lo gestiona
# NO instalar redis-server via apt — se usará contenedor Docker propio del bot
# NO reinstalar Docker — ya está instalado por CasaOS

# Verificar que Docker Compose plugin está disponible
docker compose version
# Si falla: sudo apt-get install -y docker-compose-plugin

# Verificar puertos disponibles antes de arrancar el bot
sudo ss -tlnp | grep -E ':(80|443|7000|7001|6380|8123)'
# Solo deben aparecer 80, 443 (CasaOS) y 8123 (Home Assistant)
# Los puertos 7000, 7001 y 6380 deben estar LIBRES
```

---

## Sección 4 modificada — Arquitectura del sistema

### ⚠️ Reemplaza el diagrama de arquitectura del documento original

```
╔══════════════════════════════════════════════════════════════════╗
║              RASPBERRY PI 3B — SERVICIOS EXISTENTES             ║
║                                                                  ║
║  ┌─────────────────────────┐   ┌────────────────────────────┐   ║
║  │  CasaOS                 │   │  Home Assistant            │   ║
║  │  puerto: 80 / 443       │   │  puerto: 8123              │   ║
║  │  (NO TOCAR)             │   │  (NO TOCAR)                │   ║
║  └─────────────────────────┘   └────────────────────────────┘   ║
║                                                                  ║
╠══════════════════════════════════════════════════════════════════╣
║              CRYPTO TRADER BOT — SERVICIOS NUEVOS               ║
║                                                                  ║
║  ┌──────────────────────────────────────────────────────────┐   ║
║  │  FUENTES DE DATOS EXTERNAS                               │   ║
║  │  Coinbase Advanced Trade API │ CoinGecko │ NewsAPI       │   ║
║  └──────────────┬───────────────────────────────────────────┘   ║
║                 │ WebSocket + REST                               ║
║                 ▼                                                ║
║  ┌──────────────────────────────────────────────────────────┐   ║
║  │  BOT (contenedor Docker interno — sin puerto expuesto)   │   ║
║  │  Recolector → Indicadores → Modelo IA → Risk Manager     │   ║
║  │  DemoTrader / RealTrader → Publica a Redis               │   ║
║  └──────────────┬───────────────────────────────────────────┘   ║
║                 │ pub/sub                                         ║
║                 ▼                                                ║
║  ┌──────────────────────┐                                        ║
║  │  Redis               │  puerto host: 6380                     ║
║  │  (contenedor Docker) │                                        ║
║  └──────────┬───────────┘                                        ║
║             │                                                     ║
║             ▼                                                     ║
║  ┌──────────────────────┐                                        ║
║  │  FastAPI API         │  puerto host: 7001                     ║
║  │  REST + WebSocket    │  → http://[IP-RPI]:7001               ║
║  └──────────┬───────────┘                                        ║
║             │                                                     ║
║             ▼                                                     ║
║  ┌──────────────────────┐                                        ║
║  │  Nginx (contenedor)  │  puerto host: 7000                     ║
║  │  Sirve frontend      │  → http://[IP-RPI]:7000               ║
║  │  Proxy a :7001       │                                        ║
║  └──────────────────────┘                                        ║
╚══════════════════════════════════════════════════════════════════╝
```

**Acceso al dashboard:** `http://[IP-RASPBERRY]:7000`  
**Acceso a la API:** `http://[IP-RASPBERRY]:7001`

---

## Sección 15 modificada — Docker Compose completo

### ⚠️ Reemplaza completamente el `docker-compose.yml` del documento de implementación

```yaml
# docker-compose.yml
# Crypto Trader Bot — compatible con CasaOS + Home Assistant en la misma RPi
#
# PUERTOS USADOS:
#   7000 → Nginx (dashboard web)
#   7001 → FastAPI (API REST + WebSocket)
#   6380 → Redis (separado del posible Redis de CasaOS en 6379)
#
# PUERTOS RESPETADOS (NO tocados):
#   80   → CasaOS
#   443  → CasaOS
#   8123 → Home Assistant

version: '3.8'

services:

  redis:
    image: redis:7-alpine
    container_name: cryptobot_redis
    restart: unless-stopped
    command: redis-server --maxmemory 128mb --maxmemory-policy allkeys-lru --save ""
    ports:
      - "6380:6379"          # Puerto externo 6380, interno 6379 (no conflicto)
    volumes:
      - cryptobot_redis_data:/data
    networks:
      - cryptobot_net
    deploy:
      resources:
        limits:
          memory: 150M

  bot:
    build:
      context: ./bot
      dockerfile: Dockerfile
    image: cryptobot_bot:latest
    container_name: cryptobot_bot
    restart: unless-stopped
    env_file: .env
    environment:
      - REDIS_HOST=redis          # Nombre del servicio dentro de la red Docker
      - REDIS_PORT=6379           # Puerto interno del contenedor Redis
    depends_on:
      - redis
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./bot/model:/app/model
    networks:
      - cryptobot_net
    # Sin puertos expuestos — solo accesible dentro de la red Docker interna
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 400M

  api:
    build:
      context: ./api
      dockerfile: Dockerfile
    image: cryptobot_api:latest
    container_name: cryptobot_api
    restart: unless-stopped
    env_file: .env
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
    depends_on:
      - redis
      - bot
    volumes:
      - ./data:/app/data:ro      # Acceso read-only a SQLite
      - ./logs:/app/logs:ro
    ports:
      - "7001:8000"              # FastAPI accesible en host:7001
    networks:
      - cryptobot_net
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 200M

  nginx:
    image: nginx:alpine
    container_name: cryptobot_nginx
    restart: unless-stopped
    volumes:
      - ./frontend:/usr/share/nginx/html:ro
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    ports:
      - "7000:80"                # Dashboard accesible en host:7000
    depends_on:
      - api
    networks:
      - cryptobot_net
    deploy:
      resources:
        limits:
          memory: 50M

volumes:
  cryptobot_redis_data:
    name: cryptobot_redis_data    # Nombre explícito para no colisionar con volúmenes de CasaOS

networks:
  cryptobot_net:
    name: cryptobot_net           # Nombre explícito, red aislada del resto de CasaOS
    driver: bridge
```

---

## Nginx modificado — `nginx/nginx.conf`

### ⚠️ Reemplaza el `nginx.conf` del documento de implementación

```nginx
# nginx/nginx.conf
# Nginx del Crypto Trader Bot — escucha en puerto interno 80 del contenedor
# El host lo mapea a puerto 7000 via Docker. NO interfiere con el Nginx/Caddy de CasaOS.

events {
    worker_processes 1;
    worker_connections 256;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    # Logs mínimos para no saturar la SD
    access_log off;
    error_log  /var/log/nginx/error.log warn;

    # Gzip para reducir tráfico en red local
    gzip on;
    gzip_types text/plain text/css application/javascript application/json;

    upstream cryptobot_api {
        server api:8000;         # Nombre del servicio en la red Docker interna
    }

    server {
        listen 80;
        server_name _;

        # Servir el frontend Vue.js estático
        location / {
            root  /usr/share/nginx/html;
            index index.html;
            try_files $uri $uri/ /index.html;

            # Headers de caché para assets estáticos
            location ~* \.(js|css|png|jpg|ico)$ {
                expires 1h;
                add_header Cache-Control "public";
            }
        }

        # Proxy REST al API FastAPI
        location /api/ {
            rewrite ^/api/(.*) /$1 break;
            proxy_pass         http://cryptobot_api;
            proxy_set_header   Host $host;
            proxy_set_header   X-Real-IP $remote_addr;
            proxy_read_timeout 30s;
        }

        # Proxy WebSocket al API FastAPI
        location /ws/ {
            proxy_pass             http://cryptobot_api;
            proxy_http_version     1.1;
            proxy_set_header       Upgrade $http_upgrade;
            proxy_set_header       Connection "upgrade";
            proxy_set_header       Host $host;
            proxy_read_timeout     86400s;   # Sin timeout para WebSocket
            proxy_send_timeout     86400s;
        }
    }
}
```

---

## Variables de entorno modificadas — `.env.example`

### ⚠️ Reemplaza el bloque Redis y API del `.env.example` original

```bash
# ==========================================
# MODO DE OPERACIÓN
# ==========================================
TRADING_MODE=demo
ANALYSIS_INTERVAL_SECONDS=300

# ==========================================
# COINBASE ADVANCED TRADE API
# ==========================================
COINBASE_API_KEY=your_api_key_here
COINBASE_API_SECRET=your_api_secret_here
COINBASE_SANDBOX=false

# ==========================================
# PARES Y CONFIGURACIÓN DE TRADING
# ==========================================
TRADING_PAIRS=BTC-EUR,ETH-EUR,SOL-EUR
BASE_CURRENCY=EUR
DEMO_INITIAL_BALANCE=1000.0

# ==========================================
# PARÁMETROS DE RIESGO
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
# IMPORTANTE: dentro de la red Docker del bot, Redis escucha en su puerto
# interno 6379 (nombre de servicio: redis). El host expone 6380 para debug.
# Los contenedores del bot usan REDIS_HOST=redis y REDIS_PORT=6379.
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
MODEL_CANDLES_REQUIRED=200

# ==========================================
# API BACKEND
# El API escucha en puerto 8000 dentro del contenedor.
# El host lo mapea a 7001. El frontend llama a /api/ via Nginx (puerto 7000).
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
# LOGGING
# ==========================================
LOG_LEVEL=INFO
LOG_FILE=/app/logs/bot.log
LOG_MAX_SIZE_MB=50
LOG_BACKUP_COUNT=5
```

---

## Frontend modificado — URL base de la API

### ⚠️ Modifica la constante `API_BASE` en `frontend/js/app.js`

El frontend se sirve desde el Nginx del bot en puerto 7000. Las llamadas al API van a `/api/` (relativas, sin puerto), y Nginx las proxea internamente al contenedor FastAPI. El WebSocket también es relativo.

```javascript
// frontend/js/app.js — líneas a modificar al inicio del archivo

// ANTES (documento original):
// const API_BASE = '/api';
// const WS_URL = `ws://${location.host}/ws/live`;

// DESPUÉS (con CasaOS en el mismo sistema):
const API_BASE = '/api';                              // Relativo — Nginx lo redirige a FastAPI:8000
const WS_URL = `ws://${location.hostname}:7000/ws/live`;  // Explícito con puerto 7000
```

---

## Script de setup modificado — `scripts/setup_rpi.sh`

### ⚠️ Reemplaza completamente el script del documento de implementación

```bash
#!/bin/bash
# scripts/setup_rpi.sh
# Configuración del Crypto Trader Bot en RPi con CasaOS + Home Assistant ya instalados.
# NO modifica ningún servicio existente de CasaOS ni Home Assistant.

set -e

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   Crypto Trader Bot — Setup para CasaOS             ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── Verificar que Docker ya está disponible (instalado por CasaOS) ──────────
if ! command -v docker &> /dev/null; then
    echo "❌ ERROR: Docker no está instalado. CasaOS debería haberlo instalado."
    echo "   Verifica que CasaOS está correctamente instalado y vuelve a intentarlo."
    exit 1
fi
echo "✅ Docker disponible: $(docker --version)"

if ! docker compose version &> /dev/null; then
    echo "⚠️  Docker Compose plugin no encontrado. Instalando..."
    sudo apt-get install -y docker-compose-plugin
fi
echo "✅ Docker Compose: $(docker compose version)"

# ── Verificar puertos libres ────────────────────────────────────────────────
echo ""
echo "Verificando puertos..."

check_port() {
    local port=$1
    local name=$2
    if sudo ss -tlnp 2>/dev/null | grep -q ":$port "; then
        echo "⚠️  Puerto $port ($name) ya está en uso:"
        sudo ss -tlnp | grep ":$port "
        return 1
    else
        echo "✅ Puerto $port ($name) — libre"
        return 0
    fi
}

PORTS_OK=true
check_port 7000 "Dashboard bot" || PORTS_OK=false
check_port 7001 "API bot"       || PORTS_OK=false
check_port 6380 "Redis bot"     || PORTS_OK=false

# Verificar que CasaOS y HA siguen en sus puertos (informativo)
echo ""
echo "Servicios existentes (deben seguir activos):"
sudo ss -tlnp 2>/dev/null | grep -E ":(80|443|8123) " || echo "  (no detectados en este momento)"

if [ "$PORTS_OK" = false ]; then
    echo ""
    echo "❌ Hay conflictos de puertos. Revisa qué proceso usa los puertos 7000, 7001 o 6380."
    echo "   Cambia las variables DASHBOARD_PORT, API_PORT_HOST en el docker-compose.yml si es necesario."
    exit 1
fi

# ── Verificar espacio en disco ──────────────────────────────────────────────
echo ""
AVAILABLE_MB=$(df -m / | awk 'NR==2 {print $4}')
echo "Espacio disponible en /: ${AVAILABLE_MB} MB"
if [ "$AVAILABLE_MB" -lt 2000 ]; then
    echo "⚠️  Advertencia: menos de 2 GB libres. El bot necesita espacio para logs y datos."
fi

# ── Verificar/ampliar swap ──────────────────────────────────────────────────
echo ""
SWAP_MB=$(free -m | awk '/Swap/ {print $2}')
echo "Swap actual: ${SWAP_MB} MB"
if [ "$SWAP_MB" -lt 900 ]; then
    echo "Ampliando swap a 1 GB..."
    sudo dphys-swapfile swapoff
    sudo sed -i 's/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
    sudo dphys-swapfile setup
    sudo dphys-swapfile swapon
    echo "✅ Swap ampliado a 1 GB"
else
    echo "✅ Swap ya es suficiente (${SWAP_MB} MB)"
fi

# ── Crear estructura de directorios ────────────────────────────────────────
echo ""
echo "Creando estructura de directorios..."
mkdir -p ~/crypto-trader/{data,logs,bot/model,nginx}
echo "✅ Directorios creados en ~/crypto-trader/"

# ── Instalar sqlite3 si no está (para backups) ─────────────────────────────
if ! command -v sqlite3 &> /dev/null; then
    sudo apt-get install -y sqlite3
fi

# ── Configurar cron para backups ────────────────────────────────────────────
CRON_JOB="0 2 * * * /home/pi/crypto-trader/scripts/backup_db.sh >> /home/pi/crypto-trader/logs/backup.log 2>&1"
(crontab -l 2>/dev/null | grep -v backup_db; echo "$CRON_JOB") | crontab -
echo "✅ Cron de backup configurado (2:00 AM diario)"

# ── Añadir usuario al grupo docker si no está ──────────────────────────────
if ! groups $USER | grep -q docker; then
    sudo usermod -aG docker $USER
    echo "✅ Usuario añadido al grupo docker (requiere nueva sesión SSH para activarse)"
fi

# ── Resumen final ───────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   ✅ Setup completado                                ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Pasos siguientes:"
echo ""
echo "  1. Copia el modelo entrenado:"
echo "     scp trained_model.pkl scaler.pkl pi@$(hostname -I | awk '{print $1}'):~/crypto-trader/bot/model/"
echo ""
echo "  2. Crea el archivo de configuración:"
echo "     cp ~/crypto-trader/.env.example ~/crypto-trader/.env"
echo "     nano ~/crypto-trader/.env   # Añade tus claves de Coinbase"
echo ""
echo "  3. Arranca el bot:"
echo "     cd ~/crypto-trader"
echo "     docker compose up -d"
echo ""
echo "  4. Accede al dashboard:"
echo "     http://$(hostname -I | awk '{print $1}'):7000"
echo ""
echo "  Servicios existentes NO afectados:"
echo "     CasaOS:         http://$(hostname -I | awk '{print $1}'):80"
echo "     Home Assistant: http://$(hostname -I | awk '{print $1}'):8123"
echo ""
```

---

## Integración opcional con Home Assistant

Si en el futuro se desea ver el estado del bot dentro de Home Assistant, se puede añadir un sensor REST en la configuración de HA. Esto es **completamente opcional** y no afecta al funcionamiento del bot.

### `configuration.yaml` de Home Assistant (añadir, no reemplazar)

```yaml
# Añadir al final del configuration.yaml de Home Assistant
# Muestra el estado del bot crypto en el panel de HA

rest:
  - resource: "http://localhost:7001/health"
    scan_interval: 60
    sensor:
      - name: "Crypto Bot Status"
        value_template: "{{ value_json.status }}"

  - resource: "http://localhost:7001/portfolio"
    scan_interval: 300
    authentication: basic
    username: "admin"
    password: "your_password_here"
    sensor:
      - name: "Crypto Portfolio EUR"
        value_template: "{{ value_json.total_value_eur | round(2) }}"
        unit_of_measurement: "€"
        icon: "mdi:currency-eur"

      - name: "Crypto Bot PnL"
        value_template: "{{ value_json.total_pnl_pct | round(2) }}"
        unit_of_measurement: "%"
        icon: "mdi:trending-up"
```

> **Nota:** Para que HA pueda llamar a `localhost:7001`, debe estar en la misma red del host. Si HA corre en un contenedor Docker de CasaOS, usar `http://host.docker.internal:7001` o la IP local de la RPi en lugar de `localhost`.

---

## Instalación como app de CasaOS (método recomendado)

CasaOS permite instalar apps personalizadas usando un archivo `docker-compose.yml`. Esto las integra en el panel de control de CasaOS con botones de inicio/parada, logs, etc.

### Pasos para instalar via CasaOS UI

1. Abrir CasaOS en `http://[IP-RPI]:80`
2. Ir a **App Store** → **Install a customized app**
3. Pegar el contenido del `docker-compose.yml` modificado de este documento
4. CasaOS lo registrará como app gestionada

### Alternativa: `casaos-app.yml` para instalación via CLI

```yaml
# casaos-app.yml — Definición de app para CasaOS
# Referencia: https://wiki.casaos.io/en/contribute/app-store

name: crypto-trader-bot
services:
  redis:
    image: redis:7-alpine
    container_name: cryptobot_redis
    restart: unless-stopped
    command: redis-server --maxmemory 128mb --maxmemory-policy allkeys-lru --save ""
    ports:
      - "6380:6379"
    volumes:
      - /DATA/AppData/crypto-trader/redis:/data
    networks:
      - cryptobot_net

  bot:
    build: .
    image: cryptobot_bot:latest
    container_name: cryptobot_bot
    restart: unless-stopped
    env_file: /DATA/AppData/crypto-trader/.env
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
    volumes:
      - /DATA/AppData/crypto-trader/data:/app/data
      - /DATA/AppData/crypto-trader/logs:/app/logs
      - /DATA/AppData/crypto-trader/model:/app/model
    networks:
      - cryptobot_net

  api:
    image: cryptobot_api:latest
    container_name: cryptobot_api
    restart: unless-stopped
    env_file: /DATA/AppData/crypto-trader/.env
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
    volumes:
      - /DATA/AppData/crypto-trader/data:/app/data:ro
    ports:
      - "7001:8000"
    networks:
      - cryptobot_net

  nginx:
    image: nginx:alpine
    container_name: cryptobot_nginx
    restart: unless-stopped
    volumes:
      - /DATA/AppData/crypto-trader/frontend:/usr/share/nginx/html:ro
      - /DATA/AppData/crypto-trader/nginx.conf:/etc/nginx/nginx.conf:ro
    ports:
      - "7000:80"
    networks:
      - cryptobot_net

networks:
  cryptobot_net:
    driver: bridge
```

> Si se usa la ruta de CasaOS, los directorios de datos van a `/DATA/AppData/crypto-trader/` en lugar de `~/crypto-trader/`. Ajustar el `backup_db.sh` con esta ruta.

---

## Mapa de puertos completo de la RPi (estado final)

```
Puerto 80   → CasaOS (NO TOCAR)
Puerto 443  → CasaOS HTTPS (NO TOCAR)
Puerto 8123 → Home Assistant (NO TOCAR)
Puerto 6379 → Redis de CasaOS, si existe (NO TOCAR)
─────────────────────────────────────────────────
Puerto 6380 → Redis del Crypto Bot (NUEVO)
Puerto 7000 → Dashboard web del bot (NUEVO)
Puerto 7001 → FastAPI del bot (NUEVO)
```

---

## Checklist de verificación antes de arrancar

```bash
# 1. Verificar que CasaOS sigue funcionando
curl -s -o /dev/null -w "%{http_code}" http://localhost:80
# Debe retornar 200 o 302

# 2. Verificar que Home Assistant sigue funcionando
curl -s -o /dev/null -w "%{http_code}" http://localhost:8123
# Debe retornar 200 o 302

# 3. Arrancar el bot
cd ~/crypto-trader
docker compose up -d

# 4. Verificar que los contenedores del bot están corriendo
docker compose ps
# Deben aparecer: cryptobot_redis, cryptobot_bot, cryptobot_api, cryptobot_nginx

# 5. Verificar que el dashboard del bot responde
curl -s -o /dev/null -w "%{http_code}" http://localhost:7000
# Debe retornar 200

# 6. Verificar que el API del bot responde
curl -s http://localhost:7001/health
# Debe retornar: {"status":"ok","redis":true,...}

# 7. Verificar que CasaOS Y Home Assistant siguen funcionando tras arrancar el bot
curl -s -o /dev/null -w "CasaOS: %{http_code}\n" http://localhost:80
curl -s -o /dev/null -w "HA: %{http_code}\n" http://localhost:8123

# 8. Ver logs del bot en tiempo real
docker compose logs -f bot
```

---

## Resolución de conflictos conocidos

### Redis: si CasaOS usa Redis en puerto 6379

Algunos servicios de CasaOS usan Redis internamente. El bot del crypto trader tiene su propio contenedor Redis en el puerto externo 6380, completamente separado. No comparten datos ni configuración.

```bash
# Para verificar si CasaOS usa Redis
docker ps | grep redis
# Si aparece un contenedor redis de CasaOS, no afecta al bot (el bot tiene el suyo propio)
```

### Si el puerto 7000 o 7001 ya están en uso por otra app de CasaOS

Editar el `docker-compose.yml` y cambiar los puertos de host:

```yaml
# Alternativa si 7000 o 7001 están ocupados
ports:
  - "7010:80"     # Dashboard en 7010
  - "7011:8000"   # API en 7011
```

Y actualizar la URL en el frontend (`app.js`) en consecuencia.

### Límites de memoria con tres sistemas corriendo

Con CasaOS, Home Assistant y el bot al mismo tiempo, la RPi 3B de 1 GB puede quedar ajustada. Monitorizar con:

```bash
# Ver uso de memoria en tiempo real
watch -n 5 'free -h && echo "---" && docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}"'
```

Si hay presión de memoria, reducir los límites del bot en `docker-compose.yml`:
```yaml
# Reducir si hay OOM
deploy:
  resources:
    limits:
      memory: 300M   # bot: bajar de 400M a 300M
      memory: 150M   # api: bajar de 200M a 150M
```

Y reducir la caché de velas en `.env`:
```bash
MODEL_CANDLES_REQUIRED=100   # Bajar de 200 a 100 si hay presión de memoria
```

---

*Adenda v1.0 — Convivencia con CasaOS y Home Assistant*  
*Prevalece sobre los documentos `crypto_trader_bot_spec.md` y `crypto_trader_bot_implementation.md` en todos los puntos aquí indicados.*
