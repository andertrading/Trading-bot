# Auditoría de puntuaciones y niveles de riesgo — 2026-11-22

## Conclusión ejecutiva

**Las puntuaciones actuales no están calibradas como probabilidades ni demuestran una relación monotónica entre puntuación y rendimiento.** Los niveles A/B/C deben seguir siendo provisionales y no deben interpretarse como grados de probabilidad de acierto. Con la muestra disponible, tampoco hay base para aumentar el riesgo de las señales de mayor puntuación.

Esta auditoría es descriptiva. No modifica la estrategia, sus umbrales ni los porcentajes de riesgo.

## Fuente y límites

- Artefacto: `resultados-backtest-v3`, workflow run [44836202579](https://github.com/andertrading/Trading-bot/actions/runs/44836202579), generado el 2026-11-20.
- Commit de estrategia: `0156ae5ff8555a4e93cd095a59682e14534c1798`, coincidente con la versión de `strategies.py` inspeccionada en `main`.
- Datos del artefacto: Yahoo Finance; la versión del motor de datos del backtest era anterior a las rutas actuales.
- 599 operaciones ejecutadas: 537 M15 y 62 diarias.
- Resultados brutos en R; `costs_included=False` y `position_sizing_included=False`.
- Cobertura temporal limitada: aproximadamente dos meses en M15 y dos años en diario. No es una validación walk-forward ni una muestra suficiente para certificar estabilidad.

## Resultados por nivel actual

Los niveles reproducen la función actual de `risk_manager.py`: C = puntuación 70–79; B = 80–89; A = 90–100. PF es profit factor calculado sobre los resultados brutos.

| Temporalidad | Nivel | Operaciones | Media R/operación | Acierto | Profit factor | Resultado R |
|---|---:|---:|---:|---:|---:|---:|
| M15 | C | 216 | -0,323 | 22,7% | 0,592 | -69,669 |
| M15 | B | 218 | -0,070 | 33,0% | 0,900 | -15,234 |
| M15 | A | 103 | -0,103 | 32,0% | 0,857 | -10,651 |
| Diario | C | 18 | -0,219 | 27,8% | 0,736 | -3,946 |
| Diario | B | 38 | -0,065 | 28,9% | 0,909 | -2,457 |
| Diario | A | 6 | -0,395 | 16,7% | 0,526 | -2,371 |

### Lectura correcta

- En M15, B supera a A en media R y PF. A no supera a B ni a C de forma monotónica.
- En diario, B obtiene el mejor resultado descriptivo, pero A solo tiene seis operaciones: muestra insuficiente para inferir nada fiable.
- Los tres niveles presentan resultado agregado negativo en ambas temporalidades.
- El mejor resultado de B no demuestra que el umbral 80 sea óptimo; elegirlo a partir de esta misma muestra sería sobreajuste.
- No se han deducido probabilidades de éxito a partir de estas tasas de acierto.

### Resultado diario por activo

La muestra diaria también está muy concentrada y es pequeña por activo (entre 2 y 12 operaciones). Los resultados brutos observados fueron:

| Activo | Operaciones | Acierto | Media R | Resultado R | PF |
|---|---:|---:|---:|---:|---:|
| AAPL | 9 | 55,6% | +0,796 | +7,162 | 2,200 |
| XRP | 7 | 57,1% | +0,715 | +5,002 | 2,668 |
| BTC | 5 | 40,0% | +0,200 | +1,002 | 1,334 |
| ETH | 9 | 33,3% | +0,001 | +0,009 | 1,001 |
| SOL | 5 | 20,0% | -0,400 | -2,000 | 0,500 |
| MSFT | 2 | 0,0% | -1,000 | -2,000 | 0,000 |
| XLM | 3 | 0,0% | -1,000 | -3,000 | 0,000 |
| NVDA | 12 | 16,7% | -0,413 | -4,950 | 0,505 |
| TSLA | 5 | 0,0% | -1,000 | -5,000 | 0,000 |
| TTWO | 5 | 0,0% | -1,000 | -5,000 | 0,000 |

Esto no permite concluir que AAPL/XRP deban mantenerse ni que TSLA/TTWO deban eliminarse: las muestras por activo son demasiado pequeñas, los datos proceden del motor anterior y no incluyen costes. Sí demuestra que agregar todos los activos puede ocultar diferencias importantes y que la validación debe informar resultados por activo sin seleccionar ganadores en la misma muestra.

## Distribución de puntuaciones individuales

La segmentación exacta por puntuación confirma que tampoco hay una progresión estable. En M15, por ejemplo:

| Puntuación | Operaciones | Media R | Acierto | PF |
|---:|---:|---:|---:|---:|
| 70 | 9 | -0,624 | 11,1% | 0,298 |
| 75 | 207 | -0,309 | 23,2% | 0,607 |
| 80 | 164 | +0,017 | 36,0% | 1,025 |
| 85 | 54 | -0,333 | 24,1% | 0,597 |
| 90 | 88 | -0,129 | 31,8% | 0,823 |
| 100 | 15 | +0,047 | 33,3% | 1,069 |

Los grupos de 90 y 100 puntos son distintos en muestra y no constituyen una curva de calibración. El grupo de 100 puntos solo contiene 15 operaciones M15.

## Qué significa para el diseño

La puntuación actual es una suma de reglas técnicas discretas (tendencia, RSI, MACD, ruptura y volumen). No es una probabilidad estadística. El código asigna más riesgo a A que a B y más a B que a C, pero los datos disponibles no justifican esa jerarquía como asignación óptima.

**Decisión provisional recomendada:**
1. Mantener A/B/C etiquetados como niveles heurísticos, no probabilidades.
2. No incrementar el riesgo por puntuación ni cambiar los umbrales usando esta muestra.
3. Para la investigación siguiente, guardar puntuación y componentes de cada señal, y evaluar resultados netos por bloques de puntuación en periodos cronológicos no usados para diseñar la estrategia.
4. Separar temporalidades y activos; no mezclar M15 con diario para calibrar.
5. Exigir muestra suficiente, intervalos de confianza y estabilidad entre periodos antes de calibrar probabilidades o asignar riesgo diferenciado.
6. Repetir el estudio con el proveedor y los ajustes de precios actualmente configurados, con costes y deslizamiento realistas.

## Estado

Esta auditoría no habilita alertas operables ni operativa real. El sistema sigue en investigación; la elegibilidad económica de activos y las puertas de costes/datos continúan siendo requisitos independientes.
