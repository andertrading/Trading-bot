import json
import tempfile
import unittest
from pathlib import Path

from research.oos_etoro_crypto_fee_sensitivity import _net_r, audit_fee_sensitivity


class OOSCryptoFeeSensitivityTests(unittest.TestCase):
    def test_round_trip_fee_is_applied_to_both_entry_and_exit_notional(self):
        trade = {
            "hypothetical_quantity": 2,
            "entry": 100,
            "exit": 110,
            "initial_risk_per_unit": 5,
            "result_R": 2,
        }
        # $4.20 total fee / $10 risk = 0.42R; net result is 1.58R.
        self.assertAlmostEqual(_net_r([trade], 1.0)[0], 1.58)

    def test_invalid_or_non_positive_size_fails_closed(self):
        trade = {
            "hypothetical_quantity": 0,
            "entry": 100,
            "exit": 110,
            "initial_risk_per_unit": 5,
            "result_R": 2,
        }
        with self.assertRaises(ValueError):
            _net_r([trade], 1.0)

    def test_audit_rejects_non_real_or_cost_included_source_trades(self):
        # The strict shared loader performs source/fold validation before this
        # function applies the additional fee-scenario-specific safeguards.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for asset in ("BTC", "ETH", "SOL", "XLM", "XRP"):
                for timeframe in ("15m", "1d"):
                    date = "2026-01-01T00:00:00+00:00"
                    trade = {
                        "asset": asset, "timeframe": timeframe,
                        "signal_time": date, "entry_time": "2026-01-01T00:15:00+00:00",
                        "exit_time": "2026-01-01T00:45:00+00:00", "result_R": 1,
                        "data_source": "coinbase_exchange_only", "source_venue": "Coinbase Exchange",
                        "etoro_price_equivalence": "not_established", "costs_included": True,
                        "costs_complete": False, "cost_model_execution_type": "REAL",
                        "hypothetical_quantity": 1, "entry": 100, "exit": 101,
                        "initial_risk_per_unit": 1, "oos_fold": "test", "segment_id": 0,
                    }
                    payload = {
                        "asset": asset, "interval": timeframe, "provider": "coinbase_exchange_only",
                        "research_only": True, "production_changes": False, "network_requests": 0,
                        "all_trades_inside_oos_fold": True,
                        "folds": [{
                            "fold": "test", "segment_id": 0,
                            "evaluation_start": date, "evaluation_end": "2026-01-02T00:00:00+00:00",
                        }],
                        "all_trades": [trade],
                    }
                    (root / f"{asset}_{timeframe}.json").write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                audit_fee_sensitivity(root)


if __name__ == "__main__":
    unittest.main()
