import json
import tempfile
import unittest
from pathlib import Path

from research.oos_trade_overlap_audit import audit_oos_trade_overlaps

ASSETS = ("BTC", "ETH", "SOL", "XLM", "XRP")
TIMEFRAMES = ("15m", "1d")


class OOSTradeOverlapAuditTests(unittest.TestCase):
    def _write_fixture(self, root: Path, *, btc_15m_exit="2026-01-01T01:45:00+00:00"):
        for asset_index, asset in enumerate(ASSETS):
            for timeframe in TIMEFRAMES:
                if asset == "BTC" and timeframe == "1d":
                    signal, entry, exit_ = ("2026-01-01T00:00:00+00:00", "2026-01-01T01:00:00+00:00", "2026-01-02T00:00:00+00:00")
                elif asset == "BTC" and timeframe == "15m":
                    signal, entry, exit_ = ("2026-01-01T01:15:00+00:00", "2026-01-01T01:30:00+00:00", btc_15m_exit)
                else:
                    day = 3 + asset_index * 2 + (1 if timeframe == "1d" else 0)
                    date = f"2026-01-{day:02d}"
                    signal, entry, exit_ = (f"{date}T00:00:00+00:00", f"{date}T00:15:00+00:00", f"{date}T00:45:00+00:00")
                trade = {
                    "asset": asset, "timeframe": timeframe, "signal_time": signal,
                    "entry_time": entry, "exit_time": exit_, "result_R": 1.0,
                    "data_source": "coinbase_exchange_only", "source_venue": "Coinbase Exchange",
                    "etoro_price_equivalence": "not_established", "costs_included": False,
                    "oos_fold": "test", "segment_id": 0,
                }
                payload = {
                    "asset": asset, "interval": timeframe, "provider": "coinbase_exchange_only",
                    "research_only": True, "production_changes": False, "network_requests": 0,
                    "all_trades_inside_oos_fold": True,
                    "folds": [{"fold": "test", "segment_id": 0,
                        "evaluation_start": "2026-01-01T00:00:00+00:00",
                        "evaluation_end": "2026-01-20T00:00:00+00:00"}],
                    "all_trades": [trade],
                }
                (root / f"{asset}_{timeframe}.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_detects_same_asset_cross_timeframe_overlap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_fixture(root)
            result = audit_oos_trade_overlaps(root)
        self.assertEqual(result["overlaps"]["same_asset_cross_timeframe"]["overlapping_pairs"], 1)
        self.assertEqual(result["overlaps"]["same_asset_same_timeframe"]["overlapping_pairs"], 0)
        self.assertEqual(result["maximum_concurrent_positions"]["by_asset"]["BTC"], 2)

    def test_zero_duration_trade_does_not_create_positive_duration_overlap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_fixture(root, btc_15m_exit="2026-01-01T01:30:00+00:00")
            result = audit_oos_trade_overlaps(root)
        self.assertEqual(result["overlaps"]["same_asset_cross_timeframe"]["overlapping_pairs"], 0)

    def test_rejects_artifact_that_reports_network_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_fixture(root)
            path = root / "BTC_15m.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["network_requests"] = 1
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reports network requests"):
                audit_oos_trade_overlaps(root)


if __name__ == "__main__":
    unittest.main()
