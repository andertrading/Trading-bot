import json
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
import zipfile

from etoro_spread_history import (
    build_spread_history_report,
    extract_realtime_samples,
    fetch_artifact_reports,
)


NOW = datetime(2026, 11, 23, 16, 0, tzinfo=timezone.utc)


def evidence(timestamp, spread, *, quote_type="realtime", age=2):
    return {
        "captured_at": timestamp,
        "assets": {
            "AAPL": {
                "quote": {
                    "quote_type": quote_type,
                    "spread_evidence_scope": (
                        "CURRENT_SNAPSHOT_ONLY"
                        if quote_type == "realtime"
                        else "LAST_COMPLETED_SESSION_ONLY"
                    ),
                    "spread_bps": str(spread),
                    "age_seconds": age,
                    "timestamp": timestamp,
                }
            }
        },
    }


class EtoroSpreadHistoryTests(unittest.TestCase):
    def test_extracts_only_fresh_realtime_spread_quotes(self):
        report = evidence("2026-11-23T15:00:00+00:00", 5.5)
        report["assets"]["BTC"] = {
            "quote": {
                "quote_type": "last_session",
                "spread_evidence_scope": "LAST_COMPLETED_SESSION_ONLY",
                "spread_bps": "4.0",
                "age_seconds": 60,
                "timestamp": "2026-11-23T14:00:00+00:00",
            }
        }
        report["assets"]["ETH"] = {
            "quote": {
                "quote_type": "realtime",
                "spread_evidence_scope": "CURRENT_SNAPSHOT_ONLY",
                "spread_bps": "4.0",
                "age_seconds": 900,
                "timestamp": "2026-11-23T14:59:00+00:00",
            }
        }
        samples = extract_realtime_samples(report, run_id=123, now_utc=NOW)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["asset"], "AAPL")
        self.assertEqual(samples[0]["spread_bps"], 5.5)

    def test_deduplicates_multiple_runs_on_same_utc_date(self):
        older = evidence("2026-11-23T14:00:00+00:00", 5.0)
        newer = evidence("2026-11-23T15:00:00+00:00", 4.0)
        report = build_spread_history_report(
            [(1, older), (2, newer)],
            now_utc=NOW,
            configured_assets=["AAPL"],
            min_sessions=2,
        )
        asset = report["assets"]["AAPL"]
        self.assertEqual(asset["sample_count"], 1)
        self.assertEqual(asset["latest_sample"]["spread_bps"], 4.0)
        self.assertEqual(asset["readiness"], "INSUFFICIENT_FORWARD_SAMPLES")

    def test_requires_multiple_distinct_sessions_and_never_approves_asset(self):
        reports = []
        for day in range(1, 4):
            timestamp = f"2026-11-{day:02d}T15:00:00+00:00"
            reports.append((day, evidence(timestamp, 2.0 + day)))
        report = build_spread_history_report(
            reports,
            now_utc=NOW,
            configured_assets=["AAPL", "BTC"],
            min_sessions=4,
        )
        self.assertEqual(report["assets"]["AAPL"]["sample_count"], 3)
        self.assertEqual(
            report["assets"]["AAPL"]["readiness"],
            "INSUFFICIENT_FORWARD_SAMPLES",
        )
        self.assertEqual(report["assets"]["BTC"]["sample_count"], 0)
        self.assertFalse(report["economic_registry_modified"])
        self.assertEqual(report["round_trip_cost_validation"], "NOT_VALIDATED")
        self.assertIn("15:17 UTC", report["sampling_schedule_utc"])
        self.assertIn("not continuous", report["sampling_scope"])

    def test_30_sessions_are_reviewable_but_not_auto_approved(self):
        reports = []
        for day in range(1, 31):
            date = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(days=day)
            timestamp = date.replace(hour=15).isoformat()
            reports.append((day, evidence(timestamp, 4.0 + day / 10)))
        report = build_spread_history_report(
            reports,
            now_utc=NOW,
            configured_assets=["AAPL"],
        )
        asset = report["assets"]["AAPL"]
        self.assertEqual(asset["sample_count"], 30)
        self.assertEqual(asset["readiness"], "REVIEW_REQUIRED_30_TO_59_SESSIONS")
        self.assertIsNotNone(asset["p95_spread_bps"])
        self.assertEqual(report["historical_execution_price_basis"], "NOT_VALIDATED")



    def test_artifact_fetch_includes_completed_partial_failure_runs(self):
        now = datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc)
        runs = [
            {"id": 101, "head_branch": "main", "conclusion": "success",
             "created_at": "2026-11-24T16:00:00+00:00"},
            {"id": 102, "head_branch": "main", "conclusion": "failure",
             "created_at": "2026-11-25T16:00:00+00:00"},
            {"id": 103, "head_branch": "main", "conclusion": "cancelled",
             "created_at": "2026-11-25T17:00:00+00:00"},
            {"id": 104, "head_branch": "feature/test", "conclusion": "failure",
             "created_at": "2026-11-25T18:00:00+00:00"},
        ]
        artifacts = [
            {"id": 1001, "name": "etoro-market-evidence", "expired": False,
             "workflow_run": {"id": 101}},
            {"id": 1002, "name": "etoro-market-evidence", "expired": False,
             "workflow_run": {"id": 102}},
            {"id": 1003, "name": "etoro-market-evidence", "expired": False,
             "workflow_run": {"id": 103}},
            {"id": 1004, "name": "etoro-market-evidence", "expired": False,
             "workflow_run": {"id": 104}},
            {"id": 1005, "name": "other-artifact", "expired": False,
             "workflow_run": {"id": 102}},
        ]

        def zip_report(report):
            buffer = BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(
                    "etoro_market_evidence.json",
                    json.dumps(report),
                )
            return buffer.getvalue()

        reports_by_artifact = {
            1001: zip_report(evidence("2026-11-24T16:00:00+00:00", 4.5)),
            1002: zip_report(evidence("2026-11-25T16:00:00+00:00", 3.5)),
            1003: zip_report(evidence("2026-11-25T17:00:00+00:00", 3.0)),
            1004: zip_report(evidence("2026-11-25T18:00:00+00:00", 2.5)),
        }

        class FakeResponse:
            def __init__(self, *, payload=None, content=b""):
                self.payload = payload
                self.content = content

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class FakeSession:
            def get(self, url, *, headers, timeout):
                if "/actions/workflows/" in url and "/runs?" in url:
                    return FakeResponse(payload={"workflow_runs": runs})
                if "/actions/artifacts?" in url:
                    return FakeResponse(payload={"artifacts": artifacts})
                if url.endswith("/zip"):
                    artifact_id = int(url.rstrip("/").split("/")[-2])
                    return FakeResponse(content=reports_by_artifact[artifact_id])
                raise AssertionError("Unexpected URL: " + url)

        reports, artifact_count, errors = fetch_artifact_reports(
            "owner/repo",
            "test-token",
            now_utc=now,
            session=FakeSession(),
        )

        self.assertEqual(artifact_count, 2)
        self.assertEqual({run_id for run_id, _ in reports}, {101, 102})
        self.assertEqual(errors, [])
        failure_report = next(report for run_id, report in reports if run_id == 102)
        samples = extract_realtime_samples(
            failure_report,
            run_id=102,
            now_utc=now,
        )
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["spread_bps"], 3.5)

if __name__ == "__main__":
    unittest.main()
