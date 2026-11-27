import copy
import unittest
from datetime import datetime, timezone

from asset_economic_registry import load_registry
from paper_forward import (
    calculate_verified_cost_aware_quantity,
    new_paper_state,
    paper_summary,
    paper_portfolio_snapshot,
    process_signal_bar,
    _refresh_costs_status,
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

    def test_cost_status_does_not_claim_net_when_gross_trade_is_mixed_in(self):
        self.state["trades"] = {
            "gross": {
                "status": "CLOSED",
                "cost_mode": "GROSS_DIAGNOSTIC",
                "gross_pnl_usd": 5.0,
            },
            "verified": {
                "status": "CLOSED",
                "cost_mode": "VERIFIED_REGISTRY",
                "gross_pnl_usd": 5.0,
                "costs_usd": 1.0,
                "net_pnl_usd": 4.0,
            },
        }
        self.state["costs_status"] = "VERIFIED_NET_COSTS_INCLUDED"

        _refresh_costs_status(self.state)
        snapshot = paper_portfolio_snapshot(self.state)

        self.assertEqual(self.state["costs_status"], "NOT_INCLUDED_UNVERIFIED")
        self.assertEqual(snapshot["portfolio_basis"], "PAPER_GROSS_CONSERVATIVE_CAPITAL")

    def test_unresolved_close_costs_cannot_be_overwritten_by_later_verified_close(self):
        self.state["trades"] = {
            "unresolved": {
                "status": "CLOSED",
                "cost_mode": "VERIFIED_REGISTRY",
                "gross_pnl_usd": -5.0,
                "costs_usd": None,
                "net_pnl_usd": None,
            },
            "verified": {
                "status": "CLOSED",
                "cost_mode": "VERIFIED_REGISTRY",
                "gross_pnl_usd": 5.0,
                "costs_usd": 1.0,
                "net_pnl_usd": 4.0,
            },
        }

        _refresh_costs_status(self.state)
        snapshot = paper_portfolio_snapshot(self.state)

        self.assertEqual(self.state["costs_status"], "UNRESOLVED_CLOSE_COSTS")
        self.assertEqual(snapshot["portfolio_basis"], "PAPER_GROSS_CONSERVATIVE_CAPITAL")

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

    def test_entry_and_target_same_bar_from_below_entry_is_ambiguous_no_fill(self):
        # The bar opens below entry, then reaches both entry and target.
        # OHLC does not prove that entry happened before target.
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:15:00+00:00", 99, 111, 98, 109
        ))
        self.assertEqual(
            self.state["trades"]["s1"]["status"],
            "NO_FILL_AMBIGUOUS_ENTRY_TARGET",
        )
        self.assertEqual(self.state["open_risk_usd"], 0.0)

    def test_entry_at_bar_open_then_target_in_same_bar_is_valid_fill_and_exit(self):
        # Opening at entry establishes that the entry is available before
        # subsequent intrabar movement to the target.
        process_signal_bar(self.state, signal(), bar(
            "2026-11-22T10:15:00+00:00", 100, 111, 99, 109
        ))
        trade = self.state["trades"]["s1"]
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["exit_reason"], "TARGET")
        self.assertEqual(trade["entry_fill_price"], 100.0)
        self.assertEqual(trade["exit_fill_price"], 110.0)

    def test_trade_persists_signal_and_entry_exit_bar_provenance(self):
        item = signal("provenance")
        item.update({
            "data_provider": "synthetic-provider",
            "price_adjustment_policy": "RAW",
            "price_adjustment_verified": False,
            "price_adjustment_verification_status": "NOT_REQUIRED",
            "currency": "USD",
            "score": 82,
            "signal_bar_evidence": {
                "timestamp": "2026-11-22T10:00:00+00:00",
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "data_provider": "synthetic-provider",
            },
        })
        entry_bar = bar(
            "2026-11-22T10:15:00+00:00", 99, 101, 98, 100
        )
        entry_bar.update({
            "data_provider": "synthetic-provider",
            "price_adjustment_policy": "RAW",
            "price_adjustment_verified": False,
            "price_adjustment_verification_status": "NOT_REQUIRED",
        })
        process_signal_bar(self.state, item, entry_bar)
        trade = self.state["trades"]["provenance"]
        self.assertEqual(
            trade["signal_evidence"]["data_provider"], "synthetic-provider"
        )
        self.assertEqual(
            trade["signal_evidence"]["price_adjustment_policy"], "RAW"
        )
        self.assertEqual(
            trade["signal_evidence"]["signal_bar_evidence"]["close"], 100.0
        )
        self.assertEqual(
            trade["entry_bar"]["timestamp"], "2026-11-22T10:15:00+00:00"
        )
        self.assertEqual(
            trade["entry_bar"]["price_adjustment_verification_status"],
            "NOT_REQUIRED",
        )

        exit_bar = bar(
            "2026-11-22T10:30:00+00:00", 95, 96, 94, 95
        )
        exit_bar.update({
            "data_provider": "synthetic-provider",
            "price_adjustment_policy": "RAW",
            "price_adjustment_verified": False,
            "price_adjustment_verification_status": "NOT_REQUIRED",
        })
        process_signal_bar(self.state, item, exit_bar)
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(
            trade["exit_bar"]["timestamp"], "2026-11-22T10:30:00+00:00"
        )
        self.assertEqual(
            trade["exit_bar"]["data_provider"], "synthetic-provider"
        )

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


    def test_snapshot_rejects_open_notional_mismatch(self):
        process_signal_bar(
            self.state,
            signal("first"),
            bar("2026-11-22T10:15:00+00:00", 99.5, 101, 99.5, 100),
        )
        trade = self.state["trades"]["first"]
        trade["notional_usd"] *= 0.5

        with self.assertRaisesRegex(ValueError, "notional does not match"):
            paper_portfolio_snapshot(self.state)

    def test_new_entry_rejects_existing_open_notional_mismatch(self):
        process_signal_bar(
            self.state,
            signal("first"),
            bar("2026-11-22T10:15:00+00:00", 99.5, 101, 99.5, 100),
        )
        self.state["trades"]["first"]["notional_usd"] *= 0.5

        second = signal("second")
        second.update({
            "data_timestamp": "2026-11-22T10:15:00+00:00",
            "generated_at": "2026-11-22T10:15:00+00:00",
            "expires_at": "2026-11-22T11:15:00+00:00",
        })
        with self.assertRaisesRegex(ValueError, "notional does not match"):
            process_signal_bar(
                self.state,
                second,
                bar("2026-11-22T10:30:00+00:00", 99.5, 101, 99.5, 100),
            )

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

    def test_invalid_open_risk_in_paper_state_blocks_new_entries(self):
        invalid_risk_values = (-1.0, 0.0, float("nan"), float("inf"), True, None)
        fill_bar = bar("2026-11-22T10:15:00+00:00", 99.5, 101, 99.5, 100)

        for value in invalid_risk_values:
            with self.subTest(value=value):
                state = new_paper_state()
                state["trades"]["corrupt-open-position"] = {
                    "status": "OPEN",
                    "risk_reserved_usd": value,
                    "notional_usd": 100.0,
                    "cash_reserved_usd": 100.0,
                }

                with self.assertRaisesRegex(ValueError, "risk_reserved_usd"):
                    process_signal_bar(
                        state,
                        signal("new-entry"),
                        fill_bar,
                    )

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
        self.state["costs_status"] = "COSTS_CONFIRMED"
        snapshot = paper_portfolio_snapshot(self.state)
        self.assertEqual(snapshot["costs_status"], "NOT_INCLUDED_UNVERIFIED")

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

    def test_drawdown_is_independent_of_order_for_same_timestamp_closes(self):
        timestamp = "2026-11-22T10:30:00+00:00"
        outcomes = [
            {
                "status": "CLOSED",
                "gross_pnl_usd": -50.0,
                "result_r": -1.0,
                "exit_timestamp": timestamp,
                "cost_mode": "GROSS_DIAGNOSTIC",
            },
            {
                "status": "CLOSED",
                "gross_pnl_usd": 60.0,
                "result_r": 1.2,
                "exit_timestamp": timestamp,
                "cost_mode": "GROSS_DIAGNOSTIC",
            },
        ]

        summaries = []
        for ordered in (outcomes, list(reversed(outcomes))):
            state = new_paper_state()
            state["trades"] = {
                f"trade-{index}": copy.deepcopy(trade)
                for index, trade in enumerate(ordered)
            }
            state["realized_gross_pnl_usd"] = 10.0
            state["equity_usd"] = 1010.0
            state["equity_history"] = [
                {"timestamp": None, "equity_usd": 1000.0},
                {"timestamp": timestamp, "equity_usd": 950.0},
                {"timestamp": timestamp, "equity_usd": 1010.0},
            ]
            summaries.append(paper_summary(state))

        self.assertEqual(
            summaries[0]["max_drawdown_pct_gross"],
            summaries[1]["max_drawdown_pct_gross"],
        )
        self.assertAlmostEqual(
            summaries[0]["max_drawdown_pct_gross"], 5.0
        )
        self.assertAlmostEqual(
            summaries[0]["max_drawdown_pct_gross_close_to_close"], 0.0
        )
        self.assertAlmostEqual(
            summaries[0]["max_drawdown_pct_equity_close_to_close"], 0.0
        )

    def test_net_performance_metrics_expose_gross_positive_but_net_negative_results(self):
        timestamp = "2026-11-22T10:30:00+00:00"
        state = new_paper_state()
        state["trades"] = {
            "net-loss": {
                "status": "CLOSED",
                "gross_pnl_usd": -50.0,
                "result_r": -1.0,
                "cost_mode": "VERIFIED_REGISTRY",
                "costs_usd": 5.0,
                "net_pnl_usd": -55.0,
                "result_r_net": -1.1,
                "risk_budget_usd": 50.0,
                "exit_timestamp": timestamp,
            },
            "net-win": {
                "status": "CLOSED",
                "gross_pnl_usd": 60.0,
                "result_r": 1.2,
                "cost_mode": "VERIFIED_REGISTRY",
                "costs_usd": 10.0,
                "net_pnl_usd": 50.0,
                "result_r_net": 1.0,
                "risk_budget_usd": 50.0,
                "exit_timestamp": timestamp,
            },
        }
        state["realized_gross_pnl_usd"] = 10.0
        state["realized_costs_usd"] = 15.0
        state["realized_net_pnl_usd"] = -5.0
        state["equity_usd"] = 995.0
        state["equity_history"] = [
            {"timestamp": None, "equity_usd": 1000.0},
            {"timestamp": timestamp, "equity_usd": 995.0},
        ]

        summary = paper_summary(state)
        self.assertTrue(summary["costs_included"])
        self.assertEqual(summary["net_closed_trades"], 2)
        self.assertAlmostEqual(summary["return_pct_gross"], 1.0)
        self.assertAlmostEqual(summary["return_pct_net"], -0.5)
        self.assertAlmostEqual(summary["win_rate_pct_net"], 50.0)
        self.assertAlmostEqual(summary["profit_factor_net"], 50.0 / 55.0)
        self.assertAlmostEqual(summary["expectancy_r_net"], -0.05)
        self.assertFalse(summary["net_performance_validated"])

    def test_summary_separates_m15_and_daily_performance(self):
        state = new_paper_state()
        state["trades"] = {
            "intraday": {
                "status": "CLOSED",
                "timeframe": "15m",
                "gross_pnl_usd": 12.0,
                "result_r": 0.6,
                "cost_mode": "GROSS_DIAGNOSTIC",
                "exit_timestamp": "2026-11-22T10:30:00+00:00",
            },
            "daily": {
                "status": "CLOSED",
                "timeframe": "1d",
                "gross_pnl_usd": -8.0,
                "result_r": -0.4,
                "cost_mode": "GROSS_DIAGNOSTIC",
                "exit_timestamp": "2026-11-23T16:00:00+00:00",
            },
        }
        state["realized_gross_pnl_usd"] = 4.0
        state["equity_usd"] = 1004.0
        summary = paper_summary(state)

        self.assertEqual(set(summary["by_timeframe"]), {"15m", "1d"})
        self.assertEqual(summary["by_timeframe"]["15m"]["closed_trades"], 1)
        self.assertEqual(summary["by_timeframe"]["1d"]["closed_trades"], 1)
        self.assertAlmostEqual(
            summary["by_timeframe"]["15m"]["gross_pnl_usd"], 12.0
        )
        self.assertAlmostEqual(
            summary["by_timeframe"]["1d"]["gross_pnl_usd"], -8.0
        )
        self.assertIsNone(
            summary["by_timeframe"]["15m"]["net_pnl_usd"]
        )
        self.assertEqual(
            summary["by_timeframe"]["15m"]["performance_status"],
            "DESCRIPTIVE_ONLY_NOT_VALIDATED",
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
        self.assertEqual(summary["drawdown_scope"], "REALIZED_CLOSED_TRADES_ONLY")
        self.assertFalse(summary["open_trade_unrealized_pnl_included"])
        self.assertIn("unrealized", summary["drawdown_scope_note"])
        self.assertAlmostEqual(summary["realized_gross_pnl_usd"], 5.0)



def verified_test_registry(*, fractional=True, minimum_notional=10.0):
    registry = copy.deepcopy(load_registry())
    now = datetime.now(timezone.utc).isoformat()
    item = registry["assets"]["AAPL"]
    item["fees"] = {
        "confirmed": True,
        "source": "synthetic unit-test evidence only",
        "observed_at": now,
        "entry": {"model": "FIXED", "value": 1.0, "unit": "USD"},
        "exit": {"model": "FIXED", "value": 1.0, "unit": "USD"},
    }
    item["spread"].update({
        "bid_ask_bps": 10.0,
        "treatment": "SEPARATE_FROM_PRICES",
        "evidence_scope": "HISTORICAL_CONSERVATIVE_MODEL",
        "confirmed": True,
        "source": "synthetic unit-test evidence only",
        "observed_at": now,
    })
    item["price_basis"] = {
        "historical_basis": "synthetic raw OHLC test basis",
        "execution_basis": "synthetic bid/ask test basis",
        "mapping_rule": "synthetic explicit test mapping",
        "confirmed": True,
        "source": "synthetic unit-test evidence only",
        "observed_at": now,
    }
    item["minimum_order"].update({
        "minimum_notional": minimum_notional,
        "fractional_allowed": fractional,
        "confirmed": True,
        "source": "synthetic unit-test evidence only",
        "observed_at": now,
    })
    return registry


class PaperForwardVerifiedCostTests(unittest.TestCase):
    def setUp(self):
        self.state = new_paper_state()
        self.registry = verified_test_registry()

    def test_verified_cost_sizing_includes_fixed_and_variable_round_trip_costs(self):
        result = calculate_verified_cost_aware_quantity(
            "AAPL", 100.0, 95.0, 10.0, registry=self.registry
        )
        self.assertTrue(result["valid"])
        self.assertTrue(result["complete"])
        self.assertAlmostEqual(result["reserved_stop_risk_usd"], 10.0)
        self.assertLessEqual(result["reserved_stop_risk_usd"], 10.0 + 1e-8)
        self.assertGreater(result["estimated_round_trip_costs_usd"], 2.0)

    def test_missing_live_registry_evidence_blocks_cost_aware_entry(self):
        item = signal("blocked-costs")
        item["asset"] = "AAPL"
        process_signal_bar(
            self.state, item,
            bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100),
            risk_fraction=0.01,
            require_verified_costs=True,
        )
        trade = self.state["trades"]["blocked-costs"]
        self.assertEqual(trade["status"], "REJECTED_COSTS_UNVERIFIED")
        self.assertIsNone(trade.get("quantity"))
        self.assertEqual(self.state["open_risk_usd"], 0.0)

    def test_verified_cost_entry_reserves_cash_for_round_trip_costs(self):
        item = signal("verified-cost-cash")
        item["asset"] = "AAPL"
        process_signal_bar(
            self.state, item,
            bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100),
            risk_fraction=0.01,
            require_verified_costs=True,
            economic_registry=self.registry,
        )
        trade = self.state["trades"]["verified-cost-cash"]
        self.assertEqual(trade["status"], "OPEN")
        self.assertEqual(trade["cost_mode"], "VERIFIED_REGISTRY")
        self.assertGreater(trade["cash_reserved_usd"], trade["notional_usd"])
        snapshot = paper_portfolio_snapshot(self.state)
        self.assertAlmostEqual(snapshot["cash_reserved_usd"], trade["cash_reserved_usd"])
        self.assertAlmostEqual(
            snapshot["estimated_cost_reserve_usd"],
            trade["estimated_stop_costs_usd"],
        )
        self.assertAlmostEqual(
            snapshot["available_capital_usd"],
            1000.0 - trade["cash_reserved_usd"],
        )

    def test_verified_round_trip_cost_reserve_cannot_bypass_cash_cap(self):
        item = signal("verified-cost-cash-cap")
        item["asset"] = "AAPL"
        item["stop"] = 99.9
        process_signal_bar(
            self.state, item,
            bar("2026-11-22T10:15:00+00:00", 100, 101, 99.95, 100),
            risk_fraction=0.01,
            require_verified_costs=True,
            economic_registry=self.registry,
        )
        trade = self.state["trades"]["verified-cost-cash-cap"]
        self.assertEqual(trade["status"], "REJECTED_NO_LEVERAGE_CASH")
        self.assertGreater(trade["cash_reservation_required_usd"], 1000.0)
        self.assertGreater(
            trade["notional_usd"] + trade["estimated_stop_costs_usd"],
            1000.0,
        )
        self.assertEqual(trade["available_cash_before_entry_usd"], 1000.0)
        self.assertEqual(self.state["open_risk_usd"], 0.0)

    def test_legacy_verified_open_trade_backfills_cost_cash_reserve(self):
        self.state["trades"]["legacy-verified"] = {
            "status": "OPEN",
            "asset": "AAPL",
            "risk_reserved_usd": 5.0,
            "notional_usd": 100.0,
            "cost_mode": "VERIFIED_REGISTRY",
            "estimated_stop_costs_usd": 2.0,
        }
        snapshot = paper_portfolio_snapshot(self.state)
        self.assertAlmostEqual(snapshot["cash_reserved_usd"], 102.0)
        self.assertAlmostEqual(snapshot["estimated_cost_reserve_usd"], 2.0)
        self.assertAlmostEqual(snapshot["available_capital_usd"], 898.0)

    def test_legacy_verified_open_trade_without_cost_reserve_fails_closed(self):
        self.state["trades"]["legacy-verified"] = {
            "status": "OPEN",
            "asset": "AAPL",
            "risk_reserved_usd": 5.0,
            "notional_usd": 100.0,
            "cost_mode": "VERIFIED_REGISTRY",
        }
        with self.assertRaisesRegex(ValueError, "missing its estimated cost reserve"):
            paper_portfolio_snapshot(self.state)

    def test_verified_cost_stop_close_books_net_pnl_once(self):
        item = signal("verified-cost-stop")
        item["asset"] = "AAPL"
        process_signal_bar(
            self.state, item,
            bar("2026-11-22T10:15:00+00:00", 99, 101, 98, 100),
            risk_fraction=0.01,
            require_verified_costs=True,
            economic_registry=self.registry,
        )
        trade = self.state["trades"]["verified-cost-stop"]
        process_signal_bar(
            self.state, item,
            bar("2026-11-22T10:30:00+00:00", 95, 96, 94, 95),
            risk_fraction=0.01,
            require_verified_costs=True,
            economic_registry=self.registry,
        )
        self.assertEqual(trade["status"], "CLOSED")
        self.assertAlmostEqual(trade["gross_pnl_usd"], -trade["gross_stop_risk_usd"])
        self.assertAlmostEqual(
            trade["gross_stop_risk_usd"] + trade["estimated_stop_costs_usd"],
            10.0,
        )
        self.assertEqual(trade["risk_reserved_usd"], 0.0)
        self.assertAlmostEqual(
            trade["net_pnl_usd"],
            trade["gross_pnl_usd"] - trade["costs_usd"],
        )
        self.assertAlmostEqual(
            self.state["equity_usd"],
            1000.0 + trade["net_pnl_usd"],
        )
        self.assertEqual(self.state["costs_status"], "VERIFIED_NET_COSTS_INCLUDED")
        summary = paper_summary(self.state)
        self.assertTrue(summary["costs_included"])
        self.assertIsNotNone(summary["max_drawdown_pct_net"])
        self.assertGreater(
            summary["max_drawdown_pct_net"],
            summary["max_drawdown_pct_gross"],
        )
        self.assertAlmostEqual(
            summary["max_drawdown_pct_equity"],
            summary["max_drawdown_pct_net"],
        )
        self.assertFalse(summary["net_performance_validated"])


if __name__ == "__main__":
    unittest.main()
