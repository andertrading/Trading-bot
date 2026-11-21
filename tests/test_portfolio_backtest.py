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
            1.0,
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
        self.assertEqual(result["summary"]["pnl_basis"], "GROSS_EX_COSTS")
        self.assertAlmostEqual(result["summary"]["gross_pnl_usd"], 10.0)
        self.assertAlmostEqual(result["summary"]["gross_return_pct"], 1.0)
        self.assertNotIn("net_pnl_usd", result["summary"])

    def test_net_costs_reduce_equity_and_future_position_sizing(self):
        first = self.make_trade(
            asset="AAPL",
            entry_time="2026-01-01T10:00:00Z",
            exit_time="2026-01-01T11:00:00Z",
            result_R=1.0,
        )
        second = self.make_trade(
            asset="MSFT",
            entry_time="2026-01-01T12:00:00Z",
            exit_time="2026-01-01T13:00:00Z",
            result_R=0.0,
        )

        gross = simulate_portfolio([first, second], capital_usd=1000.0)
        net = simulate_portfolio(
            [first, second],
            capital_usd=1000.0,
            execution_costs={"venue": "ETORO_REAL", "spread_bps": 0},
        )

        self.assertAlmostEqual(gross["trades"][1]["allocated_risk_usd"], 5.025)
        self.assertAlmostEqual(net["trades"][1]["allocated_risk_usd"], 5.015)
        self.assertAlmostEqual(net["summary"]["gross_pnl_usd"], 5.0)
        self.assertAlmostEqual(net["summary"]["total_entry_cost_usd"], 2.0)
        self.assertAlmostEqual(net["summary"]["total_exit_cost_usd"], 2.0)
        self.assertAlmostEqual(net["summary"]["net_pnl_usd"], 1.0)
        self.assertAlmostEqual(net["summary"]["ending_equity_usd"], 1001.0)
        self.assertTrue(net["summary"]["costs_included"])
        self.assertEqual(net["summary"]["pnl_basis"], "NET_AFTER_ASSUMED_COSTS")

    def test_crypto_commission_is_charged_on_entry_and_exit_notional(self):
        trade = self.make_trade(
            asset="BTC",
            entry=100.0,
            stop=90.0,
            result_R=1.0,
        )
        trade["exit"] = 110.0
        result = simulate_portfolio(
            [trade],
            capital_usd=1000.0,
            execution_costs={"venue": "ETORO_REAL", "spread_bps": 0},
        )

        self.assertAlmostEqual(result["summary"]["gross_pnl_usd"], 5.0)
        self.assertAlmostEqual(result["summary"]["total_entry_cost_usd"], 0.5)
        self.assertAlmostEqual(result["summary"]["total_exit_cost_usd"], 0.55)
        self.assertAlmostEqual(result["summary"]["net_pnl_usd"], 3.95)
        self.assertAlmostEqual(result["trades"][0]["net_realized_pnl_usd"], 3.95)

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

    def test_open_risk_percentage_tracks_realized_loss_and_blocks_new_risk(self):
        trades = [
            self.make_trade(
                asset=asset,
                entry_time="2026-01-01T10:00:00Z",
                exit_time=(
                    "2026-01-01T11:00:00Z"
                    if asset == "AAPL"
                    else "2026-01-01T14:00:00Z"
                ),
                result_R=-50.0 if asset == "AAPL" else 0.0,
            )
            for asset in ("AAPL", "MSFT", "NVDA", "TSLA", "TTWO", "BTC")
        ]
        later = self.make_trade(
            asset="ETH",
            entry_time="2026-01-01T12:00:00Z",
            exit_time="2026-01-01T13:00:00Z",
        )
        result = simulate_portfolio(
            trades + [later],
            capital_usd=1000.0,
        )

        summary = result["summary"]
        self.assertGreater(
            summary["peak_open_risk_pct_of_equity"],
            summary["portfolio_risk_limit_pct"],
        )
        self.assertGreaterEqual(summary["risk_limit_breach_events"], 1)
        later_rejection = [
            row for row in result["rejections"]
            if row["source_index"] == len(trades)
        ]
        self.assertEqual(len(later_rejection), 1)
        self.assertEqual(
            later_rejection[0]["reason"],
            "limite_de_riesgo_o_capital_disponible",
        )

    def test_entry_commission_is_reserved_against_available_cash(self):
        trade = self.make_trade(
            asset="BTC",
            entry=100.0,
            stop=99.9,
            score=80,
            result_R=0.0,
        )
        result = simulate_portfolio(
            [trade],
            capital_usd=100.0,
            execution_costs={
                "venue": "ETORO_REAL",
                "crypto_rate_per_side": 0.10,
                "spread_bps": 0,
            },
        )

        position = result["trades"][0]
        committed_cash = (
            position["notional_usd"] + position["entry_cost_usd"]
        )
        self.assertLessEqual(committed_cash, 100.0 + 1e-8)
        self.assertLess(position["notional_usd"], 100.0)
        self.assertAlmostEqual(result["summary"]["ending_equity_usd"], 100.0, places=7)

    def test_invalid_capital_fails_closed(self):
        with self.assertRaises(ValueError):
            simulate_portfolio([], capital_usd=0)


if __name__ == "__main__":
    unittest.main()
