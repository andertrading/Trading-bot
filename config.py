
import os

# Trading Bot 3.0 — configuración general

BOT_VERSION = "3.0"
TIMEZONE = "Europe/Madrid"

# Moneda única de referencia del sistema.
ACCOUNT_CURRENCY = "USD"

# Modo seguro: únicamente señales y simulación.
TRADING_MODE = "paper"

# Capital de referencia.
INITIAL_CAPITAL_USD = 1000.0

# Límites máximos de riesgo.
MAX_RISK_PER_TRADE = 0.01
MAX_PORTFOLIO_RISK = 0.03

# Riesgo provisional por nivel de calidad.
# No habilitar para operativa real sin validación.
RISK_TIERS = {
    "A": 0.0025,
    "B": 0.0050,
    "C": 0.0075,
    "D": 0.0100,
}

# Temporalidades.
INTRADAY_INTERVAL = "15m"
SWING_INTERVAL = "1d"

# Activos candidatos. Las comisiones se centralizan en transaction_costs.py.
# La moneda de cuenta es USD.
ASSETS = {
    "CSPX": {
        "name": "iShares Core S&P 500",
        "yahoo": "CSPX.L",
        "type": "ETF",
        "market_calendar": "XLON",
        "currency": "USD",
        "enabled": True,
    },
    "CNDX": {
        "name": "iShares Nasdaq 100",
        "yahoo": "CNDX.L",
        "type": "ETF",
        "currency": "USD",
        "enabled": True,
    },
    "TSLA": {
        "name": "Tesla",
        "yahoo": "TSLA",
        "type": "STOCK",
        "market_calendar": "XNYS",
        "currency": "USD",
        "enabled": True,
    },
    "NVDA": {
        "name": "Nvidia",
        "yahoo": "NVDA",
        "type": "STOCK",
        "currency": "USD",
        "enabled": True,
    },
    "AAPL": {
        "name": "Apple",
        "yahoo": "AAPL",
        "type": "STOCK",
        "currency": "USD",
        "enabled": True,
    },
    "MSFT": {
        "name": "Microsoft",
        "yahoo": "MSFT",
        "type": "STOCK",
        "currency": "USD",
        "enabled": True,
    },
    "TTWO": {
        "name": "Take-Two Interactive",
        "yahoo": "TTWO",
        "type": "STOCK",
        "currency": "USD",
        "enabled": True,
    },
    "BTC": {
        "name": "Bitcoin",
        "yahoo": "BTC-USD",
        "type": "CRYPTO",
        "market_calendar": "24/7",
        "currency": "USD",
        "enabled": True,
    },
    "ETH": {
        "name": "Ethereum",
        "yahoo": "ETH-USD",
        "type": "CRYPTO",
        "currency": "USD",
        "enabled": True,
    },
    "SOL": {
        "name": "Solana",
        "yahoo": "SOL-USD",
        "type": "CRYPTO",
        "currency": "USD",
        "enabled": True,
    },
    "XLM": {
        "name": "Stellar",
        "yahoo": "XLM-USD",
        "type": "CRYPTO",
        "currency": "USD",
        "enabled": True,
    },
    "XRP": {
        "name": "XRP",
        "yahoo": "XRP-USD",
        "type": "CRYPTO",
        "currency": "USD",
        "enabled": True,
    },
}

# Activos deshabilitados por restricciones de CFD.
DISABLED_ASSETS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "GOLD": "GC=F",
    "OIL": "CL=F",
}

# El modelo de costes eToro está centralizado en transaction_costs.py.
# Las tarifas públicas son estimaciones base; no equivalen al ticket
# personalizado de la cuenta ni confirman el tipo REAL/CFD.

# Costes pendientes de confirmar.
CLOSE_FEES_CONFIRMED = False
SPREADS_CONFIRMED = False
FX_CONVERSION_CONFIRMED = False

# No calcular tamaños hasta validar precios y costes.
ALLOW_POSITION_SIZING = False

# Modelo de costes eToro (referencias públicas; no son confirmación de tu ticket).
# Se usa REAL como escenario base para compras largas sin apalancamiento.
# La clasificación exacta debe verificarse en el ticket de cada instrumento.
ETORO_EXECUTION_TYPES = {
    asset: "REAL" for asset in ASSETS
}
ETORO_EXECUTION_TYPES_CONFIRMED = False

# Tarifas publicadas; se aplican como escenario base/conservador.
# Acciones reales: máximo del rango publicado ($1-$2 por lado).
ETORO_STOCK_REAL_COMMISSION_USD_PER_SIDE = 2.0
# Cripto real: tarifa base Bronze/Silver/Gold, sujeta al nivel Club.
ETORO_CRYPTO_REAL_FEE_PCT_PER_SIDE = 0.01
# CFD: spread de apertura/cierre por cada lado.
ETORO_STOCK_CFD_FEE_PCT_PER_SIDE = 0.0015
ETORO_ETF_CFD_FEE_PCT_PER_SIDE = 0.0015
ETORO_CRYPTO_CFD_FEE_PCT_PER_SIDE = 0.01

# Spread de mercado real de cada instrumento: no se infiere de OHLC.
# Introducir el spread completo bid-ask en puntos básicos solo tras medirlo.
ETORO_MARKET_SPREAD_BPS = {
    asset: None for asset in ASSETS
}
# Conversión de divisa de operación: solo necesaria si cotización != cuenta.
ETORO_FX_CONVERSION_FEE_PCT = None
ETORO_FUNDING_FX_CONVERSION_FEE_PCT = None
ETORO_EXECUTION_PRICE_BASIS_CONFIRMED = False
# Financiación CFD: tarifas dinámicas, deben copiarse del ticket de eToro.
ETORO_CFD_STOCK_ETF_ANNUAL_FEE_PCT = 0.064
ETORO_CFD_STOCK_ETF_BENCHMARK_RATE_PCT = None
ETORO_CFD_STOCK_SHORT_BORROW_ANNUAL_RATE_PCT = {
    asset: None for asset in ASSETS
}
ETORO_CFD_CRYPTO_OVERNIGHT_USD_PER_UNIT_DAY = {
    asset: None for asset in ASSETS
}
ETORO_FEE_SCHEDULE_CONFIRMED = False
ETORO_ACCOUNT_CURRENCY_CONFIRMED = True  # Usuario confirma cuenta eToro denominada en USD

# Antigüedad máxima general de datos.
DATA_MAX_AGE_MINUTES = 30

# Credenciales: únicamente mediante variables de entorno.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Las alertas de señales permanecen desactivadas hasta validar la estrategia.
# Activar únicamente mediante variable de entorno tras superar el gate estadístico.
SIGNAL_ALERTS_ENABLED = os.getenv(
    "SIGNAL_ALERTS_ENABLED", "false"
).strip().lower() in {"1", "true", "yes", "on"}

# Gate interno: no enviar alertas operativas hasta superar la validación
# histórica, walk-forward y de costes. Solo el código puede cambiar este valor.
STRATEGY_VALIDATED = False
