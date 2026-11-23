import unittest

import transaction_costs as costs
from paper_forward import new_paper_state, process_signal_bar, paper_summary


def signal(signal_id="verified", asset="CSPX"):
    return {
        "signal_id": signal_id,
        "asset": asset,
        "timeframe": "1d",
        "status": "PENDING",
        "direction": "LONG",
        "data_timestamp": "2026-11-22T10:00:00+00:00",
        "generated_at": "2026-11-22T10:00:00+00:00",
        "expires_at": "2026-11-23T10:00:00+00:00",
        "monitoring_expires_at": "2026-11-24T10:00:00+00:00",
        "entry_reference": 100.0,
        "stop": 95.0,
        "target": 110.0,
    }


def bar(ts, opening, high, low, close):
    return {"timestamp": ts, "open": opening, "high": high, "low": low, "close": close}


def complete_registry():
    registry = costs.load_registry()
    item = registry["assets"]["CSPX"]
    evidence = {
        "confirmed": True,
        "source": "test-only verified execution evidence",
        "observed_at": "2026-11-22T00:00:00Z",
    }
    item["instrument"].update(evidence)
    item["execution"].update({"type": "REAL", **evidence})
    item["fees"] = {
        "entry": {"model": "FIXED", "value": 1.0, "unit": "USD"},
        "exit": {"model": "PERCENT_NOTIONAL", "value": 0.1, "unit": "PERCENT"},
        **evidence,
    }
    item["spread"].update({
        "bid_ask_bps": 10.0,
        "treatment": "SEPARATE_FROM_PRICES",
        "evidence_scope": "HISTORICAL_CONSERVATIVE_MODEL",
        **evidence,
    })
    item["price_basis"].update({
        "historical_basis": "split-adjusted historical close",
        "execution_basis": "broker executable bid/ask quote",
        "mapping_rule": "entry ask and stop-side exit bid",
        **evidence,
    })
    item["minimum_order"].update({
        "minimum_notional": 1.0,
        "fractional_allowed": True,
        **evidence,
    })
    return registry


class VerifiedPaperLedgerTests(unittest.TestCase):
    def test_verified_costs_reduce_quantity_and_reserve_full_stop_risk(self):
        state = new_paper_state()
        process_signal_bar(
            state, signal(),
            bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100),
            require_verified_costs=True,
            economic_registry=complete_registry(),
        )
        trade = state["trades"]["verified"]
        self.assertEqual(trade["status"], "OPEN")
        self.assertLess(trade["quantity"], 0.5)
        self.assertAlmostEqual(trade["risk_reserved_usd"], 2.5, places=7)
        self.assertGreater(trade["estimated_stop_costs_usd"], 1.0)

    def test_unverified_asset_cannot_open_in_verified_cost_mode(self):
        state = new_paper_state()
        process_signal_bar(
            state, signal(asset="BTC"),
            bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100),
            require_verified_costs=True,
        )
        trade = state["trades"]["verified"]
        self.assertEqual(trade["status"], "REJECTED_COSTS_UNVERIFIED")
        self.assertIsNone(trade.get("quantity"))

    def test_close_debits_actual_round_trip_cost_once_and_reports_net(self):
        state = new_paper_state()
        registry = complete_registry()
        process_signal_bar(
            state, signal(),
            bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100),
            require_verified_costs=True, economic_registry=registry,
        )
        process_signal_bar(
            state, signal(),
            bar("2026-11-22T10:30:00+00:00", 100, 101, 94, 100),
            require_verified_costs=True, economic_registry=registry,
        )
        trade = state["trades"]["verified"]
        self.assertEqual(trade["status"], "CLOSED")
        self.assertGreater(trade["costs_usd"], 0)
        self.assertAlmostEqual(trade["net_pnl_usd"], trade["gross_pnl_usd"] - trade["costs_usd"])
        self.assertAlmostEqual(state["equity_usd"], 1000.0 + trade["net_pnl_usd"])
        summary = paper_summary(state)
        self.assertTrue(summary["costs_included"])
        self.assertFalse(summary["net_performance_validated"])
        self.assertAlmostEqual(summary["realized_costs_usd"], trade["costs_usd"])

    def test_verified_aggregate_risk_cap_uses_cost_inclusive_reservation(self):
        state = new_paper_state()
        fill = bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100)
        registry = complete_registry()
        process_signal_bar(
            state, signal("first"), fill, risk_fraction=0.0025,
            max_portfolio_risk=0.003, require_verified_costs=True,
            economic_registry=registry,
        )
        process_signal_bar(
            state, signal("second"), fill, risk_fraction=0.0025,
            max_portfolio_risk=0.003, require_verified_costs=True,
            economic_registry=registry,
        )
        self.assertEqual(state["trades"]["first"]["status"], "OPEN")
        self.assertEqual(state["trades"]["second"]["status"], "REJECTED_PORTFOLIO_RISK")


if __name__ == "__main__":
    unittest.main()
