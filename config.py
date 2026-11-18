
import os

# Trading Bot 3.0 — configuración general

BOT_VERSION = "3.0"
TIMEZONE = "Europe/Madrid"

# Modo seguro: únicamente señales y simulación.
TRADING_MODE = "paper"

# Capital de referencia para pruebas.
INITIAL_CAPITAL_EUR = 1000.0

# Gestión del riesgo: límites máximos.
MAX_RISK_PER_TRADE = 0.01
MAX_PORTFOLIO_RISK = 0.03

# Riesgo provisional por nivel de calidad.
# No se habilitará riesgo real sin validación estadística.
RISK_TIERS = {
    "A": 0.0025,
    "B": 0.0050,
    "C": 0.0075,
    "D": 0.0100,
}

# Temporalidades de las estrategias.
INTRADAY_INTERVAL = "15m"
SWING_INTERVAL = "1d"

# Activos candidatos.
# La disponibilidad definitiva debe comprobarse en eToro.
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

# Activos originales no habilitados por las restricciones CFD.
# Se conservan como referencia, pero no generan señales.
DISABLED_ASSETS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "GOLD": "GC=F",
    "OIL": "CL=F",
}

# Costes todavía pendientes de confirmar.
# No se debe asumir que la comisión de cierre está verificada.
CLOSE_FEES_CONFIRMED = False
SPREADS_CONFIRMED = False
FX_CONVERSION_CONFIRMED = False

# Sin conversión de divisa verificada no se calcula tamaño operativo.
ALLOW_POSITION_SIZING = False

# Datos y alertas.
DATA_MAX_AGE_MINUTES = 30
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
