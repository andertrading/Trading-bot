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

### C. Reducir el universo de forma controlada

No fijar ahora un nuevo universo definitivo. Evaluar primero los 2 ETF existentes y las acciones existentes, pero priorizar los instrumentos que puedan confirmarse como REAL y cuyo coste total estimado sea pequeño frente al movimiento esperado. Mantener las criptomonedas fuera del MVP de eToro hasta que su comisión por lado, spread y clasificación de ejecución demuestren viabilidad. No borrarlas del repositorio ni del histórico: se quedan en el laboratorio.

La reducción del universo debe hacerse con criterios previos y documentados, no escogiendo retrospectivamente los activos ganadores del OOS.

### D. Simplificar la política de datos

- Definir un contrato canónico OHLCV: UTC, vela cerrada, columnas y tipos uniformes, corporate actions, volumen válido, calendario y procedencia.
- Guardar snapshots inmutables con 'provider', 'symbol', 'retrieved_at', 'bar_timestamp', 'adjustment_policy' y hash.
- Usar un proveedor primario por mercado/timeframe y un segundo proveedor solo para auditoría/discrepancias o fallback explícito.
- No mezclar silenciosamente velas de Yahoo/Twelve Data/EODHD en una misma serie.
- Comparar las velas live con la fuente del backtest en una prueba de solapamiento antes de aceptar la equivalencia.
- No comprar un plan de datos todavía. Twelve Data Basic publica 8 créditos/minuto y 800 solicitudes/día; el histórico intradía disponible depende del instrumento. La cobertura de los listings LSE debe confirmarse antes de diseñar el MVP alrededor de ese proveedor.

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
