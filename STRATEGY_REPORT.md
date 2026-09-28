# Grid Trading en Demo — Informe de Estrategia, Config y Resultados

**Documento de traspaso para análisis externo.** Sin datos privados: sin claves de API, sin credenciales, sin IPs, sin nombres de usuario. Todos los importes y pares son públicos.

- **Fecha del informe**: 2026-09-28
- **Repositorio**: `trader-ia-v2` (rama activa, commit `4c13c32`; plan de corrección aplicado el 28 Sep 2026, commits `b17aa62`→`4c13c32`)
- **Modo**: `TRADING_MODE=demo` (paper trading, sin ejecución real)
- **Exchange de datos**: Kraken (precios públicos), pares BTC/EUR, ETH/EUR, SOL/EUR
- **Deployment**: Raspberry Pi 3 (ARM), Docker Compose (`redis` + `api`)
- **Fuente de los datos**: API REST del dashboard en producción. 404 snapshots, 214 fills, 159 operaciones, 155 ciclos cerrados.

> **Hallazgo principal**: el mecanismo del grid **funciona y es rentable** — 83,2% de acierto, +3,32% en 25,8 días, drawdown del 1,23%. Pero ese resultado **no es atribuible a la config desplegada**: la configuración cambió **3 veces** durante el periodo medido (§3.2) y el término de rebalanceo consume el 93% del PnL del grid con solo 10 observaciones (§4.5). No hay base estadística para el objetivo de 30 días.

---

## 1. Resumen ejecutivo

### 1.1 Métricas medidas (periodo 2-28 Sep 2026, 25,8 días)

| Métrica | Valor | Objetivo | Estado |
|---|---|---|---|
| PnL neto | **+3,3172 €** | — | — |
| Rentabilidad | **+3,32%** | 2-5%/mes | en rango |
| Retorno mensualizado simple | +3,88%/mes | 2-5% | en rango |
| Max drawdown | **1,234%** | ≤2% | cumple |
| Win rate | 83,2% (129/155) | — | alto |
| Profit factor | 1,647 | — | sano |
| Expectancy | +0,0215 €/ciclo | — | positiva |
| Sharpe diario (rf=0) | 0,319 (anual. 6,10) | — | bajo, muestra corta |
| Calmar | 2,69 | — | sano |
| Frecuencia | 6,01 ciclos/día | — | alta |
| Coste de comisiones | 29,2% del PnL bruto | <15% | alto |
| Nº de observaciones | 25,8 días / 155 ciclos | 30 días | insuficiente |

### 1.2 Config realmente desplegada (leída de `/api/bot/grid`)

| Parámetro | Valor en producción |
|---|---|
| Niveles por par | 15 |
| Spacing | 1,143% |
| Rango | ±8% |
| Leverage | 1 |
| Min lot | 2,00 € |
| Capital | 90% (30 €/par) |
| Rebalance | 8% |
| Stop-loss | 5% |
| Poll | 15 s |
| ATR-adaptive | off |
| Fees | maker 0,16% / taker 0,26% |

> El `.env` del repositorio local está desfasado respecto a producción (`LEVELS=6`, `LEVERAGE=2`, `MIN_LOT=5`). Todos los números de este informe usan los valores de producción.

### 1.3 Veredicto

La **maquinaria del grid es sólida**: captura el spread de forma consistente (percentiles p25 1,114% / p50 1,181% / p75 1,249% frente a un spacing teórico de 1,143%), 119 de 155 ciclos son operaciones limpias con 91,6% de acierto, y el drawdown cumple el objetivo con holgura. La economía unitaria funciona tal como se diseñó.

**Pero el resultado no es atribuible a la config desplegada**, por tres razones independientes:

1. **La config cambió 3 veces** (§3.2). El +3,32% es la suma de tres estrategias distintas. Solo la última (9 días, 117 ciclos) aporta +0,94 €.
2. **El resultado depende del rebalanceo, pero menos de lo que parecía** (§4.5). 145 ciclos de grid generan +6,43 €; los 5 rebalanceos reales restaron **−4,40 €** (23 fills en BD). El proxy "ciclos con movimiento ≥5%" sugería −3,09 € sobre 10 ciclos, pero son **5 eventos, no 10**.
3. **El balance no financiaba las posiciones durante el periodo medido** (§5.2). Con nocional de 2 €/nivel, el PnL se acreditaba al balance completo como si ese capital estuviese libre. **Corregido el 28 Sep 2026** (`4c13c32`): cada pierna mueve la caja por el importe completo y el patrimonio se valora con MTM de posiciones abiertas. El +3,32% histórico sigue siendo una lectura sobre capital libre.

**Recomendación**: el sistema merece seguir operándose en demo, pero no hay base para capital real. Desplegar primero el plan de corrección ya aplicado en el repo (`b17aa62`→`4c13c32`, §5), que incluye el modelo de capital decidido, y acumular 30 días estables con la config actual midiendo sobre patrimonio; el término de rebalanceo ya está instrumentado (§7.1).

---

## 2. Estrategia: qué hace exactamente

### 2.1 Construcción del grid

Al arrancar (o al recentrar), para cada par:

```
capital_total    = balance_eur × GRID_CAPITAL_PCT          # 100 × 0.90 = 90 €
capital_por_par  = capital_total / nº pares                # 90 / 3 = 30 €
rango            = precio_actual × GRID_RANGE_PCT          # semi-altura, 8%
precio_inferior  = precio_actual − rango
precio_superior  = precio_actual + rango
niveles          = 15
spacing          = (superior − inferior) / (niveles − 1)  # 16% / 14 = 1,143%
tamaño_nivel     = max(30/15, 2) = max(2,00, 2,00) = 2,00 €
```

`GRID_RANGE_PCT` es una semi-altura. El rango total es 2 × 8% = 16%.

**Ejemplo real (BTC, leído de `/api/bot/grid`):**

| Parámetro | Valor |
|---|---|
| Centro | 71 157,7 € |
| Rango | 65 465 — 76 850 € |
| Spacing | 1,143% (~813 €/nivel) |
| Niveles | 15 (7 compra / 7 venta / 1 central) |
| Tamaño por nivel | 2,00 € |

