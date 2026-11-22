# Trading Bot 3.0 — audit de simplificación y prioridades

Fecha: 2026-11-22  
Estado: propuesta de arquitectura y primer diagnóstico implementado; investigación solamente. No cambia producción ni habilita señales.

## Resumen ejecutivo

El proyecto no necesita reiniciarse. Ya tiene componentes valiosos: scanner, normalización OHLCV, calendarios de mercado, indicadores, gestión de riesgo, modelo de costes, lifecycle de señales, paper ledger y gates de seguridad.

El problema es que el proceso de investigación es más complejo que la evidencia económica que produce. Hay que reducir el número de variables simultáneas y convertir la elegibilidad económica del instrumento en un filtro previo, no en un cálculo posterior al backtest.

La simplificación recomendada es desarrollar primero un MVP de **swing diario, largo, sin apalancamiento y solo en instrumentos cuya ejecución REAL y coste estén verificados**. M15 se conserva como línea de investigación separada; no se elimina ni se mezcla con el MVP. No se propone cambiar de broker ni pagar proveedores de datos ahora.

## Hallazgos concretos del repositorio y la última ejecución

1. 'config.py' mantiene 12 activos (2 ETF LSE, 5 acciones USA y 5 cripto), dos timeframes y varios escenarios de ejecución. La ejecución real/CFD sigue sin confirmación por instrumento.
2. 'data_engine.py' usa una combinación de Yahoo, Twelve Data y EODHD. La asignación de proveedor y el camino de backtest difieren del camino live: los ETF LSE diarios usan EODHD en live y Yahoo en el histórico extendido; los demás usan Twelve Data y hay rutas especiales de backtest. Esto es defendible técnicamente, pero incrementa el coste de diagnóstico y la posibilidad de diferencias entre datasets.
3. 'transaction_costs.py' ya distingue REAL/CFD, comisiones, spread, FX y financiación, y reporta componentes no cotizados. No debe sustituirse por una aproximación más simple que oculte costes.
4. 'paper_forward.py' declara expresamente que sus resultados son brutos y que los costes no están incluidos hasta verificarlos.
5. En el artefacto de la ejecución 44978022120, 'strategy_research_summary.csv' contiene 18.790 trades agregados en todas las estrategias/activos/timeframes; **0 trades tienen costes completos** y los 18.790 registran componentes sin precio. Por tanto, 'known_fee_adjusted_total_R' es una sensibilidad parcial, no un P&L neto completo. Los resultados no se pueden presentar como rentabilidad neta realista.
6. Las dos hipótesis nuevas de PR #112 no superan los criterios de aceptación y permanecen rechazadas. No se deben rescatar mediante ajuste de parámetros sobre el mismo OOS.

## Decisiones propuestas para reducir complejidad

### A. Separar dos carriles

- **MVP de validación:** swing diario, solo largos, sin apalancamiento, un calendario de mercado por instrumento y un universo reducido de instrumentos con ejecución REAL confirmada. Un solo flujo de señales y un solo ledger.
- **Laboratorio M15:** conservarlo como investigación independiente. No comparte métricas ni criterios de aceptación con swing diario y no bloquea la entrega del MVP.

Esto reduce la cantidad de combinaciones sin descartar el trabajo ya hecho.

### B. Elegibilidad económica antes de investigar estrategias

Cada activo debe superar una ficha de elegibilidad antes de generar backtests:

- instrumento exacto y listing/ISIN;
- ejecución REAL o CFD verificada en el ticket de eToro;
- mercado, horario, divisa y calendario;
- comisión de entrada y salida;
- spread bid/ask observado y rango por sesión;
- FX/conversión cuando proceda;
- financiación overnight si corresponde;
- tamaño mínimo, fraccionamiento y restricciones de orden;
- fuente y timestamp de cada dato.

Si un campo material no se conoce, el activo queda 'COSTS_UNVERIFIED' y no puede contar como candidato validado. No se sustituye un coste desconocido por cero.

### B.1. Impacto de costes con el capital de referencia de 1.000 USD

Con el límite máximo actual de riesgo, una operación al 1% representa 10 USD de riesgo bruto planificado:

- Acciones con comisión pública de 1 USD por lado: 2 USD de ida y vuelta, equivalentes al 20% de ese riesgo, antes del spread.
- Acciones con comisión de 2 USD por lado: 4 USD de ida y vuelta, equivalentes al 40% del riesgo, antes del spread.
- Con un nivel de riesgo del 0,25% (2,50 USD), esas mismas comisiones consumen entre el 80% y el 160% del riesgo planificado.
- ETF REAL: sin comisión de operación publicada, pero el market spread y cualquier cargo aplicable siguen reduciendo la expectativa.
- Cripto REAL Bronze/Silver/Gold: 1% por posición en cada lado de la operación según el esquema publicado. Por ejemplo, con stop del 5% y riesgo bruto de 10 USD, el nominal teórico sería 200 USD; una comisión de ida y vuelta del 2% del nominal sería 4 USD (40% del riesgo), antes del spread.

