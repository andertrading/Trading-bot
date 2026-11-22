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

El workflow `.github/workflows/etf_provider_overlap_audit.yml` se ejecuta manualmente (`workflow_dispatch`) y serializa las ejecuciones para no consumir innecesariamente la cuota del proveedor. Los tests unitarios del script sí se ejecutan automáticamente en CI. Requiere el secreto existente `EODHD_API_KEY`; nunca imprime ni guarda la clave.

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


## Integración del registro económico con el auditor

El registro individual es la fuente de verdad para los activos configurados. `asset_economic_eligibility.py` conserva su interfaz y el formato del informe existente, pero delega la clasificación normal de cada activo configurado en `asset_economic_registry.py`. Los argumentos explícitos de prueba mantienen una ruta aislada para no romper los tests unitarios.

Los bloqueos del registro se traducen al vocabulario histórico del informe para preservar compatibilidad. La integración no conecta el registro con sizing, señales, alertas, paper ledger ni ejecución.


### Precio histórico frente a precio ejecutable

La ficha ahora exige tres elementos explícitos antes de confirmar la base de precios: tratamiento del histórico (ajustes por splits/dividendos), base del precio ejecutable del broker (bid/ask o precio aplicable) y regla que conecta la señal histórica con la entrada realista. No basta con adjuntar una fuente o marcar una casilla de confirmación. Los 12 activos quedan sin esos campos hasta disponer de evidencia específica.


### Coste de operación verificado desde el registro

Se añade `estimate_verified_registry_trade_costs` como ruta de cálculo para el MVP REAL. Solo calcula y declara completo el coste cuando la clasificación del registro es `ELIGIBLE`; utiliza comisión de entrada y salida por separado (fija, porcentual en puntos porcentuales o cero confirmado), spread bid-ask y FX cuando corresponda. Una ficha incompleta devuelve sus bloqueos y no transforma valores desconocidos en cero.

El cálculo anterior `estimate_etoro_trade_costs` se conserva como escenario de tarifas públicas estimadas y no puede certificar la elegibilidad del instrumento. Ambos resultados quedan separados para evitar confundir una simulación conservadora con costes confirmados del ticket.


La ruta verificada expresa todos los componentes en moneda de cuenta: las comisiones porcentuales y el spread se convierten desde la moneda de cotización mediante el tipo FX registrado; las comisiones fijas en USD ya están en moneda de cuenta. La comisión de conversión se calcula por separado sobre el nominal de entrada y salida. Si falta cualquiera de esos datos, no devuelve un total numérico.


La comisión de conversión FX del registro se expresa también en puntos porcentuales mediante `conversion_fee_unit="PERCENT"`; por ejemplo, `0.2` significa `0.2%`, no `20%` ni una fracción decimal de `0.2`.


### Conexión con el backtest y el gate de candidatas

El backtest conserva el ajuste parcial por tarifas públicas como sensibilidad, pero el campo `costs_complete` y las métricas netas verificadas dependen exclusivamente de `estimate_verified_registry_trade_costs`. Añade columnas separadas para coste completo verificado, P&L neto y resultado R neto; si el registro no está completo, esos campos permanecen vacíos y no entran en métricas netas.

El gate de candidatas de `Bot.py` consulta además `asset_economic_eligibility` directamente. Las banderas globales de confirmación ya no pueden convertir un activo `COSTS_UNVERIFIED` en candidato aprobado. La comprobación de coste mostrada al usuario también usa el registro. La aprobación sigue siendo manual y no habilita ejecución automática.


### Control de cuota de proveedores externos

El audit de mercado queda manual y serializado: cada ejecución puede consumir llamadas de EODHD, splits y Yahoo. En los últimos intentos EODHD respondió HTTP 402 incluso con una ventana de aproximadamente un año. No se ha determinado si se trata de cuota agotada, permiso del plan o una restricción temporal; no se contratará un plan ni se harán reintentos automáticos. La última ejecución disponible con éxito del smoke de EODHD había devuelto 252 sesiones por activo; los intentos posteriores con 402 no certifican el estado actual del proveedor.


### Segunda línea de investigación para el hueco LSE: cotización GBX del mismo ISIN

Se ha identificado una segunda línea de cotización en GBX para ambos fondos en la propia LSE: CSP1 para el ISIN IE00B5BMR087 y CNX1 para el ISIN IE00B53SZB19. La LSE confirma los ISIN y el mercado XLON; JustETF lista CSP1/CNX1 en GBX y CSPX/CNDX en USD.

