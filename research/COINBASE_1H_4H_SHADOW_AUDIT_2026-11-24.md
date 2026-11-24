# Auditoría económica y OOS de la investigación Coinbase 1h/4h — 2026-11-24

## Estado ejecutivo

**Conclusión: ninguna de las dos temporalidades queda aprobada para producción.** La prueba confirma que el remuestreo M15 → 1h/4h funciona técnicamente, pero no resuelve todavía el problema de expectativa neta.

- 1h: resultado bruto ligeramente positivo en test (+6.0938R / 243 operaciones), pero la sensibilidad de comisión conocida lo lleva a -472.6132R. La comisión de equilibrio sería aproximadamente 0.0127% por lado, antes de spread, deslizamiento y otros costes.
- 4h: resultado bruto negativo en test (-6.0341R / 50 operaciones), antes de costes.
- No hay costes ejecutables completos ni equivalencia de precio Coinbase/eToro verificada. Por tanto, la expectativa neta real permanece desconocida.
- No se modifican estrategias, umbrales, sizing, universo ni escáner de producción.

## Procedencia e integridad

- Workflow: [run 45503847689](https://github.com/andertrading/Trading-bot/actions/runs/45503847689)
- Artefacto: `coinbase-resampled-1h-4h-shadow-45503847689`
- SHA-256 ZIP: `a0a829d40dbf543d87c4c4bd010f4d58c0846def98618b48255c4ba67fff179a`
- Commit del workflow: `5504ac4fe2ba3d9332ff5fac0dd928dd05d79989`
- Fuente: histórico Coinbase retenido, sin nuevas peticiones de red (`network_requests=0`).
- Diez series: BTC, ETH, SOL, XLM y XRP en 1h y 4h.
- Todos los resultados se marcan como research-only; `production_changes=false`, `production_timeframe_support_changed=false`.
- Las operaciones quedan dentro de su fold OOS y segmento continuo; no cruzan huecos ni límites de fold.

## Método y límites

Las velas 1h/4h se agregan desde M15 usando Open=primero, High=máximo, Low=mínimo, Close=último y Volume=suma. Se descartan buckets incompletos. No se rellenan huecos, interpolan precios ni agregan a través de discontinuidades.

Cada segmento continuo tiene división cronológica propia 60% train / 20% validation / 20% test. Por ello, los totales siguientes suman resultados OOS de varios segmentos y activos. **No son una simulación de cartera compartida**: no modelan capital común, correlación, concurrencia entre activos ni límites agregados de exposición. Tampoco son una muestra independiente por activo cuando se agregan segmentos.

## Resultados agregados por temporalidad

| Temporalidad | Fold | Operaciones | Resultado bruto | Media por operación |
|---|---:|---:|---:|---:|
| 1h | Validation | 423 | +24.9553R | +0.0590R |
| 1h | Test | 243 | +6.0938R | +0.0251R |
| 4h | Validation | 110 | +4.9328R | +0.0448R |
| 4h | Test | 50 | -6.0341R | -0.1207R |

El test 1h tiene resultado bruto positivo, pero muy pequeño por operación. El test 4h ya es negativo antes de costes. Los tamaños de muestra 4h (especialmente 50 operaciones de test) son insuficientes para afirmar una ventaja estable.

## Desglose por activo

### 1h

| Activo | Validation n | Validation R | Test n | Test R |
|---|---:|---:|---:|---:|
| BTC | 97 | -2.2434 | 65 | +1.7371 |
| ETH | 74 | -10.3248 | 48 | +5.3196 |
| SOL | 94 | +19.3058 | 54 | +10.9420 |
| XLM | 79 | +2.4010 | 34 | -5.4687 |
| XRP | 79 | +15.8167 | 42 | -6.4362 |

Hay reversión de comportamiento entre periodos: BTC y ETH pasan de negativos en validation a positivos en test; XLM y XRP hacen lo contrario. SOL es positivo en ambos periodos, pero no supera el análisis de costes. No se autoriza excluir activos a posteriori ni seleccionar SOL como estrategia operativa con esta misma muestra.

### 4h

| Activo | Validation n | Validation R | Test n | Test R |
|---|---:|---:|---:|---:|
| BTC | 24 | +3.7097 | 15 | +3.0058 |
| ETH | 21 | -7.5409 | 9 | +0.0125 |
| SOL | 24 | +0.0618 | 15 | -1.8072 |
| XLM | 15 | +4.5721 | 5 | -5.0000 |
| XRP | 26 | +4.1301 | 6 | -2.2452 |

La reversión en test es clara en SOL, XLM y XRP. BTC es positivo en ambos periodos, pero con solo 15 operaciones de test y sin costes completos no permite aprobar una estrategia.

## Sensibilidad de comisiones

El artefacto conserva una sensibilidad basada en la comisión cripto publicada de referencia del 1% por lado. **No representa el nivel Club real del usuario ni costes ejecutables verificados**; tampoco incluye un spread/slippage completo y verificable.

| Temporalidad | Fold | Bruto | Comisión estimada en R* | Tras comisión conocida* |
|---|---:|---:|---:|---:|
| 1h | Validation | +24.9553R | 732.2213R | -707.2661R |
| 1h | Test | +6.0938R | 478.7068R | -472.6132R |
| 4h | Validation | +4.9328R | 91.8289R | -86.8963R |
| 4h | Test | -6.0341R | 46.7517R | -52.7859R |

* Suma de sensibilidades trade-level del artefacto; no es PnL de cartera ni una factura de costes reales. Los presupuestos de riesgo hipotéticos son cálculos por operación y no una cartera con capital compartido.

En 1h test, la comisión de referencia equivale a unas 78.5 veces el resultado bruto agregado. Si se escalara linealmente solo esa comisión y se asumiera spread y deslizamiento cero, el punto de equilibrio rondaría **0.0127% por lado**. Es una sensibilidad matemática, no una tarifa disponible confirmada. En 4h el resultado bruto de test ya es negativo: reducir comisiones no bastaría para volverlo positivo.

## Decisión

1. Mantener 1h y 4h en investigación; no activar señales ni añadirlas al escáner de producción.
2. No bajar el umbral de calidad, RR, ATR o tendencia para forzar más operaciones.
3. No seleccionar SOL/BTC por resultados retrospectivos: el análisis por activo es exploratorio y el coste sigue siendo dominante o no está verificado.
4. No presentar el R agregado como rentabilidad de cartera.
5. Priorizar una línea de investigación que pueda reducir de forma estructural el coste por unidad de riesgo: instrumentos con costes verificables inferiores, horizontes realmente más largos o una estructura de entrada/salida con menor rotación. Cualquier candidata deberá probarse en una muestra futura no utilizada aquí, con coste completo y sizing compartido.
6. Conservar este artefacto como baseline reproducible; no lanzar nuevas capturas ni backfills de proveedores para completar esta auditoría.

## Integridad de ejecución

En las diez salidas:
- `network_requests = 0`
- `research_only = true`
- `production_changes = false`
- `verified_cost_adjusted_total_R = null`
- `all_trades_inside_oos_fold = true`

**Resultado final:** la infraestructura de remuestreo queda validada técnicamente; la estrategia 1h/4h no queda validada económica ni estadísticamente para producción.