Estos ejemplos son sensibilidad económica, no costes confirmados de los tickets concretos. Refuerzan la prioridad de evaluar ETF REAL primero y mantener cripto fuera del MVP hasta demostrar que su expectativa bruta puede absorber las comisiones. No se debe aumentar el riesgo para compensar costes.

### C. Reducir el universo de forma controlada

No fijar ahora un nuevo universo definitivo. Evaluar primero los 2 ETF existentes y las acciones existentes, pero priorizar los instrumentos que puedan confirmarse como REAL y cuyo coste total estimado sea pequeño frente al movimiento esperado. Mantener las criptomonedas fuera del MVP de eToro hasta que su comisión por lado, spread y clasificación de ejecución demuestren viabilidad. No borrarlas del repositorio ni del histórico: se quedan en el laboratorio.

La reducción del universo debe hacerse con criterios previos y documentados, no escogiendo retrospectivamente los activos ganadores del OOS.

### D. Simplificar la política de datos

- Definir un contrato canónico OHLCV: UTC, vela cerrada, columnas y tipos uniformes, corporate actions, volumen válido, calendario y procedencia.
- Guardar snapshots inmutables con 'provider', 'symbol', 'retrieved_at', 'bar_timestamp', 'adjustment_policy' y hash.
- Usar un proveedor primario por mercado/timeframe y un segundo proveedor solo para auditoría/discrepancias o fallback explícito.
- No mezclar silenciosamente velas de Yahoo/Twelve Data/EODHD en una misma serie.
- Comparar las velas live con la fuente del backtest en una prueba de solapamiento antes de aceptar la equivalencia.
- No comprar un plan de datos todavía. Twelve Data Basic publica 8 créditos/minuto y 800 solicitudes/día. Su cobertura Basic incluye datos de EE. UU.; los mercados internacionales tienen disponibilidad limitada/de prueba según mercado y símbolo. Twelve Data documenta que el histórico diario suele remontarse al inicio del instrumento, mientras que el intradía suele abarcar desde meses hasta varios años según activo. Cada llamada de time series tiene coste por símbolo y el máximo por respuesta es 5.000 barras.
- El código actual usa Twelve Data para acciones USA y cripto, Yahoo para los ETF LSE y EODHD para el diario live de esos ETF; por tanto, no se debe cambiar el proveedor ni contratar un plan hasta comparar OHLCV en el periodo de solapamiento y confirmar disponibilidad por símbolo. La diferencia actual entre el proveedor live y el histórico extendido de CSPX/CNDX debe ser un test explícito, no una suposición.
- Referencias de datos: https://twelvedata.com/pricing ; https://twelvedata.com/stocks ; https://support.twelvedata.com/en/articles/5656039-how-to-get-historical-prices ; https://support.twelvedata.com/en/articles/5214728-getting-historical-data.

### E. Una sola cadena de validación reutilizable

Para cada hipótesis:
1. preregistro de regla y universo;
2. test unitario de señales y lookahead;
3. backtest cronológico;
4. OOS intacto;
5. walk-forward con tamaño muestral suficiente;
6. ejecución simulada con entrada siguiente vela, spread y slippage conservadores, comisiones, FX y financiación;
7. paper-forward prospectivo;
8. revisión manual antes de habilitar alertas.

Un workflow verde solo demuestra que el proceso terminó; no demuestra edge. No realizar grids de parámetros sobre el OOS.

## Qué NO cambiar

- No reiniciar el repositorio ni reescribir 'Bot.py'.
- No eliminar el trabajo de calendarios, validación de datos, normalización, costes, paper ledger o lifecycle.
- No cambiar el capital de referencia de 1.000 USD.
- Mantener máximo 1% de riesgo por operación y 3% agregado.
- No acceder a cartera, posiciones o saldo reales.
- No automatizar operaciones.
- Mantener 'STRATEGY_VALIDATED', sizing y alertas desactivados.
- No mergear estrategias de investigación a producción sin pasar gates.

## Orden de ejecución recomendado

1. Completar la matriz de ejecución y costes de los 12 instrumentos, con evidencia del ticket/mercado; empezar por los ETF y acciones existentes. No pedir datos de cartera.
2. Construir y ejecutar 'asset_economic_eligibility.py', informe automático que clasifica 'ELIGIBLE', 'INELIGIBLE' o 'COSTS_UNVERIFIED'. El comando es 'python asset_economic_eligibility.py'.
3. Crear un dataset canónico diario y una prueba de solapamiento live/backtest para el subconjunto elegible.
4. Ejecutar el MVP diario en ese subconjunto; mantener M15 y cripto en investigación separada.
5. Añadir costes completos al paper-forward antes de usar sus estadísticas para tomar decisiones.

