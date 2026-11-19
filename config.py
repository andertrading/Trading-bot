
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

# Activos candidatos.
# La moneda de cuenta es USD.
ASSETS = {
    "CSPX": {
        "name": "iShares Core S&P 500",
        "yahoo": "CSPX.L",
        "type": "ETF",
        "currency": "USD",
        "entry_fee_type": "none",
        "entry_fee": 0.0,
        "enabled": True,
    },
    "CNDX": {
        "name": "iShares Nasdaq 100",
        "yahoo": "CNDX.L",
        "type": "ETF",
        "currency": "USD",
        "entry_fee_type": "none",
        "entry_fee": 0.0,
        "enabled": True,
    },
    "TSLA": {
        "name": "Tesla",
        "yahoo": "TSLA",
        "type": "STOCK",
        "currency": "USD",
        "entry_fee_type": "fixed",
        "entry_fee": 1.0,
        "enabled": True,
    },
    "NVDA": {
        "name": "Nvidia",
        "yahoo": "NVDA",
        "type": "STOCK",
        "currency": "USD",
        "entry_fee_type": "fixed",
        "entry_fee": 1.0,
        "enabled": True,
    },
    "AAPL": {
        "name": "Apple",
        "yahoo": "AAPL",
        "type": "STOCK",
        "currency": "USD",
        "entry_fee_type": "fixed",
        "entry_fee": 1.0,
        "enabled": True,
    },
    "MSFT": {
        "name": "Microsoft",
        "yahoo": "MSFT",
        "type": "STOCK",
        "currency": "USD",
        "entry_fee_type": "fixed",
        "entry_fee": 1.0,
        "enabled": True,
    },
    "TTWO": {
        "name": "Take-Two Interactive",
        "yahoo": "TTWO",
        "type": "STOCK",
        "currency": "USD",
        "entry_fee_type": "fixed",
        "entry_fee": 1.0,
        "enabled": True,
    },
    "BTC": {
        "name": "Bitcoin",
        "yahoo": "BTC-USD",
        "type": "CRYPTO",
        "currency": "USD",
        "entry_fee_type": "percentage",
        "entry_fee": 0.01,
        "enabled": True,
    },
    "ETH": {
        "name": "Ethereum",
        "yahoo": "ETH-USD",
        "type": "CRYPTO",
        "currency": "USD",
        "entry_fee_type": "percentage",
        "entry_fee": 0.01,
        "enabled": True,
    },
    "SOL": {
        "name": "Solana",
        "yahoo": "SOL-USD",
        "type": "CRYPTO",
        "currency": "USD",
        "entry_fee_type": "percentage",
        "entry_fee": 0.01,
        "enabled": True,
    },
    "XLM": {
        "name": "Stellar",
        "yahoo": "XLM-USD",
        "type": "CRYPTO",
        "currency": "USD",
        "entry_fee_type": "percentage",
        "entry_fee": 0.01,
        "enabled": True,
    },
    "XRP": {
        "name": "XRP",
        "yahoo": "XRP-USD",
        "type": "CRYPTO",
        "currency": "USD",
        "entry_fee_type": "percentage",
        "entry_fee": 0.01,
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

# Costes pendientes de confirmar.
CLOSE_FEES_CONFIRMED = False
SPREADS_CONFIRMED = False
FX_CONVERSION_CONFIRMED = False

# No calcular tamaños hasta validar precios y costes.
ALLOW_POSITION_SIZING = False

# Antigüedad máxima general de datos.
DATA_MAX_AGE_MINUTES = 30

# Credenciales: únicamente mediante variables de entorno.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
