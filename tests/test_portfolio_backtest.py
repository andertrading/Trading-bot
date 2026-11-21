import unittest

from portfolio_backtest import simulate_portfolio


class TestPortfolioBacktest(unittest.TestCase):

    def make_trade(
        self,
        asset="AAPL",
        timeframe="15m",
        entry_time="2026-01-01T10:00:00Z",
        exit_time="2026-01-01T11:00:00Z",
        entry=100.0,
        stop=90.0,
        score=80,
        result_R=1.0,
    ):
        return {
            "asset": asset,
            "timeframe": timeframe,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "entry": entry,
            "stop": stop,
            "score": score,
            "result_R": result_R,
        }

    def test_simultaneous_entries_share_portfolio_risk_proportionally(self):
        trades = [
            self.make_trade(asset=asset)
            for asset in ("AAPL", "MSFT", "NVDA", "TSLA", "TTWO", "BTC", "ETH")
        ]
        result = simulate_portfolio(trades, capital_usd=1000.0)

        self.assertEqual(result["summary"]["trades_accepted"], 7)
        total_risk = sum(
            trade["allocated_risk_usd"]
            for trade in result["trades"]
        )
        self.assertAlmostEqual(total_risk, 30.0, places=7)
        self.assertLessEqual(
            result["summary"]["peak_open_risk_usd"],
            30.0 + 1e-8,
        )
        self.assertTrue(
            all(trade["allocation_factor"] < 1 for trade in result["trades"])
        )

    def test_risk_is_released_after_position_closes(self):
        first = self.make_trade(
            asset="AAPL",
            entry_time="2026-01-01T10:00:00Z",
            exit_time="2026-01-01T11:00:00Z",
        )
        second = self.make_trade(
            asset="MSFT",
            entry_time="2026-01-01T12:00:00Z",
            exit_time="2026-01-01T13:00:00Z",
        )
        result = simulate_portfolio([first, second], capital_usd=1000.0)

        self.assertEqual(result["summary"]["trades_accepted"], 2)
        self.assertAlmostEqual(
            result["trades"][0]["allocated_risk_usd"],
            5.0,
        )
        self.assertAlmostEqual(
            result["trades"][1]["allocated_risk_usd"],
            5.025,
        )

    def test_same_timestamp_exit_does_not_release_cash_before_entry(self):
        first = self.make_trade(
            asset="AAPL",
            entry_time="2026-01-01T10:00:00Z",
            exit_time="2026-01-01T11:00:00Z",
            entry=100.0,
            stop=100.0 - (2.5 / 900.0 * 100.0),
            score=90,
        )
        second = self.make_trade(
            asset="MSFT",
            entry_time="2026-01-01T11:00:00Z",
            exit_time="2026-01-01T12:00:00Z",
            entry=100.0,
            stop=100.0 - (2.5 / 900.0 * 100.0),
            score=90,
        )
        result = simulate_portfolio([first, second], capital_usd=1000.0)

        self.assertEqual(result["summary"]["trades_accepted"], 2)
        self.assertLess(
            result["trades"][1]["allocation_factor"],
            0.25,
        )

    def test_rejects_unknown_asset_and_invalid_long_levels(self):
        unknown = self.make_trade(asset="NOT_CONFIGURED")
        invalid = self.make_trade(asset="AAPL", stop=101.0)
        result = simulate_portfolio([unknown, invalid], capital_usd=1000.0)

        self.assertEqual(result["summary"]["trades_accepted"], 0)
        self.assertEqual(result["summary"]["trades_rejected"], 2)
        self.assertIn(
            "activo_no_configurado",
            [row["reason"] for row in result["rejections"]],
        )

    def test_trade_result_is_scaled_by_allocated_risk(self):
        trade = self.make_trade(result_R=2.0)
        result = simulate_portfolio([trade], capital_usd=1000.0)

        self.assertEqual(result["summary"]["trades_accepted"], 1)
        self.assertAlmostEqual(result["trades"][0]["allocated_risk_usd"], 5.0)
        self.assertAlmostEqual(result["trades"][0]["realized_pnl_usd"], 10.0)
        self.assertAlmostEqual(result["summary"]["ending_equity_usd"], 1010.0)
        self.assertFalse(result["summary"]["costs_included"])

    def test_simultaneous_exits_are_aggregated_for_drawdown(self):
        winning = self.make_trade(
            asset="AAPL",
            result_R=1.0,
        )
        losing = self.make_trade(
            asset="MSFT",
            result_R=-1.0,
        )
        result = simulate_portfolio(
            [winning, losing],
            capital_usd=1000.0,
        )

        self.assertEqual(result["summary"]["trades_accepted"], 2)
        self.assertAlmostEqual(result["summary"]["ending_equity_usd"], 1000.0)
        self.assertAlmostEqual(result["summary"]["max_drawdown_usd"], 0.0)

    def test_drawdown_percentage_uses_equity_peak(self):
        winning = self.make_trade(
            asset="AAPL",
            entry_time="2026-01-01T10:00:00Z",
            exit_time="2026-01-01T11:00:00Z",
            score=90,
            result_R=2.0,
        )
        losing = self.make_trade(
            asset="MSFT",
            entry_time="2026-01-01T12:00:00Z",
            exit_time="2026-01-01T13:00:00Z",
            score=90,
            result_R=-1.0,
        )
        result = simulate_portfolio(
            [winning, losing],
            capital_usd=1000.0,
        )

        self.assertAlmostEqual(
            result["summary"]["max_drawdown_usd"],
            2.5125,
        )
        self.assertAlmostEqual(
            result["summary"]["max_drawdown_pct"],
            0.25,
        )

    def test_invalid_capital_fails_closed(self):
        with self.assertRaises(ValueError):
            simulate_portfolio([], capital_usd=0)


if __name__ == "__main__":
    unittest.main()
