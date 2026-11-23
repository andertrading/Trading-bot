import json
import tempfile
import unittest
from pathlib import Path

from research.run_coinbase_oos_portfolio import (
    load_oos_test_trades,
    run_portfolio_oos,
)


ASSETS = ("BTC", "ETH", "SOL", "XLM", "XRP")
TIMEFRAMES = ("15m", "1d")


class CoinbaseOOSPortfolioRunnerTests(unittest.TestCase):
    def _write_fixture(self, root: Path, *, invalid_provider=False):
        for asset in ASSETS:
            for timeframe in TIMEFRAMES:
                start = "2026-01-01T00:00:00+00:00"
                end = "2026-01-02T00:00:00+00:00"
                trade = {
                    "asset": asset,
                    "timeframe": timeframe,
                    "signal_time": "2026-01-01T09:45:00+00:00",
                    "entry_time": "2026-01-01T10:00:00+00:00",
                    "exit_time": "2026-01-01T11:00:00+00:00",
                    "entry": 100.0,
                    "stop": 90.0,
                    "score": 80,
                    "result_R": 1.0,
                    "exit": 110.0,
                    "data_source": "coinbase_exchange_only",
                    "source_venue": "Coinbase Exchange",
                    "etoro_price_equivalence": "not_established",
                    "costs_included": False,
                    "oos_fold": "test",
                    "segment_id": 0,
                }
                payload = {
                    "asset": asset,
                    "interval": timeframe,
                    "provider": "other" if invalid_provider else "coinbase_exchange_only",
                    "research_only": True,
                    "production_changes": False,
                    "network_requests": 0,
                    "all_trades_inside_oos_fold": True,
                    "folds": [{
                        "fold": "test",
                        "segment_id": 0,
                        "evaluation_start": start,
                        "evaluation_end": end,
                    }],
                    "all_trades": [trade],
                }
                (root / f"{asset}_{timeframe}.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )

    def test_loads_only_validated_test_fold_trades(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_fixture(root)
            trades, source = load_oos_test_trades(root)
        self.assertEqual(len(trades), 10)
        self.assertEqual(source["series_count"], 10)
        self.assertEqual(source["test_folds"], 10)
        self.assertEqual(source["test_trades"], 10)
        self.assertEqual(source["network_requests"], 0)

    def test_existing_allocator_applies_shared_risk_and_cash_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_fixture(root)
            result = run_portfolio_oos(root, capital_usd=1000.0)
        summary = result["summary"]
        self.assertEqual(summary["trades_accepted"], 10)
        self.assertEqual(summary["trades_rejected"], 0)
        self.assertAlmostEqual(summary["peak_open_risk_usd"], 30.0)
        self.assertAlmostEqual(summary["gross_pnl_usd"], 30.0)
        self.assertIsNone(summary["net_pnl_usd"])
        self.assertFalse(summary["net_performance_validated"])
        self.assertFalse(summary["unrealized_pnl_included"])

    def test_rejects_non_coinbase_series(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_fixture(root, invalid_provider=True)
            with self.assertRaisesRegex(ValueError, "not Coinbase Exchange-only"):
                load_oos_test_trades(root)

    def test_rejects_duplicate_test_trade_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_fixture(root)
            path = root / "BTC_15m.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["all_trades"].append(dict(payload["all_trades"][0]))
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate OOS trade identity"):
                load_oos_test_trades(root)


if __name__ == "__main__":
    unittest.main()