**Consecuencia de `niveles = 15` (impar)**: existe un nivel exactamente en el precio central, que dispara un fill instantáneo a spread 0 en la primera Poll tras cada rebalanceo, con PnL = −fee. Confirmado en los datos: 5 ciclos con `pnl == −fees`, todos coincidentes con recentrados (precios 105,84 / 98,34 / 91,04 / 84,26 en SOL y 2 220,06 en ETH). Pérdida total ≈ **−0,10 €**. Menor, pero es un bug real y recurrente.

### 2.2 Ciclo de vida de una orden

1. **Armado**: cada nivel nace abierto en su precio, con contra-orden pendiente.
2. **Disparo**: cada 15 s se lee el precio de mercado. Si el precio **cruza** el nivel, se ejecuta un fill **al precio de mercado**, no al precio del nivel.
3. **Fill de apertura**: `pnl = −fee`. Se crea la contra-orden un nivel más allá (compra → venta en `precio + spacing`).
4. **Cierre**: `pnl_cierre = (precio_cierre − precio_entrada) × cantidad − fee`
5. **Ciclo completo** = apertura + cierre.

### 2.3 Economía unitaria — verificada con datos reales

| Métrica | Teórico | Medido (119 ciclos "core") |
|---|---|---|
| Spread capturado | 1,143% | 1,17% (BUY) / 1,18% (SELL) |
| Comisiones (2 lados) | 0,32% | 0,32% |
| Margen neto | 0,823% | ~0,85% |
| PnL medio por ciclo | +0,0197 € | **+0,0159 €** |

El mecanismo captura el spacing correctamente. La diferencia entre teórico y medido viene del **notional medio de 3,84 €** (no 2,00 €): durante la primera mitad del periodo el min-lot era mayor (§3.2).

### 2.4 Rebalanceo

Si `|precio_actual − centro| / centro > 8%`:
- Se liquidan todas las posiciones abiertas al precio de mercado (con PnL real, puede ser pérdida).
- Se reconstruye el grid centrado en el precio actual.
- Se preservan los contadores acumulados de PnL, comisiones y trades.

El umbral coincide con el borde del rango, así que solo dispara cuando el precio sale del grid. Correcto por diseño (evita whipsaw dentro del band). **Comportamiento observado: 5 eventos de rebalanceo en 25,8 días = 0,19/día** (tabla 4.5.2).

### 2.5 Stop-loss

Si `PnL_total / (balance × capital_pct) < −5%` → halt. Con config viva: `100 × 0,90 × (−0,05) = −4,50 €`.

**Nunca se ha disparado** (mínimo registrado: 99,44 €). El drawdown real del 1,23% está a 4× del umbral. Dos problemas de diseño:

- **No es durable**: un reinicio del contenedor lo rearma sin mirar el estado guardado. Decisión operativa explícita, pero implica que el SL no protege nada frente a un crash-loop.
- **`enabled: true, running: false` es indistinguible** de una pausa manual. Un SL disparado sería invisible en el dashboard.

### 2.6 Lado short

Bidireccional, confirmado activo en los 3 pares (`short_supported: true`).

| Lado | Ciclos | PnL neto | Win rate | PnL medio |
|---|---|---|---|---|
| BUY (long) | 67 | **+3,5318 €** | 94,0% | +0,0527 € |
| SELL (short) | 88 | **−0,1962 €** | 75,0% | −0,0022 € |

**El lado corto es un drag**: 88 ciclos (57% del total) para −0,20 € netos. Combinado con §4.5, **8 de los 10 ciclos largos son aperturas SELL sobre SOL**, y 3 de los 5 eventos de rebalanceo liquidaron contra-órdenes SELL: el lado corto es el principal generador de pérdidas.

> **Advertencia de replicabilidad (verificada 28 Sep 2026)**: estos shorts del demo son **contra-lados virtuales sin coste**. En real no se pueden replicar en spot: `AssetPairs` de Kraken devuelve `margin=None` para XBT/EUR, ETH/EUR y SOL/EUR (Kraken retiró el margen spot; el corto solo existe en futuros). Además, un short con margen llevaría comisión de apertura de margen y **rollover cada 4 h** (el modelo no lo simula; con ciclos de 20–55 h serían ~6–14 rollovers por ciclo). Y lo más importante: una atribución tipo "el short pierde" está **confundida con la tendencia** — SOL subió ~25% en el periodo, así que el lado en contra (short) arrastró; en un mes bajista el long sería el que sufre. Los números por lado solo valen dentro de ese régimen.
> Por otro lado, el lado BUY del demo **sí** es replicable en spot real (compra con saldo EUR, cierre con la venta de inventario), que es el ciclo 2.2 normal.

---

## 3. Configuración

### 3.1 Variables (valores de producción)

| Variable | Default | Producción | Validada al arrancar |
|---|---|---|---|
| `GRID_ENABLED` | `true` | true | no |
| `GRID_PAIRS` | `BTC/EUR,ETH/EUR,SOL/EUR` | 3 pares | no |
| `GRID_LEVERAGE` | `1` | **1** | no |
| `GRID_LEVELS` | `15` | **15** | no |
| `GRID_MIN_LOT_VALUE_EUR` | `5` | **2** | sí (>0) |
| `GRID_CAPITAL_PCT` | `0.90` | 0.90 | no |
| `GRID_RANGE_PCT` | `0.05` | **0.08** | no |
| `GRID_REBALANCE_THRESHOLD` | `0.08` | 0.08 | no |
| `GRID_STOP_LOSS_PCT` | `0.05` | 0.05 | no |
| `GRID_POLL_INTERVAL` | `15` | 15 | no |
| `GRID_ATR_ADAPTIVE` | `true` | false | no |
| `GRID_MAX_LEVELS` | `15` | 15 | no |
| `GRID_PRICE_STALE_SEC` | `300` | 300 | **inoperante** |

