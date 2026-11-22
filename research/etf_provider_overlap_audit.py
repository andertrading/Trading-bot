"""Read-only Yahoo vs EODHD daily OHLCV overlap audit for CSPX/CNDX.

Compares exact London USD ETF listings over the EODHD overlap window.
No eToro access, portfolio access, orders, or strategy calculations.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE_EODHD = "https://eodhd.com/api"
BASE_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"
ASSETS = {
    "CSPX": {"eodhd": "CSPX.LSE", "yahoo": "CSPX.L", "isin": "IE00B5BMR087"},
    "CNDX": {"eodhd": "CNDX.LSE", "yahoo": "CNDX.L", "isin": "IE00B53SZB19"},
}
OUT = Path("etf_provider_overlap")
OUT.mkdir(exist_ok=True)


def get_json(session, url, params):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response.json()


def load_eodhd(session, asset, spec, token, start, end):
    rows = get_json(session, f"{BASE_EODHD}/eod/{spec['eodhd']}", {
        "api_token": token, "from": start.isoformat(),
        "to": end.isoformat(), "period": "d", "fmt": "json",
    })
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"{asset}: EODHD histórico vacío/formato inválido")
    frame = pd.DataFrame(rows)
    required = {"date", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise RuntimeError(f"{asset}: EODHD campos incompletos")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    if frame["date"].isna().any():
        raise RuntimeError(f"{asset}: EODHD fechas inválidas")
    frame = frame.set_index("date").sort_index()
    if frame.index.has_duplicates:
        raise RuntimeError(f"{asset}: EODHD fechas duplicadas")
    for col in ("open", "high", "low", "close"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "volume" not in frame.columns:
        frame["volume"] = 0.0
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame = frame[["open", "high", "low", "close", "volume"]]
    splits = get_json(session, f"{BASE_EODHD}/splits/{spec['eodhd']}", {
        "api_token": token, "from": start.isoformat(),
        "to": end.isoformat(), "fmt": "json",
    })
    if not isinstance(splits, list):
        raise RuntimeError(f"{asset}: EODHD splits formato inválido")
    for event in splits:
        event_date = date.fromisoformat(str(event["date"]))
        numerator, denominator = str(event["split"]).split("/", 1)
        factor = float(denominator) / float(numerator)
        mask = frame.index < event_date
        frame.loc[mask, ["open", "high", "low", "close"]] *= factor
        # Volume must be expressed in current share units too.
        frame.loc[mask, "volume"] /= factor
    frame.columns = ["Open", "High", "Low", "Close", "Volume"]
    return frame


def load_yahoo(session, asset, spec, start, end):
    payload = get_json(session, f"{BASE_YAHOO}/{spec['yahoo']}", {
        "period1": int(pd.Timestamp(start, tz="UTC").timestamp()),
        "period2": int(pd.Timestamp(end + timedelta(days=1), tz="UTC").timestamp()),
        "interval": "1d", "events": "history", "includeAdjustedClose": "true",
    })
    chart = payload.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        raise RuntimeError(f"{asset}: Yahoo sin resultado válido")
    result = chart["result"][0]
    meta = result.get("meta", {})
    if str(meta.get("currency", "")).upper() != "USD":
        raise RuntimeError(f"{asset}: Yahoo moneda inesperada")
    exchange = str(meta.get("fullExchangeName") or meta.get("exchangeName") or "")
    if not exchange or not any(
        marker in exchange.lower() for marker in ("london", "lse")
    ):
        raise RuntimeError(f"{asset}: Yahoo no confirma listing LSE ({exchange or 'sin mercado'})")
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    timestamps = result.get("timestamp") or []
    if not timestamps:
        raise RuntimeError(f"{asset}: Yahoo histórico vacío")
    frame = pd.DataFrame({
        "Open": quote.get("open", []), "High": quote.get("high", []),
        "Low": quote.get("low", []), "Close": quote.get("close", []),
        "Volume": quote.get("volume", []),
    }, index=pd.to_datetime(timestamps, unit="s", utc=True).date)
    frame.index.name = "date"
    frame = frame.loc[(frame.index >= start) & (frame.index <= end)].sort_index()
    if frame.index.has_duplicates:
        raise RuntimeError(f"{asset}: Yahoo fechas duplicadas")
    for col in frame.columns:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame, exchange


def compare(asset, eod, yahoo, exchange):
    joined = eod.add_suffix("_eodhd").join(yahoo.add_suffix("_yahoo"), how="inner")
    joined = joined.dropna(subset=[
        "Open_eodhd", "High_eodhd", "Low_eodhd", "Close_eodhd",
        "Open_yahoo", "High_yahoo", "Low_yahoo", "Close_yahoo",
    ])
    if len(joined) < 200:
        raise RuntimeError(f"{asset}: solapamiento insuficiente ({len(joined)} sesiones)")
    metrics = {"asset": asset, "yahoo_exchange": exchange, "overlap_sessions": len(joined)}
    price_errors = {}
    for col in ("Open", "High", "Low", "Close"):
        pct = (joined[f"{col}_yahoo"] / joined[f"{col}_eodhd"] - 1.0).abs() * 100
        price_errors[col] = {
            "median_abs_pct": float(pct.median()),
            "p95_abs_pct": float(pct.quantile(0.95)),
            "max_abs_pct": float(pct.max()),
        }
    volume = joined[["Volume_eodhd", "Volume_yahoo"]].dropna()
    positive = volume[(volume > 0).all(axis=1)]
    volume_ratio = (
        positive["Volume_yahoo"] / positive["Volume_eodhd"]
        if not positive.empty else pd.Series(dtype=float)
    )
    metrics.update({
        "first_date": str(joined.index.min()),
        "last_date": str(joined.index.max()),
        "eodhd_only_sessions": int(len(eod.index.difference(yahoo.index))),
        "yahoo_only_sessions": int(len(yahoo.index.difference(eod.index))),
        "price_abs_pct_error": price_errors,
        "volume_positive_overlap_sessions": int(len(positive)),
        "volume_ratio_yahoo_to_eodhd_median": (
            float(volume_ratio.median()) if not volume_ratio.empty else None
        ),
        "volume_ratio_yahoo_to_eodhd_p95": (
            float(volume_ratio.quantile(0.95)) if not volume_ratio.empty else None
        ),
    })
    joined.to_csv(OUT / f"{asset.lower()}_overlap.csv", index_label="date")
    return metrics


def main():
    token = os.environ.get("EODHD_API_KEY", "").strip()
    if not token:
        raise SystemExit("FALTA EODHD_API_KEY; no se ha realizado ninguna petición")
    session = requests.Session()
    session.headers.update({"User-Agent": "TradingBot3.0-ProviderOverlapAudit/1.0"})
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=370)
    results, errors = [], []
    print("AUDITORÍA OHLCV YAHOO vs EODHD | CSPX/CNDX | SOLO LECTURA")
    print(f"Ventana solicitada: {start} -> {end}")
    print("Comparación: OHLC split-adjusted, sin ajuste por dividendos; volumen separado.")
    for asset, spec in ASSETS.items():
        try:
            eod = load_eodhd(session, asset, spec, token, start, end)
            yahoo, exchange = load_yahoo(session, asset, spec, start, end)
            result = compare(asset, eod, yahoo, exchange)
            results.append(result)
            print(json.dumps(result, sort_keys=True))
        except Exception as exc:
            errors.append(f"{asset}: {type(exc).__name__}: {exc}")
            print(f"ERROR {asset}: {type(exc).__name__}: {exc}")
    report = {
        "scope": "read_only_yahoo_eodhd_daily_overlap",
        "eToro_account_or_portfolio_accessed": False,
        "orders_or_signals_enabled": False,
        "requested_start": str(start),
        "requested_end": str(end),
        "results": results,
        "errors": errors,
        "interpretation": "Diagnostic only; does not authorize provider replacement.",
    }
    (OUT / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if errors or len(results) != 2:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
