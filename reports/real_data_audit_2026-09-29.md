# Auditoría de datos reales del grid (desde 2026-09-29)

Fuente de datos: SQLite de producción copiada del contenedor `crypto_api` (`/app/data/crypto_trader.db`) el 2026-10-26.
Periodo analizado: `2026-09-29 07:39:50` a `2026-10-26 09:51:14` (todos los trades del ledger están en este rango).
Modo de todos los trades: `demo`. No se han inventado ni interpolado datos.

## Resumen ejecutivo

- Balance inicial: 250.0000 EUR.
- Balance actual (ledger): 262.0994 EUR.
- PnL realizado bruto registrado: +12.0994 EUR (+4.84% sobre el capital inicial).
- PnL por patrimonio (último snapshot): +10.6724 EUR (+4.27%).
- Drawdown máximo observado: 0.5485%.
- Trades registrados: 391 (193 aperturas, 198 cierres).
- Cierres ganadores: 176; perdedores: 22 (win rate 88.9%).
- Comisiones de trading totales: 4.9871 EUR.
- Comisiones de margen (aperturas short, estimadas por fallback): 0.1121 EUR.
- Posiciones abiertas actuales: 10 (nocional ~80 EUR).
- Trades `rebalance` fantasma detectados: 11, con PnL inflado de +7.6232 EUR.

Corrección por filas fantasma (11 filas): PnL realizado pasa de +12.0994 EUR a +5.0732 EUR. Este ajuste es el principal hallazgo del informe.

## Desglose por par

| Par | Trades | PnL ledger (EUR) | Comisiones (EUR) |
|---|---:|---:|---:|
| SOL/EUR | 198 | 6.6718 | 2.5202 |
| ETH/EUR | 118 | 3.0695 | 1.5055 |
| BTC/EUR | 75 | 2.3581 | 0.9614 |
| Total | 391 | 12.0994 | 4.9871 |

## Filas `rebalance` fantasma

El código anterior liquidaba aperturas resting como si fueran posiciones vivas. Se detectan 11 filas `rebalance` cuyo ciclo estaba plano (número de trades anteriores en el mismo `cycle_id` par):

- 2026-10-08: 7 filas, PnL +4.7818 EUR.
- 2026-10-24: 4 filas, PnL +2.8414 EUR.
- Total inflado: +7.6232 EUR.

IDs afectados: 123, 124, 126, 134, 135, 136, 138, 359, 360, 361, 363.
El script `scripts/repair_phantom_rebalances.py` las detecta exactamente y puede borrarlas con backup + CSV usando `--apply`.

## Patrimonio y drawdown

- Primer snapshot: 2026-09-29 07:23:52, balance 250.0000, total_value 250.0000.
- Último snapshot: 2026-10-26 10:48:03, balance 262.0994, total_value 260.6724, PnL 10.6724 EUR (4.2690%).
- Mínimo total_value: 249.9424 EUR.
- Máximo total_value: 262.0994 EUR.
- Drawdown máximo: 0.5485%.

El balance (realizado) y el ledger cuadran exactamente: 250.0000 + 12.0994 = 262.0994.

## Posiciones abiertas actuales

La tabla `positions` está vacía (el grid no persiste posiciones ahí). Las posiciones vivas se reconstruyen desde `trades`. Actualmente hay 10 piernas abiertas (nocional ~80 EUR), todas long en BTC/ETH y short en SOL. La diferencia balance - total_value (-1.427 EUR) corresponde al no realizado de esas piernas.

## Código y funcionamiento

- La contabilidad se reconstruye correctamente desde `trades` usando `pnl_eur` en cierres y restando fee (+ margen en aperturas short).
- `get_stats_summary` cuadra con el balance cuando se usa el ledger completo.
- El bug de filas fantasma está corregido en el código actual (aperturas resting ya no se liquidan con PnL).
- Las filas históricas no tienen `margin_fee_eur` ni `open_trade_id` (columnas añadidas por migración posterior), por lo que el emparejamiento de operaciones en el dashboard usa heurística de precio para datos antiguos. Los trades nuevos sí guardarán esos campos.
- `model_decisions` contiene 43240 filas de un sistema ML anterior (2026-03-26 a 2026-08-26), con 543 ejecutadas. No es usado por el grid actual. No aporta al PnL del grid.
- `system_logs` y `bot_config` están vacíos.
- `positions` está vacía por diseño del grid (usa `trades`).

## Conclusión

El grid demo ha generado +12.0994 EUR (+4.84%) en 28 días, pero +7.6232 EUR (+3.05 puntos porcentuales) provienen de filas fantasma. El rendimiento real estimado es +4.4762 EUR (+1.79%), por debajo del objetivo del 2% mensual. El drawdown ha sido bajo (0.55%). La corrección del bug y la limpieza del ledger son necesarias antes de evaluar el hito de salida a real.
PY