**Variables sin validación capaces de degenerar el bot:**
- `GRID_LEVERAGE=0` → todos los PnL valen 0, el grid sigue "funcionando". `<0` → acredita PnL positivo en cada fill.
- `GRID_LEVELS=0` → `ZeroDivisionError` en cada poll.
- `GRID_CAPITAL_PCT=1.5` → despliega más del balance. No hay tope superior en ningún sitio.

### 3.2 La configuración cambió 3 veces durante el periodo medido

Reconstruido a partir del notional de cada ciclo en la BD (notional = `min_lot × leverage`):

| Época | Fechas | Min-lot | Leverage | Nocional/ciclo | Ciclos | PnL neto |
|---|---|---|---|---|---|---|
| **E1** | 02-14 Sep | 5 € | 2 | 10,00 € | 29 | **+3,3925 €** |
| **E2** | 16-19 Sep | 5 € | 1 | 5,00 € | 19 | −0,8022 € |
| **E3** | 19-28 Sep | 2 € | 1 | 2,00 € | 117 | **+0,9412 €** |

**Esto invalida la lectura directa del resultado global.**

- **E1 aporta el 102% del PnL neto total** y corre con **leverage 2**, exactamente lo que la política del proyecto prohíbe en demo. Ganó +3,39 € con solo 29 ciclos, en el tramo de mayor volatilidad.
- **E2** perdió −0,80 € en 19 ciclos.
- **E3 (config actual)** hizo +0,94 € en 9 días con 117 ciclos. **+0,11%/día**, que mensualizado da **+3,2%** — la única cifra atribuible a la config desplegada.

**Eficiencia por config:**

| Config | PnL por ciclo | Ciclos/día |
|---|---|---|
| E1 (lev 2, 10 €) | +0,1169 € | 1,81 |
| E3 (lev 1, 2 €) | **+0,0080 €** | **12,32** |

E3 gana 6,8× más ciclos al día pero 14,6× menos por ciclo. **E1 no era "mejor": fue más apalancada y coincidió con un tramo volátil.** Con 29 ciclos, la diferencia entre +3,39 € y −0,80 € es ruido estadístico, no edge.

### 3.3 Divergencia repo ↔ producción

| Variable | Repo (`.env`) | Producción | Impacto |
|---|---|---|---|
| `GRID_LEVELS` | 6 | **15** | Spacing 3,2% vs 1,143% |
| `GRID_LEVERAGE` | 2 | **1** | Nocional 2× |
| `GRID_MIN_LOT_VALUE_EUR` | 5 | **2** | Nocional 2,5× |

**El `.env` del repositorio no describe el sistema en producción.** Cualquiera que lea el repo creerá que corre con leverage 2 (prohibido por política) cuando en realidad corre con leverage 1. Es la corrección más barata y de mayor impacto del backlog.

---

## 4. Resultados — análisis detallado

### 4.1 Resultado global

| Métrica | Valor |
|---|---|
| Balance inicial | 100,0000 € |
| Balance actual | 103,3172 € |
| PnL neto | **+3,3172 € (+3,32%)** |
| Periodo | 25,8 días (02-28 Sep 2026) |
| PnL bruto | +4,7125 € |
| Comisiones totales | 1,3948 € (**29,2% del bruto**) |
| Ciclos cerrados | 155 (+ 4 abiertos) |
| Fills totales | 214 (59 aperturas, 155 cierres) |

**Conciliación verificada** (dos caminos independientes cuadran con el balance):

```
fills cierres   +3,7564
fills aperturas  −0,4392   (solo comisiones, como está diseñado)
                = +3,3172  == balance API

Σ pnl_eur de las 155 operaciones cerradas = +3,3356  (Δ de 0,018 por redondeo)
```

**La contabilidad del sistema es internamente consistente.** Punto fuerte real.

### 4.2 Equity curve (404 snapshots)

| Fecha | Balance | Δ diaria |
|---|---|---|
| 02 Sep | 100,0000 | — |
| 07 Sep | 99,5717 | **−0,4283** |
| 10 Sep | 100,1593 | +0,2950 |
| 11 Sep | 100,4568 | +0,2975 |
| **14 Sep** | 102,1671 | **+1,7103** |
| 15 Sep | 101,6805 | −0,4866 |
| 18 Sep | 102,3483 | +0,6840 |
| 21 Sep | 102,8800 | +0,2767 |
| 23 Sep | 103,4837 | +0,3899 |
| **24 Sep** | **103,7946** (pico) | +0,3109 |
| 25 Sep | 102,9603 | **−0,8343** |
| 28 Sep | 103,3172 | +0,1673 |

- **Máximo**: 103,7946 € · **Mínimo**: 99,4392 € · **Actual**: 103,3172 €
- **Max drawdown**: **1,234%**

Perfil esperado de grid: saltitos hacia arriba con retrocesos pequeños. El único movimiento diario relevante a la baja es **−0,83 € (25 Sep)**, que contiene el rebalanceo de 11 posiciones a 105,84 € (−1,2987 €). El salto de **+1,71 € (14 Sep) no es un rebalanceo**: son cierres normales de grid (dos lotes de 2 niveles el mismo tick, +0,68 € cada uno).

### 4.3 Métricas de calidad

| Métrica | Valor | Comentario |
|---|---|---|
| Win rate | **83,2%** (129/155) | típico de grid |
| Profit factor | **1,647** | sano |
| Expectancy | **+0,0215 €**/ciclo | positiva |
| Ganancia media | +0,0659 € | |
| Pérdida media | **−0,1984 €** | **3× la ganancia media** |
| Mejor / peor ciclo | +0,3480 / −0,6968 € | |
| Mediana | +0,0211 € | la mayoría son ciclos pequeños |
| Sharpe diario | 0,319 | anualizado 6,10 (sobreestimado: 23 días) |
| Sortino diario | 0,440 | |
| Calmar | 2,69 | |