## Referencias públicas a verificar por instrumento

- eToro España, comisiones: https://www.etoro.com/es/trading/fees/
- eToro España, spreads CFD: https://www.etoro.com/es/trading/fees/cfd-spreads/
- eToro España, financiación CFD: https://www.etoro.com/es/trading/fees/cfd-overnight-fees/
- Twelve Data, planes y límites: https://twelvedata.com/pricing
- Twelve Data, cobertura histórica: https://support.twelvedata.com/en/articles/5656039-how-to-get-historical-prices

Las tarifas públicas no sustituyen al ticket concreto de la cuenta. La página española de eToro consultada el 22-11-2026 indica:

- Acciones: comisión de 1 o 2 USD al abrir y cerrar, según residencia y mercado; el ticket muestra el coste aplicable.
- ETF: comisión de operación publicada como cero, pero sigue existiendo el market spread.
- Tanto acciones como ETF: algunas compras sin apalancamiento pueden ejecutarse como CFD por restricciones del producto. La ventana de ejecución identifica expresamente cualquier CFD.
- Cripto: comisión por niveles; Bronze/Silver/Gold pagan 1% por posición, con descuentos en niveles superiores. El spread de mercado es variable.
- CFD de acciones/ETF: 0,15% por operación de apertura/cierre; CFD cripto: 1%. Pueden añadirse costes nocturnos y spread de mercado.
- La página indica que desde el 2 de agosto de 2024 los nuevos usuarios residentes en España no pueden operar CFD. No inferir la situación de una cuenta existente: comprobar el ticket y las condiciones aplicables.
- Las cuentas USD y las cuentas locales tienen reglas de conversión distintas. La conversión de depósitos/fondos es un coste separado del FX de la operación.

Para el universo actual, los 12 activos están configurados en USD y la cuenta está confirmada en USD, por lo que no se presupone FX de operación en esos símbolos; los costes de financiación de depósitos en EUR, si existen, se mantienen separados. En los ETF LSE se debe comprobar además cualquier impuesto o cargo de mercado aplicable al instrumento concreto. No asumir que «ETF sin comisión» significa «coste total cero».

Fuente principal: https://www.etoro.com/es/trading/fees/ (consultada 22-11-2026). Fuentes complementarias: https://www.etoro.com/es/trading/fees/cfd-spreads/ y https://www.etoro.com/es/trading/fees/cfd-overnight-fees/.

## Resultado esperado

Menos combinaciones, menos llamadas redundantes, menos estrategias no viables y una sola ruta de aceptación. El objetivo no es producir señales a toda costa: es que cada señal candidata tenga una expectativa positiva demostrable después de costes completos, y que si no hay oportunidad el sistema diga NO TRADE.


## Auditoría reproducible de solapamiento Yahoo/EODHD (añadida en PR #114)

El archivo `research/etf_provider_overlap_audit.py` compara las cotizaciones diarias de Yahoo LSE (`CSPX.L`, `CNDX.L`) y EODHD LSE (`CSPX.LSE`, `CNDX.LSE`) durante la ventana de solapamiento disponible (aprox. 370 días). La comparación:

- exige datos de ambos proveedores y moneda USD;
- alinea sesiones por fecha de mercado;
- compara OHLC después de aplicar a EODHD los eventos de split, sin ajuste por dividendos;
- informa diferencias de sesiones y errores absolutos porcentuales por OHLC;
- informa por separado la relación de volúmenes, sin asumir que ambos proveedores los reportan de forma idéntica;
- genera CSV por activo y un resumen JSON como artefacto de GitHub Actions;
- no accede a eToro, cartera, órdenes ni señales, y no cambia el proveedor activo.

El workflow `.github/workflows/etf_provider_overlap_audit.yml` ejecuta la auditoría al cambiar estos archivos en una PR y también permite ejecución manual. Requiere el secreto existente `EODHD_API_KEY`; nunca imprime ni guarda la clave.

**Estado:** herramienta añadida; comparación real pendiente de ejecución y revisión del artefacto. Hasta entonces, Yahoo y EODHD no se consideran equivalentes y no se cambia el enrutamiento de datos.

### Resultados del solapamiento diario (ejecución 22-11-2026)

La auditoría descargó datos reales de EODHD, Yahoo LSE y, como contraste independiente, Yahoo SIX para las mismas clases de participación USD. Ventana común: 2025-11-24 a 2026-11-20.

