"""Read-only Yahoo vs EODHD daily OHLCV overlap audit for CSPX/CNDX.

Compares exact London USD ETF listings over the EODHD overlap window.
No eToro access, portfolio access, orders, or strategy calculations.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

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


def _get_twelvedata_json(session, url, params, stage):
    try:
        response = session.get(url, params=params, timeout=45)
    except requests.RequestException:
        raise RuntimeError(f"Twelve Data {stage} network error") from None
    if response.status_code >= 400:
        raise RuntimeError(f"Twelve Data {stage} HTTP {response.status_code}")
    payload = response.json()
    if isinstance(payload, dict) and payload.get("status") == "error":
        raise RuntimeError(
            f"Twelve Data {stage} API code {payload.get('code', 'unknown')}"
        )
    return payload


def load_twelvedata_daily(session, asset, start, end, token):
    """Try Twelve Data's existing Basic credential for an independent LSE ETF history."""
    search = _get_twelvedata_json(
        session,
        "https://api.twelvedata.com/symbol_search",
        {
            "symbol": asset,
            "outputsize": 50,
            "show_plan": "true",
            "apikey": token,
        },
        "symbol_search",
    )
    candidates = [
        item for item in search.get("data", [])
        if str(item.get("mic_code", "")).upper() == "XLON"
        and str(item.get("currency", "")).upper() == "USD"
        and str(item.get("instrument_type", "")).upper() == "ETF"
    ]
    if not candidates:
        raise RuntimeError(f"{asset}: Twelve Data no ofrece coincidencia ETF USD en XLON")
    exact = [item for item in candidates if str(item.get("symbol", "")).upper() == asset]
    if len(exact) != 1:
        raise RuntimeError(
            f"{asset}: Twelve Data devolvió {len(exact)} símbolos exactos XLON/USD/ETF"
        )
    candidate = exact[0]
    params = {
        "symbol": candidate["symbol"],
        "mic_code": "XLON",
        "interval": "1day",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apikey": token,
    }
    payload = _get_twelvedata_json(
        session, "https://api.twelvedata.com/time_series", params, "time_series"
    )
    meta = payload.get("meta", {})
    if (
        str(meta.get("mic_code", "")).upper() != "XLON"
        or str(meta.get("currency", "")).upper() != "USD"
    ):
        raise RuntimeError(f"{asset}: Twelve Data metadata XLON/USD no confirmada")
    values = payload.get("values")
    if not isinstance(values, list) or not values:
        raise RuntimeError(f"{asset}: Twelve Data histórico diario vacío")
    frame = pd.DataFrame(values)
    required = ["datetime", "open", "high", "low", "close"]
    if any(col not in frame.columns for col in required):
        raise RuntimeError(f"{asset}: Twelve Data OHLC incompleto")
    frame["date"] = pd.to_datetime(frame["datetime"], errors="coerce").dt.date
    if frame["date"].isna().any():
        raise RuntimeError(f"{asset}: Twelve Data fechas inválidas")
    frame = frame.set_index("date").sort_index()
    if frame.index.has_duplicates:
        raise RuntimeError(f"{asset}: Twelve Data fechas duplicadas")
    for col in ("open", "high", "low", "close"):
        frame[col.title()] = pd.to_numeric(frame[col], errors="coerce")
    frame["Volume"] = (
        pd.to_numeric(frame["volume"], errors="coerce")
        if "volume" in frame.columns else 0.0
    )
    return frame[["Open", "High", "Low", "Close", "Volume"]]