**Asimetría clave**: la pérdida media es 3× la ganancia media. Inherente al grid (muchos ciclos pequeños, pocosloss grandes), y es exactamente por eso que el win rate alto **no** implica rentabilidad por sí solo: sin el profit factor >1, el mismo 83% de acierto sería pérdida.

### 4.4 Resultados por par

| Par | Ciclos | % del total | PnL neto | Fees | Bruto | Win rate | PnL medio | Duración media |
|---|---|---|---|---|---|---|---|---|
| **ETH/EUR** | 38 | 24,5% | **+2,7088 €** | 0,4196 | +3,1284 | 92,1% | +0,0713 | 33,0 h |
| **BTC/EUR** | 22 | 14,2% | **+1,7902 €** | 0,1991 | +1,9893 | **100,0%** | +0,0814 | 54,7 h |
| **SOL/EUR** | 95 | 61,3% | **−1,1634 €** | 0,7582 | −0,4052 | 75,8% | −0,0122 | 20,2 h |

**Descomposición del resultado por par:**

| Par | Grid normal (move <5%) | Rebalanceos (move ≥5%) | Fees |
|---|---|---|---|
| BTC/EUR | +1,7902 (22 ciclos) | +0,0000 (0) | 0,1991 |
| ETH/EUR | +3,4056 | −0,6968 (1) | 0,4196 |
| SOL/EUR | **+1,2315** (86 ciclos) | **−2,3949** (9) | 0,7582 |

**SOL es la historia clave del periodo.** Genera el 61% de los ciclos y **pierde dinero (−1,16 €)**, pero su grid normal es rentable (+1,23 € con 86 ciclos). La pérdida viene de **9 ciclos largos con movimiento ≥5% (−2,39 €)**, que coinciden casi en su totalidad con los **4 eventos de rebalanceo de SOL** (15, 18 y 18 y 25 Sep; 20 posiciones liquidadas, −3,3678 € en fills de BD). Sin ellos, SOL habría aportado +1,23 € en vez de −1,16 €.

**Las comisiones de SOL (0,7582 €) superan su PnL bruto absoluto (−0,4052 €).** Con 61% de los ciclos y el triple de frecuencia que BTC, SOL genera volumen de fills sin capturar suficiente spread.

### 4.5 El rebalanceo es un término material, no dominante

> **Advertencia de lectura.** Durante la primera redacción de este informe, §4.5 confundió un *proxy* estadístico con los eventos de rebalanceo reales. Las dos cosas son distintas y se separan a continuación. La tabla 4.5.1 agrupa **ciclos** por cuánto se movió el precio entre su entrada y su salida. La tabla 4.5.2 lista los **eventos de rebalanceo** que el bot registró realmente en el log.

#### 4.5.1 Vista por ciclos (proxy `move_pct`)

| Categoría | Ciclos | PnL neto | Fees | PnL medio |
|---|---|---|---|---|
| Grid normal (move <2%) | 117 | **+5,2035 €** | 0,7141 | +0,0445 |
| Tendencia (2-5%) | 28 | +1,2238 € | 0,5532 | +0,0437 |
| **Ciclos largos (≥5%)** | **10** | **−3,0917 €** | 0,1096 | **−0,3092** |
| **Total grid (move <5%)** | **145** | **+6,4273 €** | 1,2673 | +0,0443 |
| **Total** | **155** | **+3,3356 €** | 1,3769 | +0,0215 |

**Contribución al PnL: grid +193%, ciclos largos −93%.**

Sobre el reparto por lado de esos 10 ciclos largos: **9 son SELL y 1 es BUY**; de los SELL, **8 son de SOL/EUR** y 1 de ETH/EUR.

> ⚠️ Ese recuento de lados describe la **pierna de apertura** del ciclo (el SELL que se abrió y luego se recompró a la baja), **no** las liquidaciones. La tabla 4.5.2 es la que cuenta liquidaciones.

#### 4.5.2 Eventos de rebalanceo reales (fuente: log del bot)

| Fecha UTC | Par | Precio | Posiciones | PnL registrado | Fills en BD |
|---|---|---|---|---|---|
| 11 Sep 13:49:51 | ETH/EUR | 2 220,06 | 6 | +3,1050 | 3 de 6 |
| 15 Sep 18:45:50 | SOL/EUR | 84,26 | 3 | −1,0685 | 3 de 3 |
| 18 Sep 03:50:58 | SOL/EUR | 91,04 | 3 | −0,5017 | 3 de 3 |
| 18 Sep 19:15:39 | SOL/EUR | 98,34 | 3 | −0,4989 | 3 de 3 |
| 25 Sep 11:07:01 | SOL/EUR | 105,84 | 11 | −1,2987 | 11 de 11 |
| **Total** | | | **26** | **−0,2628** | **23 de 26** |

**Hubo 5 eventos de rebalanceo, no 10.** El desglose por lado de esas 26 posiciones, tal como las registra `_liquidate_pair_positions` (que persiste el **lado de la contra-orden**, no el de la posición), es **3 SELL y 20 BUY** — no 9 SELL.

#### 4.5.3 Corrección de la afirmación sobre 105,84 €

La primera versión de este informe afirmaba que «4 de las 5 últimas liquidaciones ocurrieron en una sola ventana de 4 horas (21 Sep 05:34-08:38)». **Es falso, y el error era doble:**

1. **La ventana 21 Sep 05:34-08:38 es la de las aperturas, no la de las liquidaciones.** Esas 4 posiciones se abrieron entonces; todas se liquidaron el **25 Sep a las 11:07:01**, en un único evento.
2. **Las 11 salidas a 105,84 € son un solo evento, no varios.** Las 11 comparten `exit_timestamp = 2026-09-25T11:07:01` y el mismo precio, porque `_liquidate_pair_positions()` cierra todos los niveles abiertos del par contra un único `current_price` antes de recentrar. El log lo confirma:

   ```
   11:07:01.030  fill a 105.84
   11:07:01.106  desviación 8.4% > 8%  → rebalanceo
   11:07:01.463  11 posiciones liquidadas @ 105.84 antes de recentrar
   11:07:01.474  nuevo centro 105.84
   11:07:16.494  fill a 106.00
   ```