**CSPX**
- 251 sesiones solapadas EODHD/Yahoo LSE.
- Apertura y cierre Yahoo LSE frente a EODHD: error mediano y p95 prácticamente cero.
- Máximos Yahoo LSE: 65/251 sesiones con desviación absoluta superior al 1%; p95 6,385%; máximo 19,594%.
- Mínimos: 1/251 sesiones por encima del 1%.
- Contraste SIX (CSSPX.SW): máximo p95 0,150%, máximo absoluto 0,353%; apertura/cierre también muy próximos a EODHD.
- Los máximos anómalos de Yahoo LSE no aparecen en el contraste SIX. Ejemplos extremos: 2026-01-06 (Yahoo LSE 888,76; EODHD 743,15; SIX 742,60), 2026-02-23 (886,57; 741,90; 740,70) y 2025-11-26 (863,38; 730,59; 730,60). Esto apunta a velas High defectuosas en Yahoo LSE, no a movimientos reales del ETF.

**CNDX**
- 246 sesiones EODHD/Yahoo LSE; Yahoo LSE y EODHD coinciden prácticamente en apertura/cierre y presentan desviaciones máximas inferiores al 0,35% en High/Low.
- El contraste SIX (CSNDX.SW) tiene diferencias mayores en High/Low (p95 1,661% y 1,243%, respectivamente); por tanto, SIX no debe utilizarse como sustituto general de CNDX LSE.

**Volumen**
- Yahoo LSE/EODHD: ratio mediano 1,071 para CSPX y 1,000 para CNDX. Los volúmenes no son intercambiables sin normalización/estudio adicional.

**Decisión técnica**
- No sustituir el proveedor general de CSPX/CNDX todavía.
- Bloquear el uso sin revisión del histórico ampliado Yahoo LSE de CSPX: la validación OHLC actual solo descarta velas incoherentes, por lo que máximos exagerados pero formalmente coherentes pueden pasar inadvertidos.
- Mantener Yahoo LSE para CNDX provisionalmente sujeto a la validación de calidad ya existente.
- Investigar una reparación selectiva de máximos CSPX con contraste SIX/EODHD, con umbral predefinido, trazabilidad por vela y pruebas unitarias; no modificar datos históricos silenciosamente.
- No cambiar producción ni aceptar estrategia alguna a partir de esta auditoría.

Artefacto reproducible: ejecución [44980363657](https://github.com/andertrading/Trading-bot/actions/runs/44980363657), artefacto etf-provider-overlap-audit. CI de tests del mismo commit: [44980363667](https://github.com/andertrading/Trading-bot/actions/runs/44980363667), correcto.


### Reparación OHLC: endurecimiento tras auditoría de proveedores

La reparación desde el listing SIX ya no compara únicamente el cierre. Para cada vela defectuosa compara contra el listing alternativo todos los campos OHLC primarios que sigan siendo estructuralmente fiables; solo excluye el campo primario que incumple las relaciones OHLC y que se pretende reparar. Si un campo fiable difiere más del 0,5%, se rechaza la reparación completa. Esto evita aceptar una vela con cierre parecido pero máximo/mínimo sustancialmente distinto.

La auditoría histórica del 22/11/2026 también confirma que el 24/10/2025 fue una sesión de negociación de LSE; la falta de vela válida de CSPX/CNDX es un hueco de proveedor, no un festivo. No se completa con interpolación ni con precios de un broker minorista. La evidencia externa de precios de Trading 212 se conserva como pista de investigación, no como feed certificado ni como dato de backtest.


### Candidato externo para investigar el hueco del 24/10/2025 (no incorporado)

La página pública de Trading 212 muestra una fila histórica para los mismos instrumentos y sus ISIN de Londres:

| Activo | Open | High | Low | Close | Fuente |
|---|---:|---:|---:|---:|---|
| CSPX | 723.06 | 727.85 | 721.88 | 727.26 | Trading 212, CSPX.GB |
| CNDX | 1445.22 | 1455.61 | 1443.60 | 1454.60 | Trading 212, CNDX.GB |

Referencias: https://www.trading212.com/trading-instruments/invest/CSPX.GB y https://www.trading212.com/es/trading-instruments/invest/CNDX.GB

Las páginas identifican London Stock Exchange y los ISIN IE00B5BMR087 / IE00B53SZB19, pero la descripción de Trading 212 indica que sus valores OHLC son un índice de precio de compra diario. No se ha verificado que sea un feed de transacciones oficiales de bolsa ni se ha evaluado la metodología de construcción de máximos/mínimos. Por ello estos valores solo sirven como pista de que existe una referencia adicional; no se cargan al histórico, no rellenan huecos y no certifican Yahoo/EODHD/SIX.