Para 24/10/2025, una referencia pública de GBP/USD de 1.3306 permite comparar las filas de Trading 212 en GBX y USD. Tras convertir GBX a USD (`precio_GBX / 100 * GBPUSD`), las diferencias de Open/High/Low/Close quedan por debajo de 0.2% en ambos activos. Para CSPX, ShareCompare publica además OHLC de CSP1 en GBX ese día y su conversión queda también por debajo de 0.2% frente a la referencia USD de Trading 212. Para CNDX, ambas líneas de esa comparación proceden de Trading 212, por lo que no constituyen una verificación independiente.

Esto convierte `CSP1.L` y `CNX1.L` en candidatos razonables para una auditoría adicional de cinco años, no en fuentes aprobadas. La conversión diaria FX puede introducir diferencias intradía; hay que probar cobertura, ajustes por splits, integridad OHLC y desviación por campo durante todo el periodo. No se han añadido estos tickers al proveedor de producción ni se han rellenado velas.

Referencias:
- LSE CSP1 / ISIN IE00B5BMR087: https://www.londonstockexchange.com/en/quote/etps/CSP1/ishares-vii-plc
- LSE CNX1 / ISIN IE00B53SZB19: https://www.londonstockexchange.com/en/quote/etps/CNX1/ishares-vii-plc
- Listados por moneda e ISIN: https://www.justetf.com/uk/etf-profile.html?isin=IE00B5BMR087 y https://www.justetf.com/uk/etf-profile.html?isin=IE00B53SZB19
- Histórico CSP1 GBX 24/10/2025: https://www.sharecompare.co.uk/shares/CSP1.L/history/
- Referencias USD/GBX de Trading 212: https://www.trading212.com/trading-instruments/invest/CSPX.GB, https://www.trading212.com/es/trading-instruments/invest/CNDX.GB y https://www.trading212.com/es/trading-instruments/invest/CNX1.GB
- GBP/USD 24/10/2025: https://www.exchange-rates.org/exchange-rate-history/gbp-usd-2025-10-24


### Resultado del audit GBX de cinco años (GitHub Actions run #610)

El audit de Yahoo Finance descargó 1.259 sesiones comunes CSPX/CSP1 y 1.254 CNDX/CNX1 entre 23/11/2021 y 20/11/2026. El contraste falla ampliamente:
- CSPX: p95 máximo entre campos 8.4818% (High), desviación máxima 97.5733% (Low); 2 velas USD y 24 GBX no pasan integridad.
- CNDX: p95 máximo 1.1668% (High), desviación máxima 9.3154% (Low); 9 velas USD y 52 GBX no pasan integridad. En 05/05/2023 el mínimo GBX convertido difiere 5.8056% del campo LSE USD que se considera fiable.
- 24/10/2025 no tiene barra ni en la línea USD ni en la GBX de ninguno de los dos fondos.

Resultado: `provider_repair_approved=false`. Se descartan CSP1.L/CNX1.L como sustitutos automáticos en el estado actual. El informe JSON/CSV queda adjunto al workflow para auditoría, sin modificar datos de producción.


### Estado de validación del código integrado

En el run #619 de GitHub Actions, la suite de raíz pasó 16/16 y la suite `tests/` pasó 660/660; el arranque de `Bot.py` y las rutas `/` y `/health` también pasaron. El workflow de auditoría GBX queda manual para evitar consultas externas en cada actualización de la PR.


### Validación de datos antes de aprobar una candidata

El gate de `Bot.py` exige que la candidata lleve `data_valid=True`, un timestamp ISO-8601 con zona horaria no futura y una antigüedad numérica no negativa. La rutina `analyze_asset` ya comprueba frescura y ausencia de datos futuros; el gate vuelve a exigir la evidencia para impedir que una candidata construida por otra ruta o una prueba manual omita la validación de mercado.


## Resultado de investigación de estrategias y costes (artefacto run #110)

Se recuperó el artefacto ya existente del run de investigación 44977107159; no se volvieron a descargar históricos para este análisis. El artefacto confirma 10/10 series diarias de acciones/cripto con aproximadamente cinco años de datos y 24/24 series auditadas sin huecos. Esta evidencia procede de una rama de investigación separada y no equivale a aprobación de datos/costes para producción.

