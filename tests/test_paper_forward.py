import unittest

from paper_forward import (
    new_paper_state,
    paper_summary,
    paper_portfolio_snapshot,
    process_signal_bar,
)


def signal(signal_id="s1", status="PENDING"):
    return {
        "signal_id": signal_id,
        "asset": "BTC",
        "timeframe": "15m",
        "status": status,
        "direction": "LONG",
        "data_timestamp": "2026-11-22T10:00:00+00:00",
        "generated_at": "2026-11-22T10:00:00+00:00",
        "expires_at": "2026-11-22T11:00:00+00:00",
        "monitoring_expires_at": "2026-11-22T18:00:00+00:00",
        "entry_reference": 100.0,
        "stop": 95.0,
        "target": 110.0,
    }


def bar(ts, opening, high, low, close):
    return {"timestamp": ts, "open": opening, "high": high, "low": low, "close": close}


class PaperForwardTests(unittest.TestCase):
    def setUp(self):
        self.state = new_paper_state()

    def test_initial_virtual_capital_is_separate_and_fixed(self):
        self.assertEqual(self.state["equity_usd"], 1000.0)
        self.assertEqual(self.state["execution_mode"], "PAPER_ONLY")

    def test_ignores_signal_creation_candle_and_fills_later_entry_touch(self):
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:00:00+00:00", 99, 101, 98, 100
        ))
        self.assertEqual(self.state["trades"]["s1"]["status"], "WAITING")
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:15:00+00:00", 99, 101, 98, 100
        ))
        trade = self.state["trades"]["s1"]
        self.assertEqual(trade["status"], "OPEN")
        self.assertAlmostEqual(trade["risk_budget_usd"], 2.5)
        self.assertAlmostEqual(trade["quantity"], 0.5)

    def test_same_bar_entry_and_stop_is_conservatively_not_filled(self):
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:15:00+00:00", 99, 101, 94, 99
        ))
        self.assertEqual(
            self.state["trades"]["s1"]["status"],
            "NO_FILL_AMBIGUOUS_ENTRY_STOP",
        )

    def test_stop_and_target_same_bar_closes_at_stop(self):
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:15:00+00:00", 99, 101, 98, 100
        ))
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:30:00+00:00", 100, 111, 94, 100
        ))
        trade = self.state["trades"]["s1"]
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["exit_reason"], "STOP")
        self.assertEqual(trade["exit_fill_price"], 95.0)
        self.assertAlmostEqual(trade["result_r"], -1.0)

    def test_stop_gap_fills_at_worse_open(self):
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:15:00+00:00", 99, 101, 98, 100
        ))
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:30:00+00:00", 92, 94, 91, 93
        ))
        self.assertEqual(self.state["trades"]["s1"]["exit_fill_price"], 92.0)

    def test_target_gap_gets_opening_price(self):
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:15:00+00:00", 99, 101, 98, 100
        ))
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:30:00+00:00", 112, 114, 111, 113
        ))
        self.assertEqual(self.state["trades"]["s1"]["exit_fill_price"], 112.0)

    def test_duplicate_and_out_of_order_bars_are_ignored(self):
        first = bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100)
        process_signal_bar(self.state, signal(), first)
        process_signal_bar(self.state, signal(), first)
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:10:00+00:00", 99, 101, 98, 100
        ))
        self.assertEqual(self.state["trades"]["s1"]["status"], "OPEN")


    def test_aggregate_open_risk_cap_blocks_additional_paper_trade(self):
        first = signal("first")
        second = signal("second")
        fill_bar = bar("2026-11-22T10:15:00+00:00", 99.5, 101, 99.5, 100)
        process_signal_bar(
            self.state, first, fill_bar, max_portfolio_risk=0.003
        )
        process_signal_bar(
            self.state, second, fill_bar, max_portfolio_risk=0.003
        )
        self.assertEqual(self.state["trades"]["first"]["status"], "OPEN")
        self.assertEqual(
            self.state["trades"]["second"]["status"],
            "REJECTED_PORTFOLIO_RISK",
        )
        self.assertAlmostEqual(self.state["open_risk_usd"], 2.5)

    def test_aggregate_cash_reservation_prevents_implicit_leverage(self):
        fill_bar = bar("2026-11-22T10:15:00+00:00", 99.5, 101, 99.5, 100)
        for index in range(5):
            item = signal(f"cash-{index}")
            item["stop"] = 99.0
            item["target"] = 102.0
            process_signal_bar(self.state, item, fill_bar)
        statuses = [
            self.state["trades"][f"cash-{index}"]["status"]
            for index in range(5)
        ]
        self.assertEqual(statuses.count("OPEN"), 4)
        self.assertEqual(statuses.count("REJECTED_NO_LEVERAGE_CASH"), 1)
        self.assertLessEqual(
            sum(
                trade.get("notional_usd", 0.0)
                for trade in self.state["trades"].values()
                if trade["status"] == "OPEN"
            ),
            self.state["equity_usd"],
        )

    def test_open_trade_exits_at_monitoring_deadline(self):
        item = signal()
        item["monitoring_expires_at"] = "2026-11-22T10:30:00+00:00"
        process_signal_bar(self.state, item, bar(
            "2026-11-22T10:15:00+00:00", 99, 101, 98, 100
        ))
        process_signal_bar(self.state, item, bar(
            "2026-11-22T10:30:00+00:00", 100, 101, 99.5, 101
        ))
        trade = self.state["trades"]["s1"]
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["exit_reason"], "TIME_EXIT")
        self.assertEqual(trade["exit_fill_price"], 101.0)

    def test_stop_touch_before_entry_invalidates_waiting_trade(self):
        item = signal()
        process_signal_bar(
            self.state, item,
            bar("2026-11-22T10:15:00+00:00", 99.5, 99.8, 94.8, 95.2),
        )
        self.assertEqual(
            self.state["trades"]["s1"]["status"],
            "NO_FILL_STOP_TOUCHED",
        )
        process_signal_bar(
            self.state, item,
            bar("2026-11-22T10:30:00+00:00", 99.8, 100.5, 95.5, 100.2),
        )
        self.assertEqual(
            self.state["trades"]["s1"]["status"],
            "NO_FILL_STOP_TOUCHED",
        )

    def test_zero_provisional_risk_never_creates_a_paper_fill(self):
        process_signal_bar(
            self.state, signal(),
            bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100),
            risk_fraction=0.0,
        )
        self.assertEqual(
            self.state["trades"]["s1"]["status"],
            "REJECTED_INVALID_RISK_POLICY",
        )
        self.assertEqual(self.state["open_risk_usd"], 0.0)

    def test_hard_per_trade_risk_cap_cannot_be_overridden(self):
        with self.assertRaises(ValueError):
            process_signal_bar(
                self.state, signal(),
                bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100),
                risk_fraction=0.011,
            )

    def test_expired_signal_cannot_open(self):
        expired = signal()
        expired["expires_at"] = "2026-11-22T10:05:00+00:00"
        process_signal_bar(self.state, expired, bar(
            "2026-11-22T10:15:00+00:00", 99, 101, 98, 100
        ))
        self.assertEqual(self.state["trades"]["s1"]["status"], "NOT_FILLED_EXPIRED_OR_INVALID")

    def test_paper_portfolio_snapshot_reports_virtual_cash_and_open_risk(self):
        snapshot = paper_portfolio_snapshot(self.state)
        self.assertEqual(snapshot["portfolio_basis"], "PAPER_GROSS_CONSERVATIVE_CAPITAL")
        self.assertEqual(snapshot["capital_usd"], 1000.0)
        self.assertEqual(snapshot["available_capital_usd"], 1000.0)
        self.assertEqual(snapshot["open_positions"], [])
        self.assertTrue(snapshot["positions_confirmed"])

        process_signal_bar(
            self.state, signal(),
            bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100),
        )
        snapshot = paper_portfolio_snapshot(self.state)
        self.assertEqual(len(snapshot["open_positions"]), 1)
        self.assertAlmostEqual(snapshot["open_positions"][0]["risk_usd"], 2.5)
        self.assertAlmostEqual(snapshot["open_positions"][0]["notional_usd"], 50.0)
        self.assertAlmostEqual(snapshot["invested_notional_usd"], 50.0)
        self.assertAlmostEqual(snapshot["available_capital_usd"], 950.0)

    def test_gross_profit_cannot_increase_conservative_paper_capital(self):
        process_signal_bar(
            self.state, signal(),
            bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100),
        )
        process_signal_bar(
            self.state, signal(),
            bar("2026-11-22T10:30:00+00:00", 110, 111, 109, 110),
        )
        snapshot = paper_portfolio_snapshot(self.state)
        self.assertAlmostEqual(snapshot["equity_usd"], 1005.0)
        self.assertAlmostEqual(snapshot["capital_usd"], 1000.0)
        self.assertAlmostEqual(snapshot["available_capital_usd"], 1000.0)

    def test_realized_gross_loss_reduces_conservative_paper_capital(self):
        process_signal_bar(
            self.state, signal(),
            bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100),
        )
        process_signal_bar(
            self.state, signal(),
            bar("2026-11-22T10:30:00+00:00", 95, 95, 94, 94.5),
        )
        snapshot = paper_portfolio_snapshot(self.state)
        self.assertAlmostEqual(snapshot["capital_usd"], 997.5)
        self.assertAlmostEqual(snapshot["available_capital_usd"], 997.5)

    def test_gross_profit_does_not_increase_paper_risk_or_cash_limit(self):
        first = signal("profit-first")
        process_signal_bar(
            self.state, first,
            bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100),
        )
        process_signal_bar(
            self.state, first,
            bar("2026-11-22T10:30:00+00:00", 110, 111, 109, 110),
        )
        self.assertAlmostEqual(self.state["equity_usd"], 1005.0)

        second = signal("cash-cap-after-gross-profit")
        second.update({
            "data_timestamp": "2026-11-22T10:30:00+00:00",
            "generated_at": "2026-11-22T10:30:00+00:00",
            "expires_at": "2026-11-22T11:30:00+00:00",
            "stop": 99.75,
        })
        process_signal_bar(
            self.state, second,
            bar("2026-11-22T10:45:00+00:00", 100, 100.5, 99.9, 100.2),
        )
        trade = self.state["trades"]["cash-cap-after-gross-profit"]
        self.assertEqual(trade["status"], "OPEN")
        self.assertAlmostEqual(trade["risk_budget_usd"], 2.5)
        self.assertAlmostEqual(trade["risk_capital_base_usd"], 1000.0)
        self.assertAlmostEqual(trade["notional_usd"], 1000.0)

    def test_invalid_paper_state_or_missing_open_notional_is_rejected(self):
        process_signal_bar(
            self.state, signal(),
            bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100),
        )
        del self.state["trades"]["s1"]["notional_usd"]
        with self.assertRaises(ValueError):
            paper_portfolio_snapshot(self.state)

        with self.assertRaises(ValueError):
            paper_portfolio_snapshot(
                new_paper_state(900),
                expected_initial_capital_usd=1000,
            )

    def test_summary_reports_gross_only_and_never_claims_net_validation(self):
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:15:00+00:00", 99, 101, 98, 100
        ))
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:30:00+00:00", 110, 111, 109, 110
        ))
        summary = paper_summary(self.state)
        self.assertEqual(summary["closed_trades"], 1)
        self.assertFalse(summary["costs_included"])
        self.assertFalse(summary["net_performance_validated"])
        self.assertAlmostEqual(summary["realized_gross_pnl_usd"], 5.0)


if __name__ == "__main__":
    unittest.main()