**El precio no estaba congelado.** En 16 segundos el feed pasó de 105,84 a 106,00, lo que descarta un precio estancado. No es posible fechar la frescura con exactitud retrospectiva porque el código no guarda `price_ts` (ver §5.4), pero el movimiento posterior es evidencia directa contra la hipótesis de precio obsoleto.

Una de esas 11 posiciones es el **nivel central phantom** (§5.7): entró y salió a 105,84 con `pnl_eur = −0,0033`.

#### 4.5.4 Desfase entre el proxy y la realidad

De los 10 ciclos del proxy 4.5.1, **9 son liquidaciones reales** y **1 es un falso positivo**: el ciclo ETH del 2 Sep (2 082,00 → 2 220,06, −0,6968 €), que se cerró un mes *antes* de que existiera la función de liquidación (`fa6464d`, 7 Sep). Es un cierre de grid ordinario con un movimiento amplio, no un rebalanceo.

> **Las dos vistas no deben sumarse ni compararse.** 4.5.1 reparte el resultado por *ciclo* (cada ciclo una vez, según cuánto se movió el precio). 4.5.2 cuenta *fills de liquidación* (un ciclo puede aportar varias liquidaciones, y un fill de liquidación pertenece a un ciclo abierto días antes). Por eso −3,0917 € y −4,4042 € son dos descomposiciones distintas de la misma realidad, no dos estimaciones de la misma magnitud.

**El coste efectivo de los rebalanceos en el resultado registrado es de −4,4042 €** (23 fills en BD, tabla 4.5.2), concentrados en 5 eventos. Frente a los +6,4273 € del grid en 145 ciclos, **el rebalanceo se lleva ~40% del beneficio, no el 93% que sugería el proxy**. El grid no está al borde de la ruina, pero tampoco tiene el margen de seguridad que el informe original le atribuía.

**Con 0,19 rebalanceos/día y −0,88 € de media por evento, el término esperado del rebalanceo es ≈ −0,17 €/día**, frente a los +0,25 €/día del grid (E3). El margen de seguridad es de ~1,4× — suficiente para sobrevivir, **insuficiente para confiar**.

### 4.6 Tests de hipótesis sobre el sesgo de gap

El código dimensiona la cantidad al **precio del nivel** pero fija el `entry_price` al **precio de mercado** del fill. La hipótesis (§5.1) era que esto inflaba el PnL. **Los datos la refutan:**

| Métrica | Valor | Interpretación |
|---|---|---|
| Ciclos con movimiento > 1,5× spacing | **0 de 155** | No hay gaps |
| Ciclos con movimiento < 0,5× spacing | **155 de 155** | Ningún ciclo se mueve lo que debería |
| Spread capturado mediano | 1,181% | **Correcto**, no inflado |
| PnL real / teórico por ciclo | 0,682 | **El sesgo es negativo, no positivo** |

La causa del 0,682 es el **notional medio de 3,84 €** contra un teórico de 2,00 €: al calcular el PnL esperado se usó 2 €/nivel durante periodos en que el min-lot era 5 € o 10 €. No es un sesgo del mecanismo.

**Conclusión: el sesgo de gap no sesga el resultado en ninguna dirección. Los fills ocurren sistemáticamente en o cerca del nivel de trigger, como debe ser.** Esta hipótesis queda cerrada.

### 4.7 Overrides de comisión: noitados

`config_service` expone `maker_fee` y `taker_fee` como editables, con `min`/`max` declarados en el código pero **nunca aplicados**. Un `PUT` con `maker_fee = 5.0` haría que cada fill acredite PnL positivo. No hay evidencia de que haya ocurrido (los fees observados son 0,0033 € sobre notional de ~2 €, consistente con 0,16%), pero el hueco existe.

### 4.8 Métricas que no se pueden calcular con los datos actuales

- **Sharpe fiable**: 23 días con datos de retorno diario. El 6,10 anualizado es estadísticamente meaningless.
- **Pérdida por rebalanceo con significancia**: 5 eventos (y 20 de las 23 liquidaciones son del mismo par y en 3 ventanas de tendencia alcista, así que no son observaciones independientes).
- **PnL por lado desagregado por par**: solo agregado.
- **Coste de opportunity del capital ocioso**: el modelo no lo registra.

---

## 5. Problemas identificados en el código

Ordenados por impacto. Los marcados con ✓ han sido **confirmados o refutados empíricamente** con los datos de producción.

**Estado (28 Sep 2026)**: la lista mantiene los hallazgos del periodo medido con su resolución. Los marcados `[RESUELTO]` quedaron corregidos en el plan `b17aa62`→`4c13c32`, **cada corrección con su test de regresión** (suite local: **45 passed**, excluyendo los tests heredados de la era ML sin mantenimiento). Los que conservan tag sustantivo siguen abiertos.

### 5.1 [REFUTADO] El sesgo de gap en fills

Verificado en §4.6: 0 de 155 ciclos con gap, spread capturado correcto, sin sesgo direccional. **No es un problema.** La hipótesis inicial era razonable pero los datos no la sostienen.

### 5.2 [CRÍTICO — RESUELTO el 28 Sep 2026] El balance no financiaba las posiciones

**Problema (confirmado con datos)**: `update_balance` solo recibía el PnL. En una apertura el PnL era `−fee`. **No se debitaba el nocional al abrir ni se acreditaba al cerrar.** `total_value_eur` estaba forzado a ser idéntico a `balance_eur`.

Con la config de producción: 15 niveles × 2,00 € × 3 pares = **90 € de nocional nominal**, pero solo **~12 € simultáneos** (las 2 posiciones abiertas reales del BTC × 2,05 €). El PnL se acreditaba sobre un balance de 100 € del que no se había descontado nada.

**Implicación directa**: el +3,32% **no es un 3,32% de 100 € invertibles**. Es PnL sobre capital libre. En una cuenta real, esos 90 € estarían comprometidos y el drawdown se materializaría como pérdida de poder de compra. Este era el punto que más cambiaba la lectura del resultado.