En la muestra completa de investigación diaria:
- Baseline: 224 operaciones, +27.25 R brutos.
- Donchian breakout: 138 operaciones, +23.49 R brutos.
- EMA reclaim: 89 operaciones, +9.73 R brutos.
- Trend pullback: 24 operaciones, -0.32 R brutos.

Estos resultados son exploratorios, no una validación de rentabilidad. En el portfolio OOS de los 10 activos core, el escenario de eToro de $1 por lado en acciones y 1% por lado en cripto, incluso con spread añadido igual a cero, dio resultados netos negativos:
- Baseline: +8.10% bruto frente a -14.97% neto (127 operaciones).
- Donchian: +1.89% bruto frente a -13.39% neto (79 operaciones).
- EMA reclaim: +2.44% bruto frente a -8.68% neto (58 operaciones).
- Trend pullback: +1.29% bruto frente a -2.07% neto (17 operaciones).

La muestra por activo y fold no alcanza el umbral interno de 100 operaciones; ninguna estrategia diaria queda estadísticamente aprobada. Los escenarios son estimaciones públicas, no costes verificados del ticket. Además, el modelo de costes verificado del registro sigue incompleto para los 12 activos. Conclusión operativa: no activar ninguna estrategia; el cuello de botella no es solo el capital, sino la combinación de edge bruto débil, comisión fija por operación y coste porcentual de cripto.

### Referencia pública de tarifas eToro para España (no sustituye el ticket)

La tabla oficial de costes de eToro, actualización enero de 2026, incluye España en la lista de países seleccionados. Para acciones individuales distintas de AUD/HKD/AED, el ejemplo publicado aplica $1 al abrir y $1 al cerrar; para ETFs la comisión es cero. La página oficial de tarifas confirma que el importe de acciones depende de residencia y bolsa, no se aplica a ETFs ni a CFDs, y que el spread de mercado sigue existiendo.

En cripto, la página oficial publica un 1% por posición para los niveles Bronze/Silver/Gold, con descuentos para niveles superiores; el coste se aplica en compra y venta. El nivel real del cliente y el coste que muestra el ticket deben confirmarse antes de rellenar el registro económico. No se cambia ningún campo de coste verificado ni se marca ningún activo como elegible con esta evidencia pública.

Fuentes oficiales:
- eToro Fees: https://www.etoro.com/trading/fees/
- eToro Cost & Charges examples (January 2026 update): https://www.etoro.com/wp-content/uploads/2025/07/Cost-and-Charges-examples-table-Crypto-Fees-in-May-2025.pdf


### Impacto de la comisión sobre el presupuesto de riesgo de 1.000 USD

En el artefacto recuperado, las operaciones diarias de acciones usan un presupuesto hipotético de riesgo de 5 USD por operación. Con la tarifa pública española de 1 USD al abrir y 1 USD al cerrar, la comisión fija de ida y vuelta es 2 USD: **0.40 R por operación**, antes del spread. Es decir, consume el 40% del riesgo presupuestado de 5 USD.

Para cripto, el mismo artefacto estima que la comisión del 1% por lado representa una mediana aproximada de 0.28–0.43 R por operación según el activo (BTC, ETH, SOL, XLM y XRP), también antes de añadir spread. Son estimaciones derivadas de los tamaños hipotéticos de las operaciones, no un cálculo de costes verificado por ticket.

Esto demuestra que subir el capital reduce el peso relativo de la comisión fija de acciones, pero no elimina el coste porcentual de cripto ni crea por sí solo una ventaja estadística. No se debe aumentar el riesgo por operación para compensar comisiones: el límite absoluto del 1% permanece intacto.


### Sensibilidad analítica al capital (no es un nuevo backtest)

Se hizo una sensibilidad aislada sobre los costes por operación del artefacto: se mantuvieron los resultados brutos y las comisiones porcentuales de cripto, mientras que la comisión fija de acciones se escaló inversamente con el capital (suponiendo que el nominal y el presupuesto de riesgo crecen proporcionalmente). No se cambió el riesgo porcentual ni se incluyó spread adicional.

- Donchian diario: neto estimado -30.25 R con 1.000 USD; -9.61 R con 2.500; -2.73 R con 5.000; +0.71 R con 10.000; +2.43 R con 20.000.
- EMA reclaim diario: neto estimado -25.33 R con 1.000 USD y sigue en -5.19 R con 20.000 USD.
- Baseline diario: neto estimado -59.84 R con 1.000 USD y sigue en -7.40 R con 20.000 USD.

