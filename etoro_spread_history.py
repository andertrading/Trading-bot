"""Aggregate forward-only eToro realtime spread snapshots from GitHub Actions artifacts.

This diagnostic never edits the economic registry and never accesses broker
account balances, positions, PnL, orders or trade history.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import math
import os
from pathlib import Path
import zipfile
from typing import Any, Mapping

import requests

API_ROOT = "https://api.github.com"
EVIDENCE_WORKFLOW = "etoro_market_evidence.yml"
EVIDENCE_ARTIFACT = "etoro-market-evidence"
MIN_SESSIONS_FOR_REVIEW = 30
MIN_SESSIONS_FOR_STRONG_REVIEW = 60


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _finite_nonnegative(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def extract_realtime_samples(
    report: Mapping[str, Any],
    *,
    run_id: int,
    now_utc: datetime,
) -> list[dict[str, Any]]:
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware")
    captured_at = _timestamp(report.get("captured_at"))
    if captured_at is None or captured_at > now_utc.astimezone(timezone.utc):
        return []
    assets = report.get("assets")
    if not isinstance(assets, Mapping):
        return []

    samples = []
    for asset, record in assets.items():
        if not isinstance(record, Mapping):
            continue
        quote = record.get("quote")
        if not isinstance(quote, Mapping) or quote.get("quote_type") != "realtime":
            continue
        if quote.get("spread_evidence_scope") != "CURRENT_SNAPSHOT_ONLY":
            continue
        spread_bps = _finite_nonnegative(quote.get("spread_bps"))
        age_seconds = _finite_nonnegative(quote.get("age_seconds"))
        quote_timestamp = _timestamp(quote.get("timestamp"))
        if (
            spread_bps is None
            or age_seconds is None
            or age_seconds > 300
            or quote_timestamp is None
            or quote_timestamp > now_utc.astimezone(timezone.utc)
            or quote_timestamp > captured_at + timedelta(minutes=2)
        ):
            continue
        samples.append({
            "asset": str(asset),
            "timestamp": quote_timestamp.isoformat(),
            "utc_date": quote_timestamp.date().isoformat(),
            "spread_bps": spread_bps,
            "quote_age_seconds": age_seconds,
            "workflow_run_id": int(run_id),
        })
    return samples


def build_spread_history_report(
    artifact_reports: list[tuple[int, Mapping[str, Any]]],
    *,
    now_utc: datetime,
    lookback_days: int = 90,
    configured_assets: list[str] | tuple[str, ...] | None = None,
    min_sessions: int = MIN_SESSIONS_FOR_REVIEW,
) -> dict[str, Any]:
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware")
    now_utc = now_utc.astimezone(timezone.utc)
    if isinstance(lookback_days, bool) or not isinstance(lookback_days, int) or lookback_days < 1:
        raise ValueError("lookback_days must be a positive integer")
    if isinstance(min_sessions, bool) or not isinstance(min_sessions, int) or min_sessions < 1:
        raise ValueError("min_sessions must be a positive integer")
    cutoff = now_utc - timedelta(days=lookback_days)
    latest_by_asset_day: dict[tuple[str, str], dict[str, Any]] = {}
    reports_read = 0
    for run_id, report in artifact_reports:
        captured_at = _timestamp(report.get("captured_at"))
        if captured_at is None or captured_at < cutoff or captured_at > now_utc:
            continue
        reports_read += 1
        for sample in extract_realtime_samples(
            report, run_id=run_id, now_utc=now_utc
        ):
            quote_time = _timestamp(sample["timestamp"])
            if quote_time is None or quote_time < cutoff:
                continue
            key = (sample["asset"], sample["utc_date"])
            prior = latest_by_asset_day.get(key)
            if prior is None or sample["timestamp"] > prior["timestamp"]:
                latest_by_asset_day[key] = sample

    assets_to_report = sorted(set(configured_assets or ()) | {
        asset for asset, _ in latest_by_asset_day
    })
    asset_results = {}
    for asset in assets_to_report:
        samples = sorted(
            (
                sample for (symbol, _), sample in latest_by_asset_day.items()
                if symbol == asset
            ),
            key=lambda item: item["timestamp"],
        )
        values = [sample["spread_bps"] for sample in samples]
        sessions = len(samples)
        if sessions < min_sessions:
            readiness = "INSUFFICIENT_FORWARD_SAMPLES"
        elif sessions < MIN_SESSIONS_FOR_STRONG_REVIEW:
            readiness = "REVIEW_REQUIRED_30_TO_59_SESSIONS"
        else:
            readiness = "REVIEW_REQUIRED_60_PLUS_SESSIONS"
        asset_results[asset] = {
            "sample_count": sessions,
            "distinct_utc_dates": sessions,
            "median_spread_bps": _percentile(values, 0.50),
            "p90_spread_bps": _percentile(values, 0.90),
            "p95_spread_bps": _percentile(values, 0.95),
            "maximum_spread_bps": max(values) if values else None,
            "latest_sample": samples[-1] if samples else None,
            "readiness": readiness,
            "evidence_scope": "FORWARD_REALTIME_SNAPSHOTS_ONLY",
            "samples": samples,
        }

    sample_counts = [item["sample_count"] for item in asset_results.values()]
    return {
        "schema_version": 1,
        "captured_at": now_utc.isoformat(),
        "mode": "READ_ONLY_FORWARD_SPREAD_HISTORY_AUDIT",
        "source": "eToro realtime bid/ask quote snapshots from GitHub Actions artifacts",
        "sampling_schedule_utc": (
            "Weekdays at 15:17 UTC (GitHub Actions cron); manual captures may use other times"
        ),
        "sampling_scope": (
            "One latest realtime snapshot per asset and UTC date; not continuous "
            "intraday spread history"
        ),
        "lookback_days": lookback_days,
        "minimum_sessions_for_review": min_sessions,
        "account_data_accessed": False,
        "order_actions_performed": False,
        "economic_registry_modified": False,
        "round_trip_cost_validation": "NOT_VALIDATED",
        "historical_execution_price_basis": "NOT_VALIDATED",
        "summary": {
            "artifact_reports_read": reports_read,
            "assets_reported": len(asset_results),
            "assets_with_minimum_review_samples": sum(
                item["sample_count"] >= min_sessions
                for item in asset_results.values()
            ),
            "maximum_sessions_observed": max(sample_counts, default=0),
            "status": (
                "FORWARD_SAMPLE_COLLECTION"
                if not any(item["sample_count"] >= min_sessions for item in asset_results.values())
                else "SPREAD_DISTRIBUTIONS_READY_FOR_MANUAL_REVIEW"
            ),
        },
        "assets": asset_results,
    }


def _github_json(session: requests.Session, url: str, headers: dict[str, str]) -> dict[str, Any]:
    response = session.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("GitHub API returned a non-object response")
    return payload


def fetch_artifact_reports(
    repository: str,
    token: str,
    *,
    now_utc: datetime,
    lookback_days: int = 90,
    session: requests.Session | None = None,
) -> tuple[list[tuple[int, Mapping[str, Any]]], int, list[str]]:
    if "/" not in repository:
        raise ValueError("repository must use owner/name format")
    if not token:
        raise ValueError("GitHub token is required")
    session = session or requests.Session()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    root = f"{API_ROOT}/repos/{repository}"
    runs = []
    for page in range(1, 6):
        payload = _github_json(
            session,
            f"{root}/actions/workflows/{EVIDENCE_WORKFLOW}/runs"
            f"?branch=main&status=completed&per_page=100&page={page}",
            headers,
        )
        batch = payload.get("workflow_runs", [])
        if not isinstance(batch, list):
            raise ValueError("GitHub workflow runs response is invalid")
        runs.extend(batch)
        if len(batch) < 100:
            break
    cutoff = now_utc.astimezone(timezone.utc) - timedelta(days=lookback_days)
    eligible_run_ids = set()
    for run in runs:
        if (
            not isinstance(run, Mapping)
            or run.get("head_branch") != "main"
            # Partial evidence captures intentionally exit non-zero after
            # uploading their JSON artifact. Include completed failures too:
            # downstream extraction independently accepts only fresh,
            # timestamp-valid realtime quote samples from the artifact.
            or run.get("conclusion") not in {"success", "failure"}
        ):
            continue
        created = _timestamp(run.get("created_at"))
        if created is not None and cutoff <= created <= now_utc.astimezone(timezone.utc):
            eligible_run_ids.add(run.get("id"))

    artifacts = []
    for page in range(1, 6):
        payload = _github_json(
            session,
            f"{root}/actions/artifacts?per_page=100&page={page}",
            headers,
        )
        batch = payload.get("artifacts", [])
        if not isinstance(batch, list):
            raise ValueError("GitHub artifacts response is invalid")
        artifacts.extend(batch)
        if len(batch) < 100:
            break

    selected = [
        item for item in artifacts
        if isinstance(item, Mapping)
        and item.get("name") == EVIDENCE_ARTIFACT
        and item.get("expired") is False
        and isinstance(item.get("workflow_run"), Mapping)
        and item["workflow_run"].get("id") in eligible_run_ids
    ]
    reports = []
    errors = []
    for artifact in sorted(selected, key=lambda item: item.get("created_at", "")):
        artifact_id = artifact.get("id")
        try:
            response = session.get(
                f"{root}/actions/artifacts/{artifact_id}/zip",
                headers=headers,
                timeout=60,
            )
            response.raise_for_status()
            with zipfile.ZipFile(BytesIO(response.content)) as archive:
                json_files = [
                    name for name in archive.namelist()
                    if name.endswith(".json") and not name.startswith("__MACOSX/")
                ]
                if len(json_files) != 1:
                    raise ValueError("artifact must contain exactly one JSON report")
                report = json.loads(archive.read(json_files[0]))
            if not isinstance(report, Mapping):
                raise ValueError("eToro artifact JSON is not an object")
            run_id = artifact["workflow_run"]["id"]
            reports.append((int(run_id), report))
        except Exception as exc:
            errors.append(f"artifact {artifact_id}: {type(exc).__name__}: {exc}")
    return reports, len(selected), errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY"))
    parser.add_argument("--token", default=os.getenv("GH_TOKEN"))
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--min-sessions", type=int, default=MIN_SESSIONS_FOR_REVIEW)
    parser.add_argument("--output", default="artifacts/etoro_spread_history.json")
    args = parser.parse_args()
    if not args.repo or not args.token:
        raise SystemExit("GITHUB_REPOSITORY and GH_TOKEN are required")
    now_utc = datetime.now(timezone.utc)
    reports, artifact_count, errors = fetch_artifact_reports(
        args.repo, args.token, now_utc=now_utc,
        lookback_days=args.lookback_days,
    )
    from config import ASSETS
    result = build_spread_history_report(
        reports,
        now_utc=now_utc,
        lookback_days=args.lookback_days,
        configured_assets=sorted(ASSETS),
        min_sessions=args.min_sessions,
    )
    result["summary"]["artifacts_found"] = artifact_count
    result["summary"]["artifact_read_errors"] = len(errors)
    result["artifact_read_errors"] = errors
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], ensure_ascii=False))
    print(f"Informe guardado: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