**Corrección (commit `4c13c32`)**: el balance ahora **financia las posiciones** — cada pierna mueve la caja por el importe completo (`buy −amount×precio−fee`, `sell +amount×precio−fee`) — y `total_value_eur` es el **patrimonio** = caja + MTM de niveles abiertos (long suma, short resta), que es lo que debe medir el drawdown del objetivo ≤2%. La reconciliación del grid y la caja se basan en el ledger de trades (**`_rebuild_cash_from_ledger`**, idempotente, migra la contabilidad antigua sin recontar al arrancar). El PnL neto por ciclo es idéntico al anterior (spread − 2×fee); lo que cambia es dónde se refleja mientras el nivel está abierto.

**Verificación**: 7 tests nuevos sobre financiación de piernas, patrimonio con MTM (long/short) y migración desde el ledger (`test_grid_funding.py`), más actualización de `test_grid_persist_atomic.py`; **45 passed** en local. A partir de aquí, +2/5% mensual y drawdown deben medirse sobre **patrimonio**.

**Corrección posterior (28 Sep, deployment)**: financiar el nocional completo en el demo **infló el dashboard** (+58%): al acreditar el importe de los contra-lados short virtuales (sin margen, leverage 1) el balance saltó de ~103 € a 136 €, y el patrimonio mostró un flotante ficticio de +23 €. El modelo correto para el demo es el ya documentado en AGENTS.md: **el balance es PnL realizado** (cada fill acredita `spread − comisiones`, sin desplazar nocional), y lo que sí aporta la corrección es que `total_value_eur` valora las abiertas con MTM. Se revirtió el movimiento de nocional en `_persist_grid_fill` (se vuelve a acreditar `pnl`), `_rebuild_cash_from_ledger` pasó a `_rebuild_balance_from_ledger` (balance = `initial + Σ pnl_eur`, idempotente; los `pnl_eur` nulos se tratan como `−fee`), y el dashboard etiqueta la tarjeta "Balance libre" como **"PnL realizado"**. El patrimonio (con MTM) sigue siendo la métrica del objetivo.

### 5.3 [ALTO — confirmado] `value_eur` omite el leverage

El nocional creado es `tamaño × leverage`, pero `value_eur` graba `tamaño` sin leverage. `value_eur` es la base de `pnl_pct` en todo el sistema. Con `LEVERAGE=2` (config E1, que produjo el 102% del PnL), todos los porcentajes mostrados eran **2× el retorno real**.

En producción (leverage 1) el error no se manifiesta, pero el código sigue mal y la config E1 estuvo afectada.

### 5.4 [ALTO — RESUELTO] El guard de precio obsoleto es código muerto

`_get_price` intentaba leer `price_ts:{pair}`. **Ningún escritor existía en el repo.** La rama nunca se ejecutaba. La protección real es el TTL de 60 s de `price:{pair}`.

**Corregido (`241a6d7`)**: el guard muerto se eliminó. La consecuencia anexa (con `POLL_INTERVAL=15` y TTL 60 s, hasta 3 de cada 4 polls comparan contra un precio de hasta 60 s) es un límite de diseño del TTL, no código muerto.

### 5.5 [ALTO] Divergencia de contabilidad garantizada

1. **Posiciones indexadas por par**: máximo una posición *visible* por par, aunque el grid mantiene N concurrentes. Al cerrar un nivel se borra la visualización de todas las del par. **Confirmado en datos: 59 aperturas contra 155 cierres en la tabla de fills.** El estado real solo vive en la BD.
2. **Filas fantasma**: cada liquidación escribe una fila de cierre pero nunca actualiza la de apertura. `open_operations` crece monótonamente (4 operaciones "abiertas" en el sistema ahora mismo que ya no existen).
3. **No-atomicidad balance↔BD — RESUELTO (`c59faf7`)**: el balance se acreditaba en Redis *antes* del commit en SQLite; si el commit fallaba solo se emitía un warning. Ahora el trade se persiste en BD **primero** y solo después se mueve el balance (test de regresión: `test_grid_persist_atomic.py`).

### 5.6 [ALTO] Ningún límite de riesgo se aplica

`RiskManager` está instanciado pero **no tiene un solo call site**. Inertes: máx. posiciones abiertas, máx. trades/día, % de portfolio en cripto, balance mínimo, SL/TP por posición, cooldown por par, sizing por ATR.

El grid no tiene ningún cap de nocional ni de exposición por par. Los únicos límites son implícitos y no configurables.

### 5.7 [MEDIO — RESUELTO] Nivel central phantom con nº impar de niveles

§2.1. Con `LEVELS=15` había un nivel exactamente en el precio que disparaba un fill a spread 0 en cada rebalanceo. 5 casos confirmados, −0,10 € acumulados.

**Corregido (`8533c2b`)**: los niveles se desplazan media posición (`price + (i − (n−1)/2 + 0,5) × spacing`), por lo que ningún nivel cae sobre el centro con nº impar de niveles, sin obligar a `levels` par. Los 5 casos históricos responden a la config entonces vigente (test de regresión: `test_grid_center_phantom.py`).

### 5.8 [MEDIO] Otros defectos

| # | Defecto | Impacto |
|---|---|---|
| a | Un solo poll llena **todos** los niveles cruzados al mismo precio de mercado | Un wick abre N longs al mismo precio; en los datos 0 gaps, así que no se ha manifestado |
| b | Suelo de lot sin techo | Si el balance cae, el suelo fija el despliegue y `capital_pct` deja de ser restricción |
| c | Un segundo engine borra el lock y el estado del grid del primero | Relevante si se sube `--workers` |
| d | `min`/`max` de `config_service` declarados pero nunca aplicados (§4.7) | Un override de fee podría acreditar PnL positivo |
| e | `GRID_PAIRS` y `TRADING_PAIRS` se dividen sin normalizar — **RESUELTO** (`b17aa62` normaliza y descarta vacíos) | Un espacio o formato distinto dejaba un par sin precio; el capital se infrautilizaba en silencio |
| f | `GRID_LEVELS=0` → `ZeroDivisionError`; `GRID_LEVERAGE<0` → PnL positivo — **RESUELTO** (`b17aa62` valida variables críticas al arrancar) | Sin validación al arrancar |
| g | `trades` no tenía columna `reason` (ni `entry_price`/`exit_price`) — **RESUELTO** (`14019e7`: columna `reason` + migración; cierres por rebalanceo distinguibles en BD y `/api/trades`) | Obligaba a heurísticas externas para medir el término dominante (§4.5) |

