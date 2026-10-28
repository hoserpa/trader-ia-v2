# Calibracion backtest vs demo real

Periodo comun disponible: 2026-09-29 a 2026-09-30 (2 dias). Los datos historicos descargados terminan el 2026-09-30; el demo real continua hasta 2026-10-26.

## Resultado

- Real: 41 trades, PnL +1.8246 EUR.
- Backtest (5m, fill=close): 3 trades, PnL -0.135 EUR.

Fuera de tolerancia (±20% ciclos, ±35% o ±1 EUR PnL). El modelo de relleno por cierre de vela de 5 minutos pierde la granularidad del sondeo de 15 s del bot.

## Accion

Ajustar el modelo de relleno o la resolucion (1 minuto) para las finalistas. No se han tocado parametros de la estrategia.