import json
import tempfile
import unittest
from pathlib import Path

from research.oos_portfolio_etoro_crypto_fee_sensitivity import (
    _metrics,
    audit_portfolio_fee_sensitivity,
)


class OOSPortfolioCryptoFeeSensitivityTests(unittest.TestCase):
    def test_fee_applies_to_both_open_and_close_notionals(self):
        trade = {
            "quantity": 2,
            "entry": 100,
            "exit_price": 110,
            "realized_pnl_usd": 20,
            "stop": 95,
            "planned_gross_stop_risk_usd": 10,
        }
        result = _metrics([trade], 1.0)
        self.assertEqual(result["fee_only_cost_usd"], 4.2)
        self.assertEqual(result["fee_only_adjusted_pnl_on_fixed_allocations_usd"], 15.8)
        self.assertEqual(result["median_fee_burden_R"], 0.42)
        self.assertEqual(result["median_stop_distance_pct"], 5.0)

    def test_audit_rejects_net_validated_or_cost_included_source(self):
        payload = {
            "mode": "RESEARCH_ONLY_OOS_PORTFOLIO_SIMULATION",
            "source_validation": {
                "provider": "coinbase_exchange_only",
                "eToro_price_equivalence": "not_established",
                "verified_transaction_costs": "not_complete",
            },
            "summary": {
                "costs_included": False,
                "net_performance_validated": True,
                "verified_costs_complete_trades": 0,
                "trades_accepted": 1,
            },
            "portfolio_policy": {
                "capital_usd": 1000.0,
                "maximum_risk_per_trade_pct": 1.0,
                "maximum_aggregate_open_risk_pct": 3.0,
            },
            "accepted_trades": [{
                "asset": "BTC", "timeframe": "15m", "quantity": 1,
                "entry": 100, "exit_price": 101, "realized_pnl_usd": 1,
                "costs_included": False, "verified_costs_complete": False,
                "verified_costs_total_usd": None,
            }],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "portfolio.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                audit_portfolio_fee_sensitivity(path)

    def test_audit_requires_costs_unverified_for_each_accepted_trade(self):
        payload = {
            "mode": "RESEARCH_ONLY_OOS_PORTFOLIO_SIMULATION",
            "source_validation": {
                "provider": "coinbase_exchange_only",
                "eToro_price_equivalence": "not_established",
                "verified_transaction_costs": "not_complete",
            },
            "summary": {
                "costs_included": False,
                "net_performance_validated": False,
                "verified_costs_complete_trades": 0,
                "trades_accepted": 1,
            },
            "portfolio_policy": {
                "capital_usd": 1000.0,
                "maximum_risk_per_trade_pct": 1.0,
                "maximum_aggregate_open_risk_pct": 3.0,
            },
            "accepted_trades": [{
                "asset": "BTC", "timeframe": "15m", "quantity": 1,
                "entry": 100, "exit_price": 101, "realized_pnl_usd": 1,
                "costs_included": False, "verified_costs_complete": False,
                "verified_costs_total_usd": 0.0,
            }],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "portfolio.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                audit_portfolio_fee_sensitivity(path)


if __name__ == "__main__":
    unittest.main()
