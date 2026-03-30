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

| Parámetro | Anterior | Nuevo |
|-----------|----------|-------|
| BUY_THRESHOLD | 0.40 | 0.40 |
| SELL_THRESHOLD | 0.40 | **0.50** |
| STOP_LOSS_ATR_MULTIPLIER | 1.5 | **2.0** |
| TAKE_PROFIT_ATR_MULTIPLIER | 3.0 | **3.5** |
| MAX_DAILY_TRADES | 20 | **10** |

### Razón de los cambios
- **SELL_THRESHOLD 0.50**: El modelo tiene baja precisión en señales SELL (0.547), ser más selectivo reduce falsos positivos
- **STOP_LOSS 2.0x**: Más espacio para evitar stops prematuros por ruido
- **TAKE_PROFIT 3.5x**: Mejor ratio riesgo/beneficio (1:1.75)
- **MAX_DAILY_TRADES 10**: Reducir comisiones y operaciones de baja calidad

---

## Próximos Tests Recomendados

- [ ] Probar nueva configuración en simulador
- [ ] Período más largo (60-90 días)
- [ ] Entrenar modelo con más datos para mejorar SELL precision

---

## Modelo ML

- **Precision BUY:** 0.698 (objetivo: 0.6) ✅
- **Precision SELL:** 0.547 (objetivo: 0.6) ❌
- Necesita mejora en detección de señales SELL