def load_yfinance_daily(ticker, start, end):
    """Use the same yfinance options and date normalization as data_engine.py."""
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    frame = yf.Ticker(ticker).history(
        start=start_dt,
        end=end_dt,
        interval="1d",
        auto_adjust=False,
        actions=True,
        repair=False,
        timeout=25,
    )
    if frame is None or frame.empty:
        raise RuntimeError(f"{ticker}: yfinance histórico vacío")
    index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce"))
    if index.isna().any():
        raise RuntimeError(f"{ticker}: yfinance timestamps inválidos")
    if index.tz is not None:
        index = index.tz_localize(None)
    index = index.normalize().tz_localize("UTC")
    frame = frame.copy()
    # Match the session-date key used by EODHD and Yahoo chart comparisons.
    frame.index = index.date
    frame.index.name = "date"
    required = ["Open", "High", "Low", "Close"]
    if any(col not in frame.columns for col in required):
        raise RuntimeError(f"{ticker}: yfinance OHLC incompleto")
    if "Volume" not in frame.columns:
        frame["Volume"] = 0.0
    frame = frame[["Open", "High", "Low", "Close", "Volume"]]
    for col in frame.columns:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if frame.index.has_duplicates:
        raise RuntimeError(f"{ticker}: yfinance fechas duplicadas")
    return frame.sort_index()


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


def assess_alternate_repairs(primary, alternate):
    """Mirror the production repair eligibility rules for every invalid yfinance bar."""
    invalid = _invalid_ohlc_dates(primary)
    maximum_repairs = max(10, int(np.ceil(len(primary) * 0.01)))
    issues = []
    deviations = []
    for day in invalid:
        if day not in alternate.index:
            issues.append({"date": str(day), "reason": "alternate_date_missing"})
            continue
        alt = alternate.loc[day]
        if isinstance(alt, pd.DataFrame) or bool(ohlc_integrity(alternate.loc[[day]])["invalid_rows"]):
            issues.append({"date": str(day), "reason": "alternate_bar_invalid"})
            continue
        primary_close = pd.to_numeric(
            pd.Series([primary.at[day, "Close"]]), errors="coerce"
        ).iloc[0]
        alternate_close = pd.to_numeric(
            pd.Series([alt["Close"]]), errors="coerce"
        ).iloc[0]
        if (
            not np.isfinite(primary_close) or primary_close <= 0
            or not np.isfinite(alternate_close) or alternate_close <= 0
        ):
            issues.append({"date": str(day), "reason": "close_unverifiable"})
            continue
        deviation = abs(float(primary_close) / float(alternate_close) - 1.0) * 100.0
        deviations.append(deviation)
        if deviation > 0.5:
            issues.append({
                "date": str(day),
                "reason": "close_deviation_over_0_5pct",
                "close_deviation_pct": round(deviation, 6),
            })
    if len(invalid) > maximum_repairs:
        issues.append({
            "date": None,
            "reason": "repair_count_over_production_limit",
            "invalid_count": len(invalid),
            "maximum_repairs": maximum_repairs,
        })
    return {
        "invalid_primary_bar_count": len(invalid),
        "maximum_repairs_allowed": maximum_repairs,
        "alternate_repair_issues": issues,
        "max_close_deviation_pct": round(max(deviations), 6) if deviations else None,
        "all_invalid_bars_repairable": len(invalid) > 0 and not issues,
        "no_repair_needed": len(invalid) == 0,
    }


def _invalid_ohlc_dates(frame):
    mask = ohlc_integrity(frame)
    required = ["Open", "High", "Low", "Close"]
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
    return list(frame.index[invalid])



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


