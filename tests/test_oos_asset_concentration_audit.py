import json
import tempfile
import unittest
from pathlib import Path

from research.oos_asset_concentration_audit import audit_asset_concentration

ASSETS = ("BTC", "ETH", "SOL", "XLM", "XRP")
TIMEFRAMES = ("15m", "1d")


class OOSAssetConcentrationAuditTests(unittest.TestCase):
    def _write_fixture(self, root: Path):
        for asset_index, asset in enumerate(ASSETS):
            for timeframe in TIMEFRAMES:
                if asset == "BTC" and timeframe == "15m":
                    signal, entry, exit_, result = (
                        "2026-01-01T00:00:00+00:00",
                        "2026-01-01T00:15:00+00:00",
                        "2026-01-01T02:00:00+00:00", 2.0,
                    )
                elif asset == "BTC" and timeframe == "1d":
                    signal, entry, exit_, result = (
                        "2026-01-01T00:00:00+00:00",
                        "2026-01-01T01:00:00+00:00",
                        "2026-01-02T00:00:00+00:00", -1.0,
                    )
                else:
                    day = 3 + asset_index * 2 + (1 if timeframe == "1d" else 0)
                    date = f"2026-01-{day:02d}"
                    signal, entry, exit_, result = (
                        f"{date}T00:00:00+00:00",
                        f"{date}T00:15:00+00:00",
                        f"{date}T00:45:00+00:00", 1.0,
                    )
                trade = {
                    "asset": asset, "timeframe": timeframe, "signal_time": signal,
                    "entry_time": entry, "exit_time": exit_, "result_R": result,
                    "data_source": "coinbase_exchange_only", "source_venue": "Coinbase Exchange",
                    "etoro_price_equivalence": "not_established", "costs_included": False,
                    "oos_fold": "test", "segment_id": 0,
                }
                payload = {
                    "asset": asset, "interval": timeframe, "provider": "coinbase_exchange_only",
                    "research_only": True, "production_changes": False, "network_requests": 0,
                    "all_trades_inside_oos_fold": True,
                    "folds": [{
                        "fold": "test", "segment_id": 0,
                        "evaluation_start": "2026-01-01T00:00:00+00:00",
                        "evaluation_end": "2026-01-20T00:00:00+00:00",
                    }],
                    "all_trades": [trade],
                }
                (root / f"{asset}_{timeframe}.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_asset_breakdown_and_ex_post_overlap_exclusion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_fixture(root)
            result = audit_asset_concentration(root)
        self.assertEqual(result["by_asset"]["BTC"]["trades"], 2)
        self.assertEqual(result["same_asset_cross_timeframe_overlap"]["overlapping_pairs"], 1)
        self.assertEqual(
            result["overlap_exclusion_sensitivity"]["m15_member_trades_removed"], 1
        )
        self.assertEqual(
            result["overlap_exclusion_sensitivity"]["daily_member_trades_removed"], 1
        )
        self.assertIsNone(result["weekly_m15_exit_buckets"]["effective_sample_size"])


if __name__ == "__main__":
    unittest.main()