### 5.9 [RESUELTO - histórico] PnL fantasma en la liquidación del 11 Sep 2026

**Síntoma**: el log del 11 Sep 13:49:51 registra `6 posiciones liquidadas @ 2220.06` en ETH/EUR, con PnL por posición `[+1,7675, +1,3719, +1,0021, −0,6808, −0,3395, −0,0161]`. En la BD solo existen **3 de esas 6** (los tres negativos). Los `id` 18, 19 y 20 faltan — los únicos huecos en todo el rango 1–217.

**Evidencia de que el dinero nunca se movió** (no es una pérdida de capital, es contabilidad en memoria):

| Snapshot | Timestamp UTC | Balance |
|---|---|---|
| 17 | 13:49:51.400 | 100,4756 € |
| 18 | 13:49:51.563 | 99,7948 € (Δ = −0,6808) |
| 19 | 13:49:51.593 | 99,4553 € (Δ = −0,3395) |
| 20 | 13:49:51.630 | 99,4392 € (Δ = −0,0161) |

El balance solo se movió 3 veces, exactamente por los 3 PnL negativos. Los +4,1415 € positivos **nunca se acreditaron**, y por eso la conciliación balance↔ledger cuadra exactamente (§6).

**Causa raíz**: la versión de `_liquidate_pair_positions` que corría ese día (`fa6464d`, 7 Sep) iteraba **todos** los niveles abiertos sin filtrar. Para los niveles originales —los de `id` entero `0, 1, 2`, que no proceden de un fill y por tanto **no tienen `cycle_id`**— acumulaba `state["pnl_eur"] += pnl` y los metía en la lista del log, pero suPnL nunca llegaba al balance ni a un `trades` nuevo. El commit **`c10b7d2` (15 Sep) añadió exactamente el guard que lo corrige**:

```python
for level in state["levels"]:
    if level["status"] != "open":
        continue
+   if not level.get("cycle_id"):
+       continue
```

**Estado actual: corregido.** Los 3 eventos posteriores (15, 18 y 25 Sep) liquidan exactamente las posiciones que el log dice, sin filas huérfanas. Los ids 18-20 son un artefacto histórico, no un defecto vigente, y no son reconstruibles: el dato solo existía en el estado en memoria del proceso del 11 Sep.

---

## 6. Lo que sí está bien

Para que el análisis sea equilibrado:

- **La construcción del grid es aritméticamente correcta.** La fórmula de niveles, spacing y sizing es limpia y consistente.
- **La economía unitaria está verificada empíricamente.** El spread capturado (p50 1,181%) coincide con el teórico (1,143%). El mecanismo hace lo que dice hacer.
- **La contabilidad cuadra exactamente.** `Σ(cierres.pnl) − Σ(aperturas.fee) ≡ Δbalance`, verificado por dos caminos independientes contra el valor de la API.
- **El drawdown cumple el objetivo** con holgura: 1,23% frente al límite del 2%.
- **Las cuatro ramas del PnL de liquidación son correctas** para un nivel de contra-orden.
- **El guard `cycle_id`** evita contabilizar PnL fantasma sobre órdenes no llenadas.
- **El umbral de rebalance igual al borde del rango** es la decisión de diseño correcta (evita whipsaw dentro del band).
- **El largo funciona** (94% de acierto, +3,53 € en 67 ciclos). El edge está donde se esperaba.
- **La rama ATR tiene un suelo de comisiones** (`spacing ≥ 2 × fee`) — buena intención, aunque el orden de las operaciones lo hace ineficaz.

---

## 7. Recomendaciones priorizadas

### 7.1 Tratar el rebalanceo como el riesgo dominante (revisado con la verdad de logs)

El rebalanceo consume ~40% del PnL del grid (5 eventos, −4,40 €) con 0,19 eventos/día. Tres acciones concretas:

1. ~~**Registrar los rebalanceos como eventos de primera clase**~~ — **HECHO (`14019e7`)**: `trades.reason` distingue cierres de grid de cierres de rebalanceo (columna + migración, expuesto en `/api/trades`). Instrumentación lista para decidir 8% → 6%: `SELECT pair, timestamp, COUNT(*), SUM(pnl_eur) FROM trades WHERE reason='rebalance' GROUP BY pair, timestamp`.
2. **Evaluar `rebalance_threshold = 6%` con datos**, no con intuición. Con 8% hay 0,19 eventos/día; a 6% serían ~0,4/día. El efecto sobre el PnL es ambiguo: liquidaciones más frecuentes pero de menor magnitud. **Instrumentar antes de decidir.**
3. **Considerar asimetría**: en un mercado en tendencia, el lado contrario al movimiento pierde en cada rebalanceo. 3 de los 5 eventos liquidaron contra-órdenes SELL mientras el precio subía, y 8 de los 10 ciclos largos son aperturas SELL en SOL. Un grid que solo abre un lado (o que reduce el notional del lado contrario tras N desviaciones) podría cortar el término dominante.

### 7.2 Bloqueantes para capital real

