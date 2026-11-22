import unittest

from paper_forward import (
    new_paper_state,
    paper_summary,
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


if __ (unittest.main())