La sensibilidad no incluye spread, no simula el efecto de la trayectoria de equity sobre el tamaño de las posiciones y no constituye una validación de rentabilidad. Solo demuestra que aumentar capital reduce el peso de la comisión fija de acciones, pero no arregla por sí solo una estrategia con ventaja bruta insuficiente; no se recomienda aumentar el capital de referencia ni el riesgo para compensarlo.


### Identidad de instrumentos Twelve Data

El gate de metadata exige símbolo y moneda compatibles, tipo de activo correcto, bolsa y tipo presentes, y zona horaria coherente con el calendario configurado para acciones/ETF. El MIC es obligatorio para acciones y ETF, pero no para cripto: Twelve Data documenta MIC para valores negociables y no lo presenta como identificador aplicable a monedas digitales. La validación de tipo usa `Common Stock`, `ETF` y `Digital Currency`.

Para cinco acciones USA y dos ETF LSE, la bolsa y el MIC se comparan con valores esperados independientes por símbolo y se envían también como filtros a Twelve Data. La identidad de esos siete listados está respaldada por las bolsas oficiales; aún falta confirmar una respuesta real del proveedor con esos metadatos. Para cripto se valida el tipo y la presencia de exchange, pero no se fuerza un venue único ni un MIC.

Fuente de esquema de metadata Twelve Data: https://twelvedata.com/docs/ai/ai-data-assistant


Para las cinco acciones estadounidenses configuradas (TSLA, NVDA, AAPL, MSFT y TTWO), las páginas de cotización oficiales de Nasdaq confirman que son Nasdaq-listed. Se han fijado en `config.py` los valores esperados `twelvedata_exchange="NASDAQ"` y `twelvedata_mic_code="XNAS"`; el ejemplo oficial de Twelve Data para AAPL muestra precisamente `exchange=NASDAQ` y `mic_code=XNAS`. El gate ahora rechaza discrepancias exactas en esos dos campos. El valor `market_calendar="XNYS"` se mantiene únicamente como calendario de sesiones estadounidense usado por el motor, no como afirmación de que el listado esté en NYSE.

Fuentes de listing:
- TSLA: https://www.nasdaq.com/market-activity/stocks/tsla
- NVDA: https://www.nasdaq.com/market-activity/stocks/nvda/real-time
- AAPL: https://www.nasdaq.com/market-activity/stocks/aapl/latest-real-time-trades
- MSFT: https://www.nasdaq.com/market-activity/stocks/msft/real-time
- TTWO: https://www.nasdaq.com/market-activity/stocks/ttwo/real-time
- Metadata de ejemplo Twelve Data (AAPL / NASDAQ / XNAS): https://twelvedata.com/docs/ai/ai-data-assistant

Para CSPX y CNDX se han fijado `twelvedata_exchange="London Stock Exchange"` y `twelvedata_mic_code="XLON"` a partir de las fichas oficiales de LSE, que confirman ticker, moneda USD, ISIN y MIC. La API se consulta con esos filtros y el gate rechaza cualquier discrepancia. No se fuerza un MIC a las criptomonedas.


### Ventaja bruta frente al coste por operación

En la muestra diaria completa del mismo artefacto, la ventaja bruta media por operación queda por debajo del coste modelado en ambas clases:
- Acciones USA: la comisión de ida y vuelta equivale a 0.40 R. Expectativa bruta observada: baseline +0.203 R/trade, Donchian +0.175 R/trade y EMA reclaim +0.126 R/trade.
- Cripto: el coste porcentual modelado equivale a una media de 0.371–0.385 R/trade. Expectativa bruta observada: baseline -0.008 R/trade, Donchian +0.162 R/trade y EMA reclaim +0.085 R/trade.

La comparación es previa a spread y usa hipótesis públicas de eToro, no tickets verificados. Indica que las estrategias actuales no generan suficiente ventaja bruta para absorber los costes; no basta con cambiar el capital ni con ampliar el número de operaciones. La siguiente investigación debe buscar una mejora de expectancy neta con validación fuera de muestra, no aumentar frecuencia ni relajar los filtros.


## Simplificación operativa: MVP diario y M15 manual de laboratorio

El escaneo por defecto de `Bot.run_scan()` analiza ahora solo `1d`. El workflow programado elimina el cron M15 de cada 15 minutos y conserva únicamente el escaneo diario. M15 sigue disponible mediante una ejecución manual explícita del workflow para investigación/paper; no se elimina su código ni su soporte de pruebas.

