# Resultados de Simulaciones de Trading

Historial de simulaciones realizadas con el bot de trading para comparar diferentes configuraciones.

---

## Configuración General

| Parámetro | Valor |
|-----------|-------|
| Balance inicial | 100€ |
| Pares | BTC/EUR, ETH/EUR, SOL/EUR |
| Timeframe | 1h |
| Duración | 30 días |
| Timestamps procesados | ~720 |

---

## Simulación 1: Umbrales Estándar (0.40)

**Fecha:** 2026-03-27  
**Configuración:** `BUY_THRESHOLD=0.40`, `SELL_THRESHOLD=0.40`, `MAX_DAILY_TRADES=20`

### Resultados

| Métrica | Valor |
|---------|-------|
| Return total | +2.47% |
| Win rate | 44.4% |
| Total trades | 108 |
| Compras | 54 |
| Ventas | 54 |
| Ganadores | 24 |
| Perdedores | 30 |
| Max drawdown | 2.96% |
| Sharpe ratio | 1.71 |
| Comisiones | 2.23€ |
| Mejor trade | +1.43€ |
| Peor trade | -0.58€ |

### Análisis
- Alto volumen de trades (3.6/día)
- Comisiones elevadas (2.23% del capital)
- Win rate bajo (< 50%)
- Sharpe bueno (> 1.0)

---

## Simulación 2: Umbrales Selectivos (0.55)

**Fecha:** 2026-03-27  
**Configuración:** `BUY_THRESHOLD=0.55`, `SELL_THRESHOLD=0.55`, `MAX_DAILY_TRADES=4`

### Resultados

| Métrica | Valor |
|---------|-------|
| Return total | +1.39% |
| Win rate | 47.8% |
| Total trades | 46 |
| Compras | 23 |
| Ventas | 23 |
| Ganadores | 11 |
| Perdedores | 12 |
| Max drawdown | 3.21% |
| Sharpe ratio | 1.21 |
| Comisiones | 0.94€ |
| Mejor trade | +1.43€ |
| Peor trade | -0.88€ |

### Análisis
- Trades reducidos 57% (de 108 a 46)
- Comisiones reducidas 58% (de 2.23€ a 0.94€)
- Win rate mejorado (+3.4%)
- Return menor (-1.08%)
- Max drawdown ligeramente mayor

---

## Comparativa

| Métrica | Sim 1 (0.40) | Sim 2 (0.55) | Sim 3 (Actual) |
|---------|---------------|---------------|----------------|
| Return | +2.47% | +1.39% | -1.38% |
| Win rate | 44.4% | 47.8% | 41.7% |
| Trades | 108 | 46 | 72 |
| Comisiones | 2.23€ | 0.94€ | 1.45€ |
| Max DD | 2.96% | 3.21% | 3.86% |
| Sharpe | 1.71 | 1.21 | -1.0 |

---

## Conclusiones

1. **Sim 1 (0.40)**: Mejor retorno histórico (+2.47%), alto volumen de trades
2. **Sim 2 (0.55)**: Balanceado, buen win rate (47.8%), menos trades
3. **Sim 3 (Actual)**: Peor resultado, return negativo - **requiere revisión**
4. El modelo SELL necesita mejora (precision 0.547)

---

## Simulación 3: Umbral 0.45

**Fecha:** 2026-03-30  
**Configuración:** `BUY_THRESHOLD=0.45`, `SELL_THRESHOLD=0.45`

### Resultados

| Métrica | Valor |
|---------|-------|
| Return total | -1.38% |
| Win rate | 41.7% |
| Total trades | 72 |
| Compras | 36 |
| Ventas | 36 |
| Ganadores | 15 |
| Perdedores | 21 |
| Max drawdown | 3.86% |
| Sharpe ratio | -1.0 |
| Comisiones | 1.45€ |
| Mejor trade | +1.01€ |
| Peor trade | -0.88€ |

### Análisis
- **Peor resultado de todas las simulaciones**
- Return negativo (-1.38%)
- Sharpe negativo indica mal retorno por riesgo
- Win rate bajo (41.7%)
- Pierde más de lo que gana

---

## Nueva Configuración Aplicada

**Fecha:** 2026-03-30  
**Estado:** REVETIDA - Volvemos a configuración original (Simulación 1)

