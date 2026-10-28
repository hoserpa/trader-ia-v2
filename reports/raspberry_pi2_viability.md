# Informe de viabilidad para Raspberry Pi 2 Model B

## Objetivo

Este informe describe el proyecto actual, su estrategia, requisitos y la viabilidad de ejecutarlo en una Raspberry Pi 2 Model B. Está pensado para que otro modelo de IA pueda evaluar si conviene continuar con el proyecto actual o empezar desde cero.

## 1. Descripción del proyecto

- Nombre: trader-ia-v2
- Tipo: bot de trading grid en modo demo sobre Kraken.
- Activos: BTC/EUR, ETH/EUR, SOL/EUR.
- Lenguaje: Python 3.11.
- Backend: FastAPI (el grid corre dentro del proceso `api`).
- Frontend: dashboard Vue.js servido como estáticos.
- Persistencia: SQLite + Redis.
- Despliegue actual: Docker Compose (`redis` + `api`), probado en Raspberry Pi 3.
- Estado: demo-only. El ML/entrenamiento antiguo fue eliminado y archivado.

## 2. Estrategia actual

El bot implementa un grid de rango fijo:

- 3 pares: BTC/EUR, ETH/EUR, SOL/EUR.
- 15 niveles por par.
- Rango configurado: ±8% alrededor del precio central (rango total 16%).
- Espaciado aproximado: 1.14%.
- Leverage: 1x.
- Capital por grid: 90% del patrimonio.
- Lote mínimo: 8 EUR.
- Polling: cada 15 segundos.
- Rebalanceo: si el precio se desvía más del 8% del centro.
- Stop-loss global: -5% del capital invertido.
- Fees: maker 0.16%, taker 0.26% (Kraken).
- Shorts simulados en demo con comisiones de margen (apertura 0.02%, rollover 0.025% cada 4h).
- Liquidación de margen simulada con niveles 80% (call) y 40% (stop).

La estrategia captura el spread entre niveles. El espaciado debe cubrir al menos las comisiones de ida y vuelta.

## 3. Requisitos de software

Dependencias principales (`bot/requirements.txt`):

- pandas >= 2.0.0
- ccxt >= 4.0.0
- sqlalchemy >= 2.0.0
- redis >= 5.0.0
- httpx >= 0.25.0
- websockets >= 10.0
- loguru >= 0.7.0
- python-dotenv >= 1.0.0

API (`api/requirements.txt`) añade FastAPI, uvicorn y dependencias web.

Servicios:

- Redis 7 (128 MB límite configurado).
- API Python (512 MB límite configurado).
- SQLite en disco.
- Frontend estático.

## 4. Requisitos de hardware (Raspberry Pi 2 Model B)

Especificaciones relevantes de Pi 2 Model B:

- CPU: ARM Cortex-A7 quad-core a 900 MHz (ARMv7).
- RAM: 1 GB.
- Red: Ethernet 10/100 (sin WiFi integrado).
- Almacenamiento: microSD.
- USB: 4 puertos.

El proyecto original está pensado para Pi 3. Pi 2 tiene menos CPU y la misma RAM de 1 GB.

## 5. Viabilidad en Pi 2 Model B

### Factores favorables

- La carga de trabajo es ligera: un bucle de 15 segundos, 3 pares, API local y dashboard.
- No usa modelos de ML en la operativa actual.
- Redis y SQLite son ligeros para este volumen.
- El contenedor API tiene límite de 512 MB y Redis de 128 MB.

### Riesgos y limitaciones

- 1 GB de RAM es justo para OS + Docker + Python + Redis + SQLite. Puede haber OOM si se añaden más pares o servicios.
- La compilación de dependencias (pandas, ccxt, cryptography) en ARMv7 es lenta y puede agotar memoria o tiempo. El Dockerfile ya usa multi-stage build, pero el build en Pi 2 será pesado.
- La imagen base `python:3.11-slim` debe existir para ARMv7; si no, el build fallará.
- La microSD sufre desgaste por SQLite, logs y snapshots frecuentes.
- La red Ethernet 100 Mbps es suficiente para Kraken, pero no ideal para depuración remota intensiva.
- El dashboard y la API añaden consumo de memoria y CPU aunque sean ligeros.

### Conclusión de viabilidad

Es viable con restricciones, pero no es la plataforma recomendada. Pi 2 puede ejecutar el bot si se evita compilar en la Pi (usar imagen preconstruida) y se limita la carga. Para producción o crecimiento, se recomienda Pi 3B+, Pi 4 o Pi 5.

## 6. Recomendaciones para Pi 2

- No compilar en la Pi: construir la imagen en otra máquina ARM compatible o usar una imagen ya publicada.
- Activar swap (al menos 1-2 GB) para evitar OOM durante arranque.
- Reducir carga: empezar con 1-2 pares y 10 niveles.
- Desactivar el dashboard si no es necesario, o servirlo desde otra máquina.
- Usar una microSD de buena calidad o almacenamiento USB/SSD.
- Configurar `mem_limit` y monitorizar memoria.
- Evitar `pandas` si se puede sustituir por cálculos simples, pero eso implica refactor.

## 7. ¿Empezar desde cero?

No es necesario empezar desde cero para usar Pi 2. El código actual ya está probado y el grid es simple. Un reinicio completo solo tendría sentido si se quiere:

- Eliminar Docker y ejecutar el bot como proceso nativo.
- Sustituir Redis por persistencia en archivo o SQLite.
- Reducir dependencias (especialmente pandas).
- Simplificar el dashboard o eliminarlo.

El coste de reescribir es alto y arriesga perder la contabilidad y las correcciones ya implementadas. La opción más razonable es optimizar el despliegue actual para Pi 2, no reescribir el proyecto.

## 7b. Correcciones a tener en cuenta

- El rango configurado es ±8% (total 16%), no 8% total.
- El umbral de rebalanceo (8%) coincide con el límite del rango, por lo que el recentrado ocurre cuando un lado ya está en máxima exposición.
- El lote mínimo de 8 EUR cuadruplica el lote natural con ~100 EUR de balance y 3 pares; el tope de exposición de la Fase 4.2 es necesario.
- Las correcciones del plan.md no están aplicadas en el ledger histórico: sigue habiendo PnL fantasma.

## 8. Preguntas para otro modelo de IA

1. ¿Es viable ejecutar este stack en Pi 2 con 1 GB de RAM sin OOM?
2. ¿Conviene usar Docker en Pi 2 o ejecutar el bot nativamente?
3. ¿Qué cambios mínimos permitirían reducir memoria y CPU sin reescribir el bot?
4. ¿Es razonable mantener pandas/ccxt en Pi 2 o conviene sustituirlos?
5. ¿Qué estrategia de persistencia es más adecuada para microSD (SQLite vs Redis)?
6. ¿Qué nivel de fiabilidad se puede esperar de un grid en demo con este hardware?
PY