def compare(asset, eod, yahoo, exchange, alternate, yahoo_yf, alternate_yf, twelvedata=None):
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
    chart_price_errors = _price_error_metrics(eod, yahoo, "_eodhd", "_yahoo_chart")
    price_errors = _price_error_metrics(eod, yahoo_yf, "_eodhd", "_yahoo_yfinance")
    six_errors = _price_error_metrics(eod, alternate_yf, "_eodhd", "_six_yfinance")
    chart_vs_yfinance = _price_error_metrics(yahoo, yahoo_yf, "_yahoo_chart", "_yahoo_yfinance")
    full_lse_six = (
        yahoo_yf.add_suffix("_lse")
        .join(alternate_yf.add_suffix("_six"), how="inner")
    )
    full_lse_six_errors = _price_error_metrics(
        yahoo_yf, alternate_yf, "_lse", "_six"
    )
    full_lse_six.to_csv(
        OUT / f"{asset.lower()}_five_year_lse_vs_six.csv",
        index_label="date",
    )
    full_lse_six_outliers = {}
    for col in ("Open", "High", "Low", "Close"):
        error = (
            full_lse_six[f"{col}_lse"] / full_lse_six[f"{col}_six"] - 1.0
        ).abs() * 100
        full_lse_six_outliers[col] = {
            "over_0_5pct": int((error > 0.5).sum()),
            "over_1pct": int((error > 1.0).sum()),
            "over_2pct": int((error > 2.0).sum()),
            "dates_over_1pct": [str(day) for day in full_lse_six.index[error > 1.0]],
        }
    yahoo_integrity = ohlc_integrity(yahoo)
    yahoo_yfinance_integrity = ohlc_integrity(yahoo_yf)
    chart_only_vs_yfinance = list(yahoo.index.difference(yahoo_yf.index))
    eodhd_only_vs_yfinance = list(eod.index.difference(yahoo_yf.index))
    missing_session_crosscheck = []
    missing_days = sorted(set(chart_only_vs_yfinance) | set(eodhd_only_vs_yfinance))
    for day in missing_days:
        item = {
            "date": str(day),
            "yahoo_chart_available": day in yahoo.index,
            "eodhd_available": day in eod.index,
            "six_available": day in alternate_yf.index,
            "twelvedata_available": twelvedata is not None and day in twelvedata.index,
        }
        if day in yahoo.index:
            chart_close = pd.to_numeric(
                pd.Series([yahoo.at[day, "Close"]]), errors="coerce"
            ).iloc[0]
            item["yahoo_chart_close"] = (
                float(chart_close) if np.isfinite(chart_close) and chart_close > 0 else None
            )
        if day in alternate_yf.index:
            six_row = alternate_yf.loc[day]
            item["six_ohlc_valid"] = bool(
                ohlc_integrity(alternate_yf.loc[[day]])["invalid_rows"] == 0
            )
            if day in yahoo.index:
                chart_close = item.get("yahoo_chart_close")
                six_close = float(six_row["Close"])
                item["six_close"] = six_close
                item["six_close_deviation_vs_yahoo_chart_pct"] = (
                    round(abs(chart_close / six_close - 1.0) * 100.0, 6)
                    if chart_close is not None else None
                )
        if day in eod.index:
            eod_row = eod.loc[day]
            item["eodhd_ohlc_valid"] = bool(ohlc_integrity(eod.loc[[day]])["invalid_rows"] == 0)
            item["eodhd_ohlc"] = {
                col: float(eod_row[col]) for col in ("Open", "High", "Low", "Close")
            }
            if day in alternate_yf.index:
                six_row = alternate_yf.loc[day]
                item["six_ohlc_deviation_vs_eodhd_pct"] = {
                    col: round(abs(float(six_row[col]) / float(eod_row[col]) - 1.0) * 100.0, 6)
                    for col in ("Open", "High", "Low", "Close")
                }
        missing_session_crosscheck.append(item)
    eodhd_integrity = ohlc_integrity(eod)
    six_integrity = ohlc_integrity(alternate)
    six_yfinance_integrity = ohlc_integrity(alternate_yf)
    alternate_repair_assessment = assess_alternate_repairs(yahoo_yf, alternate_yf)
    twelvedata_metrics = None
    if twelvedata is not None:
        twelvedata_metrics = {
            "integrity": ohlc_integrity(twelvedata),
            "vs_eodhd_price_abs_pct_error": _price_error_metrics(
                eod, twelvedata, "_eodhd", "_twelvedata"
            ),
            "vs_yahoo_yfinance_price_abs_pct_error": _price_error_metrics(
                yahoo_yf, twelvedata, "_yahoo_yfinance", "_twelvedata"
            ),
            "overlap_sessions_with_yahoo_yfinance": int(
                len(yahoo_yf.index.intersection(twelvedata.index))
            ),
            "first_date": str(twelvedata.index.min()),
            "last_date": str(twelvedata.index.max()),
        }
    # Gate the exact yfinance path used by extended backtests, not just Yahoo's chart endpoint.
    blockers = []
    if yahoo_yfinance_integrity["invalid_rows"] != 0:
        blockers.append("yfinance_yahoo_has_structurally_invalid_ohlc_rows")
    for col, stats in price_errors.items():
        if stats["p95_abs_pct"] is None or stats["p95_abs_pct"] > 0.5:
            blockers.append(f"yfinance_{col.lower()}_p95_error_over_0_5pct")
        if stats["max_abs_pct"] is None or stats["max_abs_pct"] > 1.0:
            blockers.append(f"yfinance_{col.lower()}_max_error_over_1pct")
    metrics.update({
        "provider_integrity": {
            "eodhd": eodhd_integrity,
            "yahoo_chart_lse": yahoo_integrity,
            "yahoo_yfinance_lse": yahoo_yfinance_integrity,
            "yahoo_chart_six": six_integrity,
            "yahoo_yfinance_six": six_yfinance_integrity,
        },
        "price_abs_pct_error": price_errors,
        "yahoo_chart_price_abs_pct_error": chart_price_errors,
        "yahoo_chart_vs_yfinance_price_abs_pct_error": chart_vs_yfinance,
        "five_year_lse_vs_six_yfinance_price_abs_pct_error": full_lse_six_errors,
        "five_year_lse_vs_six_yfinance_outliers": full_lse_six_outliers,
        "five_year_lse_six_overlap_sessions": int(len(full_lse_six)),
        "five_year_lse_six_first_date": str(full_lse_six.index.min()),
        "five_year_lse_six_last_date": str(full_lse_six.index.max()),
        "six_yfinance_vs_eodhd_price_abs_pct_error": six_errors,
        "alternate_repair_assessment": alternate_repair_assessment,
        "twelvedata_independent_source": twelvedata_metrics,
        "yfinance_missing_session_diagnostics": {
            "chart_only_vs_yfinance_dates": [str(day) for day in chart_only_vs_yfinance],
            "eodhd_only_vs_yfinance_dates": [str(day) for day in eodhd_only_vs_yfinance],
            "eodhd_overlap_missing_session_crosscheck": missing_session_crosscheck,
        },
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
    # Extend provider certification to the same ~5-year window used by daily backtests.
    start = end - timedelta(days=1825)
    results, errors, twelvedata_errors = [], [], []
    twelvedata_token = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
    print("AUDITORÍA OHLCV YAHOO/EODHD/SIX/TWELVE DATA | CSPX/CNDX | SOLO LECTURA")
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
            yahoo_yf = load_yfinance_daily(spec["yahoo"], start, end)
            alternate_yf = load_yfinance_daily(spec["alternate"], start, end)
            twelvedata_frame = None
            if twelvedata_token:
                try:
                    twelvedata_frame = load_twelvedata_daily(
                        session, asset, start, end, twelvedata_token
                    )
                except Exception as exc:
                    # Never log request exception text: it may contain API query parameters.
                    twelvedata_errors.append(
                        f"{asset}: {type(exc).__name__}: {exc}"
                    )
            result = compare(
                asset, eod, yahoo, exchange, alternate,
                yahoo_yf, alternate_yf, twelvedata_frame,
            )
            result["twelvedata_status"] = (
                "available" if twelvedata_frame is not None
                else "not_configured" if not twelvedata_token
                else "unavailable"
            )
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
        "twelvedata_errors": twelvedata_errors,
        "twelvedata_credential_configured": bool(twelvedata_token),
        "provider_research_approved": (
            not errors and len(results) == len(ASSETS) == 2
            and all(item.get("yahoo_research_quality_gate") == "PASS" for item in results)
        ),
        "interpretation": "Diagnostic only; a completed workflow is not a data-quality pass and does not authorize provider replacement.",
    }
    (OUT / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if errors or len(results) != 2:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
