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
    "CSPX": {"eodhd": "CSPX.LSE", "yahoo": "CSPX.L", "alternate": "CSSPX.SW", "isin": "IE00B5BMR087"},
    "CNDX": {"eodhd": "CNDX.LSE", "yahoo": "CNDX.L", "alternate": "CSNDX.SW", "isin": "IE00B53SZB19"},
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


def load_yahoo(session, asset, spec, start, end, symbol_key="yahoo", expected_market="london"):
    symbol = spec[symbol_key]
    payload = get_json(session, f"{BASE_YAHOO}/{symbol}", {
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
    markers = ("london", "lse") if expected_market == "london" else ("swiss", "six")
    if not exchange or not any(marker in exchange.lower() for marker in markers):
        raise RuntimeError(
            f"{asset}: Yahoo no confirma mercado {expected_market} "
            f"para {symbol} ({exchange or 'sin mercado'})"
        )
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


def ohlc_integrity(frame):
    """Count structurally invalid OHLC rows without silently dropping them."""
    required = ["Open", "High", "Low", "Close"]
    if frame is None or frame.empty or any(col not in frame.columns for col in required):
        return {"rows": 0 if frame is None else int(len(frame)), "invalid_rows": None, "invalid_dates": []}
    values = frame[required].apply(pd.to_numeric, errors="coerce")
    finite = np.isfinite(values.to_numpy(dtype=float)).all(axis=1)
    positive = (values > 0).all(axis=1)
    coherent = (
        (values["High"] >= values["Low"])
        & (values["High"] >= values["Open"])
        & (values["High"] >= values["Close"])
        & (values["Low"] <= values["Open"])
        & (values["Low"] <= values["Close"])
    )
    invalid = ~(finite & positive & coherent)
    return {
        "rows": int(len(frame)),
        "invalid_rows": int(invalid.sum()),
        "invalid_dates": [str(day) for day in frame.index[invalid]],
    }


def _price_error_metrics(left, right, suffix_left, suffix_right):
    joined = (
        left.add_suffix(suffix_left)
        .join(right.add_suffix(suffix_right), how="inner")
    )
    output = {}
    for col in ("Open", "High", "Low", "Close"):
        a = pd.to_numeric(joined[f"{col}{suffix_left}"], errors="coerce")
        b = pd.to_numeric(joined[f"{col}{suffix_right}"], errors="coerce")
        valid = a.notna() & b.notna() & (a > 0) & (b > 0)
        pct = ((a[valid] / b[valid]) - 1.0).abs() * 100
        output[col] = {
            "observations": int(len(pct)),
            "median_abs_pct": float(pct.median()) if not pct.empty else None,
            "p95_abs_pct": float(pct.quantile(0.95)) if not pct.empty else None,
            "max_abs_pct": float(pct.max()) if not pct.empty else None,
        }
    return output


def compare(asset, eod, yahoo, exchange, alternate):
    joined = (
        eod.add_suffix("_eodhd")
        .join(yahoo.add_suffix("_yahoo"), how="inner")
        .join(alternate.add_suffix("_six"), how="left")
    )
    joined = joined.dropna(subset=[
        "Open_eodhd", "High_eodhd", "Low_eodhd", "Close_eodhd",
        "Open_yahoo", "High_yahoo", "Low_yahoo", "Close_yahoo",
    ])
    if len(joined) < 200:
        raise RuntimeError(f"{asset}: solapamiento insuficiente ({len(joined)} sesiones)")
    metrics = {"asset": asset, "yahoo_exchange": exchange, "overlap_sessions": len(joined)}
    price_errors = _price_error_metrics(eod, yahoo, "_eodhd", "_yahoo")
    six_errors = _price_error_metrics(eod, alternate, "_eodhd", "_six")
    yahoo_integrity = ohlc_integrity(yahoo)
    eodhd_integrity = ohlc_integrity(eod)
    six_integrity = ohlc_integrity(alternate)
    # Conservative research gate: no structurally invalid bars, p95 <= 0.5%,
    # and max absolute OHLC discrepancy <= 1.0% versus the live EODHD source.
    blockers = []
    if yahoo_integrity["invalid_rows"] != 0:
        blockers.append("yahoo_has_structurally_invalid_ohlc_rows")
    for col, stats in price_errors.items():
        if stats["p95_abs_pct"] is None or stats["p95_abs_pct"] > 0.5:
            blockers.append(f"yahoo_{col.lower()}_p95_error_over_0_5pct")
        if stats["max_abs_pct"] is None or stats["max_abs_pct"] > 1.0:
            blockers.append(f"yahoo_{col.lower()}_max_error_over_1pct")
    metrics.update({
        "provider_integrity": {
            "eodhd": eodhd_integrity,
            "yahoo_lse": yahoo_integrity,
            "yahoo_six": six_integrity,
        },
        "price_abs_pct_error": price_errors,
        "six_vs_eodhd_price_abs_pct_error": six_errors,
        "yahoo_research_quality_gate": "PASS" if not blockers else "FAIL",
        "yahoo_research_quality_blockers": blockers,
    })
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
        "volume_positive_overlap_sessions": int(len(positive)),
        "volume_ratio_yahoo_to_eodhd_median": (
            float(volume_ratio.median()) if not volume_ratio.empty else None
        ),
        "volume_ratio_yahoo_to_eodhd_p95": (
            float(volume_ratio.quantile(0.95)) if not volume_ratio.empty else None
        ),
    })
    # Explicitly surface extreme LSE-vs-EODHD and SIX-vs-EODHD OHLC discrepancies.
    for col in ("Open", "High", "Low", "Close"):
        primary_error = (
            joined[f"{col}_yahoo"] / joined[f"{col}_eodhd"] - 1.0
        ).abs() * 100
        alternate_error = (
            joined[f"{col}_six"] / joined[f"{col}_eodhd"] - 1.0
        ).abs() * 100
        metrics.setdefault("outliers_over_1pct", {})[col] = {
            "london_dates": [str(x) for x in joined.index[primary_error > 1.0]],
            "six_dates": [str(x) for x in joined.index[alternate_error > 1.0]],
        }
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
            yahoo, exchange = load_yahoo(
                session, asset, spec, start, end,
                symbol_key="yahoo", expected_market="london",
            )
            alternate, alternate_exchange = load_yahoo(
                session, asset, spec, start, end,
                symbol_key="alternate", expected_market="swiss",
            )
            result = compare(asset, eod, yahoo, exchange, alternate)
            result["alternate_symbol"] = spec["alternate"]
            result["alternate_exchange"] = alternate_exchange
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
        "provider_research_approved": bool(results) and all(
            item.get("yahoo_research_quality_gate") == "PASS" for item in results
        ),
        "interpretation": "Diagnostic only; a completed workflow is not a data-quality pass and does not authorize provider replacement.",
    }
    (OUT / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if errors or len(results) != 2:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