Esto reduce a la mitad los análisis del escaneo por defecto y elimina las llamadas recurrentes M15, sin alterar estrategias, reglas de riesgo ni el modo PAPER_ONLY. CI #635 pasó en el head final de esta modificación, incluyendo tests completos y arranque del bot.


La descarga de Twelve Data también envía ahora `exchange=NASDAQ` y `mic_code=XNAS` para esos cinco activos, además de comprobar que la respuesta coincide. Así se evita depender únicamente de la resolución por ticker. La documentación de `/time_series` admite ambos filtros. La prueba unitaria verifica los parámetros enviados y CI #637 pasó en el head `00ca39fe49b93d7e00c410685d03efbf7f41d85e`.


Fuentes de identidad de los ETF:
- CSPX, ISIN IE00B5BMR087, moneda USD, MIC XLON: https://www.londonstockexchange.com/en/quote/etps/CSPX/ishares-vii-plc
- CNDX, ISIN IE00B53SZB19, moneda USD, MIC XLON: https://www.londonstockexchange.com/en/quote/etps/CNDX/ishares-vii-plc
- Twelve Data lista London Stock Exchange con MIC XLON: https://twelvedata.com/exchanges?country=United+Kingdom
- Para los siete valores con identidad fijada, Twelve Data recibe filtros `exchange` y `mic_code` y la respuesta debe coincidir. Los tests son unitarios con metadata simulada; no se ha gastado cuota en una consulta real de confirmación.


### Correspondencia entre baseline de investigación y estrategia de producción

El evaluador `baseline` del run #110 llama directamente a `strategies.evaluate_signal_at`, es decir, al mismo evaluador que utiliza el bot de producción. En la investigación se fija la puntuación en 85 para igualar el tramo de riesgo entre hipótesis; por tanto, la señal es la misma, pero el dimensionamiento no reproduce la puntuación variable de producción.

El baseline diario produjo 224 operaciones y +27.25 R brutos en la muestra completa. En el portfolio OOS core (10 activos) produjo 127 operaciones y +8.10% bruto, pero -14.97% neto bajo el escenario eToro modelado, incluso con spread adicional cero. Esto hace que el resultado sea directamente relevante para la estrategia actual, aunque sigue siendo evidencia exploratoria y no validación de ejecución real.


### Sensibilidad híbrida eToro + Bitvavo (sin recomendar un segundo broker)

El artefacto #110 también comparó mantener acciones en eToro y ejecutar cripto en Bitvavo, con supuestos de comisión por lado de 0.05% para BTC/ETH/SOL/XRP en pares USDC y 0.15% maker / 0.25% taker para XLM/EUR. La documentación pública de Bitvavo confirma que los pares USDC tienen comisiones reducidas de hasta 0.05% y que las tarifas dependen del tipo de orden y del volumen de 30 días; no se ha confirmado el nivel real de un usuario.

Incluso bajo ese escenario híbrido y con spread adicional igual a cero, el portfolio OOS core continuó negativo:
- Baseline: -9.94% neto, frente a -14.97% con el escenario eToro cripto 1%.
- Donchian: -8.84%, frente a -13.39%.
- EMA reclaim: -5.46%, frente a -8.68%.
- Trend pullback: -1.18%, frente a -2.07%.

La mejora de costes es visible, pero no compensa la falta de ventaja neta. Añadir un segundo broker aumentaría la complejidad operativa y de divisa sin resolver el problema de rentabilidad; se descarta como cambio del MVP por ahora. Las tasas del escenario son hipótesis, no costes verificados.

Fuentes públicas Bitvavo:
- Comisiones por tipo de orden y volumen: https://support.bitvavo.com/hc/es/articles/4405175148689--Cu%C3%A1l-es-la-comisi%C3%B3n-de-trading-cuando-compro-o-vendo-criptomonedas
- Pares USDC con comisiones de 0.05% o inferiores: https://bitvavo.com/en/news/usdc-pairs


### Position sizing remains deliberately blocked

The production validation gate requires a complete `sizing_result` and independently confirmed portfolio risk before a candidate can become an approved manual signal. `ALLOW_POSITION_SIZING` remains disabled, and `Bot.run_scan()` does not invent a size when the registry is incomplete or portfolio exposure is unknown. This is intentional: the current registry has no economically eligible assets, and the bot must not infer the user's real open positions.