1. ~~**Decidir el modelo de capital** (§5.2)~~ — **HECHO (`4c13c32`)**: el balance financia las posiciones y el objetivo (+2/5% mensual, drawdown ≤2%) se mide sobre **patrimonio** (caja + MTM). Pendiente de despliegue en el demo (con la migración idempotente desde el ledger en el arranque).
2. **Alinear el `.env` del repo con producción** (§3.3). Una línea, elimina la confusión más cara del repositorio.
3. **⚠️ Lado short no replicable en real (nuevo, 28 Sep 2026)**: el "short" del demo es un contra-lado virtual sin coste. Kraken no ofrece margen spot (`margin=None` en AssetPairs de los 3 pares); el corto real solo existe en futuros, con comisión de apertura y rollover/funding periódico. **Decisión necesaria**: (a) grid long-only en real (compras con saldo EUR, ventas solo cierran inventario — el lado BUY sí replica), o (b) migrar el lado corto a futuros perpetuals con su propio modelo de funding. Hasta decidirlo, **el periodo demo no valida la salida a real**.
4. **⚠️ Lote mínimo por debajo del mínimo de Kraken (nuevo, verificado vía AssetPairs 28 Sep 2026)**: `GRID_MIN_LOT_VALUE_EUR=2` en spot real es rechazado (Kraken exige cantidad mínima en la moneda base, no solo `costmin`):
   - BTC: `ordermin=0.00005` → **3,68 €** al precio actual (el lote demo de 2 € ≈ 0,000027 BTC)
   - ETH: `ordermin=0.001` → **2,36 €**
   - SOL: `ordermin=0.06` → **6,26 €**
   
   El lote de 2 € **falla en los tres pares** (SOL es el más duro). Config real replicable necesita `GRID_MIN_LOT_VALUE_EUR ≥ 6,5 €` (SOL manda) o reducir pares/niveles; eso cambia el capital y el spacing respecto al demo actual. Verificar `ordermin`/`costmin` en `AssetPairs` antes de desplegar.
5. **Acumular 30 días con la config actual sin cambios.** E3 lleva 9 días. Los 3 cambios de configuración invalidaron 26 días de histórico.
6. **Instrumentar el drawdown y el nº de rebalanceos como métricas de primera clase** (§7.1).

### 7.3 Alta prioridad

5. ~~**Escribir `price_ts:{pair}` o borrar el guard muerto**~~ (§5.4) — **HECHO (`241a6d7`)**; el TTL de 60 s frente al poll de 15 s sigue siendo un límite de diseño a vigilar.
6. **Indexar las posiciones por par+nivel** (§5.5.1). Con 15 niveles el problema es 15× mayor que con 6.
7. **Limpiar las filas fantasma** de cada liquidación (§5.5.2); la parte de atomicidad balance↔BD ya está **HECHA** (`c59faf7`).
8. **Añadir un cap de exposición por par.**
9. **Revisar el lado short.** 88 ciclos para −0,20 € netos, 8 de los 10 ciclos largos y 3 de los 5 rebalanceos. Es la parte del sistema que pierde dinero de forma consistente.
10. ~~**Validar las variables críticas al arrancar**~~ (`leverage > 0`, `levels ≥ 2`, `0 < capital_pct ≤ 1`) — **HECHO (`b17aa62`)**.

### 7.4 Media prioridad

11. Corregir `value_eur` para que incluya el leverage (§5.3).
12. ~~**Eliminar el nivel central con nº impar de niveles, o forzar `levels` a par**~~ (§5.7) — **HECHO (`8533c2b`)** con offsets de media posición, sin obligar a par.
13. Distinguir en el dashboard "pausado" de "halt por stop-loss" (§2.5).
14. Aplicar el clamp declarado en `config_service` (§4.7).
15. Si se reactiva ATR-adaptive: aplicar el suelo de comisiones después del tope de niveles, no antes.

---

## 8. Contexto de la estrategia

**Marco original**: 2-5% mensual a leverage 1, con salida a real tras 30 días consecutivos de ≥2%/mes neto y drawdown máximo ≤2%.

**Regla de monitorización vigente**: no tocar la configuración por un único rebalanceo. Solo reevaluar el umbral (8% → 6%) o el lado short si hay un segundo rebalanceo en la misma semana.

**Sobre la regla de monitorización**: los datos la respaldan. Hubo **5 rebalanceos en 26 días** (11, 15, 18, 18 y 25 Sep), concentrados en 2 ventanas de tendencia alcista en SOL (15-18 Sep y 18-25 Sep). La semana del **18-25 Sep** concentra **2 eventos**, lo que habría activado la revisión del umbral y del lado short. **La regla es correcta y se habría disparado como estaba previsto; el problema es que el resultado de esa revisión no se aplicó.**

**Contexto de mercado**: el resultado se obtuvo en un periodo de volatilidad elevada. La frecuencia de ciclos —y por tanto el retorno— la pone el mercado, no el bot: E3 produce 12,3 ciclos/día frente a 1,81 de E1. En un mercado de baja volatilidad el mismo grid generará muy pocos ciclos y el retorno se desplomará. **El % mensual no se configura, se hereda de la volatilidad del mercado.** Los 6,0 ciclos/día del promedio no son una propiedad del sistema, son una propiedad de septiembre de 2026.

---

## Anexo A — Endpoints usados para extraer estos datos

```bash
GET /api/portfolio            # balance, posiciones abiertas, PnL
GET /api/portfolio/history    # equity curve (404 snapshots)
GET /api/trades               # fills individuales (214)
GET /api/trades/operations    # ciclos round-trip (159)
GET /api/trades/stats         # win rate, PnL, fees, max drawdown
GET /api/bot/grid             # estado del grid por par + config efectiva
GET /api/bot/status           # estado del motor
```

## Anexo B — Estructura del código relevante

| Archivo | Rol |
|---|---|
| `bot/strategies/grid_strategy.py` | Construcción del grid, fills, rebalanceo, liquidación |
| `bot/trading/engine.py` | Bucle de poll, reconciliación, restauración de estado |
| `bot/trading/portfolio.py` | Balance y posiciones (contabilidad) |
| `bot/trading/broker.py` | Comisiones (maker/taker), soporte de short |
| `bot/trading/risk_manager.py` | Límites de riesgo (inertes, sin call sites) |
| `bot/config.py` | Parsing y validación de config |
| `bot/database/crud.py` | Persistencia, reconstrucción de round-trips, estadísticas |
| `api/` | FastAPI + WebSocket, dashboard |
