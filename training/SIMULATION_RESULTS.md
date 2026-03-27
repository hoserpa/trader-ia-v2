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

| Métrica | Sim 1 (0.40) | Sim 2 (0.55) | Diferencia |
|---------|---------------|---------------|------------|
| Return | +2.47% | +1.39% | -1.08% |
| Win rate | 44.4% | 47.8% | +3.4% |
| Trades | 108 | 46 | -57% |
| Comisiones | 2.23€ | 0.94€ | -58% |
| Max DD | 2.96% | 3.21% | +0.25% |
| Sharpe | 1.71 | 1.21 | -0.50 |

---

## Conclusiones

1. **Umbral 0.40**: Más trades, más comisiones, mejor return histórico, peor win rate
2. **Umbral 0.55**: Menos trades, menos comisiones, peor return histórico, mejor win rate
3. El modelo con umbral alto es más selectivo pero pierde oportunidades

---

## Próximos Tests Recomendados

- [ ] Umbral 0.45 (intermedio)
- [ ] Umbral 0.50 
- [ ] Período más largo (60-90 días)
- [ ] Ajustar stop loss (2.0x en vez de 1.5x)
- [ ] Diferentes combinaciones de pares

---

## Modelo ML

- **Precision BUY:** 0.698 (objetivo: 0.6) ✅
- **Precision SELL:** 0.547 (objetivo: 0.6) ❌
- Necesita mejora en detección de señales SELL
