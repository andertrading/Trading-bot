"""Research-only audit of same-ISIN London GBX listings as USD ETF history fallbacks.

This script never modifies production data or enables a provider. It compares
CSP1.L/CNX1.L in GBX with CSPX.L/CNDX.L in USD using the same-date GBP/USD close.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

OHLC = ("Open", "High", "Low", "Close")
ASSETS = {
    "CSPX": {
        "usd_ticker": "CSPX.L",
        "gbp_ticker": "CSP1.L",
        "isin": "IE00B5BMR087",
        "gap_dates": ["2025-10-24"],
    },
    "CNDX": {
        "usd_ticker": "CNDX.L",
        "gbp_ticker": "CNX1.L",
        "isin": "IE00B53SZB19",
        "gap_dates": ["2025-10-24", "2023-05-05"],
    },
}
FX_TICKER = "GBPUSD=X"
MAX_P95_DEVIATION_PCT = 0.5
MAX_FIELD_DEVIATION_PCT = 1.0
OUTPUT = Path("etf_gbp_listing_audit")


def normalize_history(frame):
    if frame is None or frame.empty:
        raise ValueError("empty history")
    result = frame.copy()
    if isinstance(result.columns, pd.MultiIndex):
        result.columns = result.columns.get_level_values(0)
    missing = [column for column in OHLC if column not in result.columns]
    if missing:
        raise ValueError(f"missing OHLC columns: {missing}")
    index = pd.to_datetime(result.index, errors="coerce")
    if index.isna().any():
        raise ValueError("invalid history dates")
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    result.index = index.normalize()
    if result.index.has_duplicates:
        raise ValueError("duplicate history dates")
    for column in OHLC:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.loc[:, list(OHLC)].sort_index()


def convert_gbp_pence_to_usd(gbp_frame, fx_frame):
    """Convert GBX OHLC to USD using same-session GBP/USD Close; no FX ffill."""
    gbp = normalize_history(gbp_frame)
    fx = normalize_history(fx_frame)
    joined = gbp.join(fx["Close"].rename("GBPUSD_Close"), how="left")
    valid_fx = (
        np.isfinite(joined["GBPUSD_Close"].to_numpy(dtype=float))
        & (joined["GBPUSD_Close"].to_numpy(dtype=float) > 0)
    )
    missing_fx_dates = [str(day.date()) for day in joined.index[~valid_fx]]
    usable = joined.loc[valid_fx]
    converted = usable.loc[:, list(OHLC)].div(100.0).mul(
        usable["GBPUSD_Close"], axis=0
    )
    converted.attrs["conversion"] = "GBX / 100 * same-session GBPUSD Close"
    converted.attrs["missing_fx_dates"] = missing_fx_dates
    return converted, missing_fx_dates


def ohlc_integrity(frame):
    data = normalize_history(frame)
    values = data.loc[:, list(OHLC)].to_numpy(dtype=float)
    finite_positive = np.isfinite(values).all() and (values > 0).all()
    coherent = (
        (data["High"] >= data["Low"])
        & (data["High"] >= data["Open"])
        & (data["High"] >= data["Close"])
        & (data["Low"] <= data["Open"])
        & (data["Low"] <= data["Close"])
    )
    return {
        "rows": int(len(data)),
        "invalid_rows": int((~coherent).sum()) if finite_positive else int(len(data)),
        "finite_positive": bool(finite_positive),
        "first_date": str(data.index.min().date()),
        "last_date": str(data.index.max().date()),
    }


def trusted_primary_fields(row):
    values = {}
    for column in OHLC:
        value = pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
        values[column] = float(value) if np.isfinite(value) and value > 0 else None
    trusted = {column for column, value in values.items() if value is not None}
    high, low = values["High"], values["Low"]
    open_price, close_price = values["Open"], values["Close"]
    if high is not None and any(
        value is not None and high < value
        for value in (low, open_price, close_price)
    ):
        trusted.discard("High")
    if low is not None and any(
        value is not None and low > value
        for value in (high, open_price, close_price)
    ):
        trusted.discard("Low")
    if open_price is not None and (
        ("High" in trusted and open_price > high)
        or ("Low" in trusted and open_price < low)
    ):
        trusted.discard("Open")
    if close_price is not None and (
        ("High" in trusted and close_price > high)
        or ("Low" in trusted and close_price < low)
    ):
        trusted.discard("Close")
    return trusted


def compare_ohlc(usd_frame, converted_gbp_frame):
    usd = normalize_history(usd_frame)
    gbp_usd = normalize_history(converted_gbp_frame)
    common = usd.index.intersection(gbp_usd.index)
    if len(common) == 0:
        raise ValueError("no common USD/GBX listing sessions")
    left = usd.loc[common]
    right = gbp_usd.loc[common]
    result = {"common_sessions": int(len(common)), "fields": {}}
    for column in OHLC:
        error = (left[column] / right[column] - 1.0).abs() * 100.0
        result["fields"][column] = {
            "median_abs_pct": round(float(error.median()), 6),
            "p95_abs_pct": round(float(error.quantile(0.95)), 6),
            "max_abs_pct": round(float(error.max()), 6),
            "over_0_5pct": int((error > 0.5).sum()),
            "over_1pct": int((error > 1.0).sum()),
        }
    result["first_common_date"] = str(common.min().date())
    result["last_common_date"] = str(common.max().date())
    result["max_field_p95_abs_pct"] = max(
        values["p95_abs_pct"] for values in result["fields"].values()
    )
    result["max_field_deviation_pct"] = max(
        values["max_abs_pct"] for values in result["fields"].values()
    )
    return result


def assess_target_dates(usd_frame, gbp_usd_frame, target_dates):
    usd = normalize_history(usd_frame)
    alternate = normalize_history(gbp_usd_frame)
    assessments = []
    for text_date in target_dates:
        day = pd.Timestamp(text_date)
        item = {
            "date": text_date,
            "gbx_listing_available": day in alternate.index,
            "usd_listing_available": day in usd.index,
            "status": "UNAVAILABLE",
        }
        if day not in alternate.index:
            item["status"] = "NO_GBX_CANDIDATE"
        elif day not in usd.index:
            item["status"] = "CANDIDATE_ONLY_NO_PRIMARY_CROSSCHECK"
            item["gbx_candidate_ohlc_usd":] = {
                column: round(float(alternate.at[day, column]), 6)
                for column in OHLC
            }
        else:
            trusted = trusted_primary_fields(usd.loc[day])
            item["trusted_primary_fields"] = sorted(trusted)
            deviations = {
                column: abs(
                    float(usd.at[day, column]) / float(alternate.at[day, column]) - 1.0
                ) * 100.0
                for column in trusted
            }
            item["trusted_field_deviations_pct"] = {
                key: round(value, 6) for key, value in deviations.items()
            }
            item["status"] = (
                "REPAIR_CANDIDATE_WITHIN_TOLERANCE"
                if trusted and max(deviations.values()) <= MAX_FIELD_DEVIATION_PCT
                else "REPAIR_REJECTED"
            )
        assessments.append(item)
    return assessments


def download(ticker, start, end):
    frame = yf.download(
        ticker,
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=False,
        repair=False,
        progress=False,
        threads=False,
    )
    return normalize_history(frame)


def main():
    end = date.today()
    start = end - timedelta(days=1825)
    OUTPUT.mkdir(exist_ok=True)
    fx = download(FX_TICKER, start, end)
    results = []
    errors = []
    for asset, spec in ASSETS.items():
        try:
            usd = download(spec["usd_ticker"], start, end)
            gbp = download(spec["gbp_ticker"], start, end)
            converted, missing_fx = convert_gbp_pence_to_usd(gbp, fx)
            comparison = compare_ohlc(usd, converted)
            targets = assess_target_dates(usd, converted, spec["gap_dates"])
            usd_integrity = ohlc_integrity(usd)
            gbp_integrity = ohlc_integrity(converted)
            comparison["usd_listing_integrity"] = usd_integrity
            comparison["gbx_listing_integrity_after_conversion"] = gbp_integrity
            comparison["missing_fx_dates"] = missing_fx
            comparison["target_dates"] = targets
            comparison["tickers"] = {
                "usd": spec["usd_ticker"],
                "gbx": spec["gbp_ticker"],
                "isin": spec["isin"],
            }
            comparison["quality_gate"] = (
                "PASS"
                if (
                    comparison["max_field_p95_abs_pct"] <= MAX_P95_DEVIATION_PCT
                    and comparison["max_field_deviation_pct"] <= MAX_FIELD_DEVIATION_PCT
                    and usd_integrity["invalid_rows"] == 0
                    and gbp_integrity["invalid_rows"] == 0
                    and not missing_fx
                )
                else "FAIL"
            )
            (OUTPUT / f"{asset.lower()}_usd_vs_gbx.csv").write_text(
                pd.concat(
                    [
                        usd.add_suffix("_usd"),
                        converted.add_suffix("_gbx_converted_usd"),
                    ],
                    axis=1,
                    join="inner",
                ).to_csv(index_label="date"),
                encoding="utf-8",
            )
            results.append({"asset": asset, **comparison})
        except Exception as exc:
            errors.append(f"{asset}: {type(exc).__name__}: {exc}")
            results.append({"asset": asset, "quality_gate": "FAIL", "error": errors[-1]})

    report = {
        "scope": "research_only_gbx_same_isin_cross_listing_audit",
        "requested_start": str(start),
        "requested_end_exclusive": str(end),
        "fx_ticker": FX_TICKER,
        "fx_policy": "same-session GBPUSD Close; no forward-fill",
        "production_provider_changed": False,
        "historical_bars_repaired": False,
        "eToro_account_or_portfolio_accessed": False,
        "orders_or_signals_enabled": False,
        "results": results,
        "errors": errors,
        "provider_repair_approved": (
            not errors
            and len(results) == len(ASSETS)
            and all(item.get("quality_gate") == "PASS" for item in results)
            and all(
                target.get("status") == "REPAIR_CANDIDATE_WITHIN_TOLERANCE"
                for item in results
                for target in item.get("target_dates", [])
                if target.get("usd_listing_available")
            )
        ),
        "interpretation": (
            "Research only. A passing cross-listing comparison is not permission "
            "to repair missing primary bars without date-specific independent evidence."
        ),
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