The next sizing implementation must be a clearly labelled virtual/paper calculation using the 1,000 USD reference capital and fully evidenced round-trip costs. It must remain separate from the user's real account and must not mark a candidate as tradable until the account-specific execution and portfolio-risk requirements are satisfied.


Nota de interpretación del modelo de costes: `ETORO_STOCK_REAL_COMMISSION_USD_PER_SIDE=2.0` sigue siendo el escenario superior conservador usado por el estimador genérico. La sensibilidad específica de España usa 1 USD por lado según la tabla pública. Ninguno de los dos valores sustituye la confirmación de residencia contractual y ticket; el registro económico continúa fail-closed.


## Corrección crítica: ajuste de splits solicitado no equivale a verificación independiente

Se corrigió un falso positivo en los metadatos. `_attach_market_data_metadata()` marcaba `price_adjustment_verified=True` de forma incondicional, aunque para Twelve Data diario solo se había enviado `adjust=splits` y no se habían reconciliado las velas con factores de splits independientes.

Ahora:
- Twelve Data diario queda como `price_adjustment_verified=False` y estado `REQUESTED_NOT_INDEPENDENTLY_VERIFIED`.
- El modo intradía `adjust=none` conserva el estado documentado `DOCUMENTED_RAW_MODE`.
- El backtest rechaza snapshots e históricos diarios de Twelve Data hasta que exista el estado explícito `INDEPENDENTLY_VERIFIED`.
- El gate del bot bloquea candidatas diarias de Twelve Data mientras falte esa verificación independiente.
- Los snapshots antiguos con el flag heredado `True` pero sin evidencia independiente también se rechazan.

Consecuencia para la investigación anterior: el run #110 sigue aportando evidencia de cobertura y ausencia de huecos en las series, pero su campo heredado `price_adjustment_verified=True` era un falso positivo del wrapper y no demuestra que los factores históricos estén bien aplicados. Sus resultados de estrategia se mantienen como exploratorios, no como validación de datos para producción. No se descargaron datos nuevos ni se consumieron créditos para esta corrección.

CI #646 pasó en el head `62d098ac40f0607c1379038dc36b7c92c2ba1189`.


La regresión también está cubierta: un test construye un snapshot legado con `price_adjustment_verified=True` pero sin estado de verificación independiente y confirma que el loader lo rechaza. CI #648 pasó con esta prueba incluida.


### Supresión de llamadas diarias mientras falte evidencia

El escáner diario omite los diez activos cuya fuente diaria es Twelve Data antes de llamar al proveedor, mientras no exista evidencia independiente de ajuste por splits. Así evita descargar históricos que el propio gate acabaría rechazando. La ruta manual M15 sigue disponible y continúa solicitando datos intradía en modo raw documentado.

Para desbloquear un activo en el futuro no basta un booleano: `twelvedata_daily_adjustment_verification` debe contener estado `INDEPENDENTLY_VERIFIED`, una fuente HTTPS y un `verified_at` ISO-8601 con zona horaria que no esté en el futuro. La misma evidencia se adjunta a los metadatos del histórico y se valida al cargar snapshots. CI #653 pasó con estas comprobaciones.


### Auditoría independiente preparada para los splits de TSLA y NVDA

Se ha añadido un auditor manual que compara las velas OHLC de Twelve Data con Yahoo Finance alrededor de los dos eventos que afectan al histórico de cinco años: TSLA 3:1 (25/08/2022) y NVDA 10:1 (primer día de cotización ajustada 10/06/2024). Comprueba también el evento y ratio de split de Yahoo frente a los comunicados oficiales de los emisores.

- Dos solicitudes `time_series` de Twelve Data, una por símbolo; cero llamadas al endpoint `/splits` de 20 créditos.
- Comparación de los cuatro campos OHLC en las sesiones comunes, tolerancia máxima 0,75%, y comprobación de continuidad del cierre en el límite del split.
- Workflow exclusivamente manual con confirmación booleana explícita de posible consumo de créditos.
- El informe propone la evidencia por activo, pero no modifica automáticamente `config.py`. No se ha ejecutado; no se han realizado llamadas ni consumido créditos.

Fuentes oficiales:
- TSLA: https://ir.tesla.com/press-release/tesla-announces-three-one-stock-split
- NVDA: https://investor.nvidia.com/files/doc_downloads/2024/05/NVIDIA-2024-Stock-Split-FAQ.pdf