| Parámetro | Valor |
|-----------|-------|
| BUY_THRESHOLD | 0.40 |
| SELL_THRESHOLD | 0.40 |
| STOP_LOSS_ATR_MULTIPLIER | 1.5 |
| TAKE_PROFIT_ATR_MULTIPLIER | 3.0 |
| MAX_DAILY_TRADES | 20 |

---

## Próximos Tests Recomendados

- [ ] Reentrenar modelo con más datos para mejorar SELL precision
- [ ] Probar período más largo (60-90 días)
- [ ] Probar otras combinaciones de pares

---

## Fase B: Reentrenamiento con labeling event-based (2026-08-10)

**Objetivo:** corregir las causas raíz del modelo desplegado (label 90min ≠ operativa real, oversampling con duplicados, sin feature de par, datos Binance vs Kraken, regla de decisión rota).

### Cambios aplicados al pipeline

| Fix | Detalle |
|-----|---------|
| Labeling event-based | BUY/SELL = el precio toca TP (3×ATR) antes que SL (2.5×ATR) en 32 velas (8h), replicando la estrategia real |
| Sin oversampling | `--oversample-strength 0.0` por defecto (los duplicados idénticos causaban sobreajuste) |
| Sin `class_weight=balanced` | La distribución natural (BUY 35%, SELL 38%, HOLD 27%) no requiere rebalanceo |
| Feature de par | `pair_id` (0/1/2) en features, entrenamiento e inferencia |
| Datos Kraken | 21,801 velas 15m por par (dic-2025 → ago-2026) exportadas de la DB del bot (exchange real de producción) |

### Métricas walk-forward (label_32, sin oversampling)

| Métrica | Antes (modelo 2026-07-01) | Después |
|---------|---------------------------|---------|
| AUC ovr | 0.566 | **0.6115** |
| Precision BUY | 0.095 | 0.4318 |
| Precision SELL | ~0.28 | 0.4127 |
| F1 macro | 0.281 | 0.4073 |

Mejora sustancial de calibración, pero precision out-of-sample por debajo del breakeven de la estrategia con fees (~48-50%).

### Backtest realista out-of-sample (26-jun → 10-ago, el período en vivo)

Reproduce la lógica real del bot (TP/SL, trailing +0.8%, parcial 50% @1.5R, force-close, fees maker 0.16%, max 3 trades/día) sobre el split de test que el modelo nunca vio:

| Umbral | Trades | WR | PnL neto | Fees |
|--------|--------|-----|----------|------|
| 0.40 | 139 | 44.6% | **-2.34€** | 2.99€ |
| 0.45 | 139 | 49.6% | **-2.08€** | 3.00€ |
| 0.50 | 132 | 46.2% | **-2.30€** | 2.80€ |
| 0.55 | 44 | 50.0% | **-0.70€** | 0.96€ |

**Conclusión:** el modelo reentrenado NO supera el gate de despliegue en ningún umbral. El PnL neto es negativo en todos los casos; incluso con 0.55 (44 trades) pierde dinero. El volumen de fees supera el edge real del modelo.

### Decisión

- **NO se despliega** el modelo reentrenado (gate bloqueado).
- **Producción se mantiene** con la Fase A: predictor corregido (argmax + umbral 0.55) + riesgo endurecido (max 3 trades/día, cooldown 180min). En la práctica el bot emite HOLD salvo confianza ≥ 0.55, lo que corta la sangría de fees.
- **Gate de despliegue endurecido** en `export_model.py`: ahora es bloqueante y exige `backtest_results.json` con PnL neto > 0, WR ≥ 48% y precision test ≥ 0.45 (ejecutar `backtest_model.py` antes de exportar).

### Siguientes pasos sugeridos (fuera de scope de esta iteración)

1. Features con memoria más larga (tendencias multicuadro, acumulaciones de retornos 24h+) o características de sentimiento/microestructura.
2. Timeframe mayor (1h/4h) con horizonte de 8h para reducir ruido y fees relativas.
3. Estrategia con R:R más favorable (TP 4-5×ATR con entrada más selectiva) para bajar el breakeven por debajo de 45%.
4. Validar con backtest de 2+ ciclos completos walk-forward antes de cada despliegue.

---

## Modelo ML

- **Precision BUY:** 0.698 (objetivo: 0.6) ✅
- **Precision SELL:** 0.547 (objetivo: 0.6) ❌
- Necesita mejora en detección de señales SELL
