import unittest

from paper_market_data import (
    MarketDataRevisionError,
    empty_market_data_state,
    merge_asset_bars,
    validate_market_data_state,
)


def bar(day, close=100.0):
    return {
        "date": day,
        "open": close - 1.0,
        "high": close + 1.0,
        "low": close - 2.0,
        "close": close,
        "volume": 1000,
    }


class PaperMarketDataTests(unittest.TestCase):
    def test_initial_seed_and_idempotent_replay(self):
        state = empty_market_data_state()
        captured = "2026-11-22T18:00:00+00:00"
        state, added = merge_asset_bars(
            state, "CSPX", [bar("2026-11-19"), bar("2026-11-20")],
            captured_at=captured,
        )
        self.assertEqual(added, 2)
        state, added = merge_asset_bars(
            state, "CSPX", [bar("2026-11-19"), bar("2026-11-20")],
            captured_at=captured,
        )
        self.assertEqual(added, 0)
        self.assertEqual(len(state["assets"]["CSPX"]["bars"]), 2)
        validate_market_data_state(state)

    def test_new_session_appends_without_rewriting_history(self):
        state = empty_market_data_state()
        state, _ = merge_asset_bars(
            state, "CNDX", [bar("2026-11-19")],
            captured_at="2026-11-20T18:00:00+00:00",
        )
        state, added = merge_asset_bars(
            state, "CNDX", [bar("2026-11-19"), bar("2026-11-20", 101.0)],
            captured_at="2026-11-21T18:00:00+00:00",
        )
        self.assertEqual(added, 1)
        self.assertEqual(
            [item["date"] for item in state["assets"]["CNDX"]["bars"]],
            ["2026-11-19", "2026-11-20"],
        )

    def test_provider_revision_is_blocked_not_silently_overwritten(self):
        state = empty_market_data_state()
        state, _ = merge_asset_bars(
            state, "CSPX", [bar("2026-11-20")],
            captured_at="2026-11-20T18:00:00+00:00",
        )
        with self.assertRaises(MarketDataRevisionError):
            merge_asset_bars(
                state, "CSPX", [bar("2026-11-20", 99.5)],
                captured_at="2026-11-21T18:00:00+00:00",
            )
        self.assertEqual(
            state["assets"]["CSPX"]["bars"][0]["close"], 100.0
        )

    def test_invalid_ohlc_and_duplicate_dates_are_rejected(self):
        state = empty_market_data_state()
        bad = bar("2026-11-20")
        bad["high"] = 90.0
        with self.assertRaises(ValueError):
            merge_asset_bars(
                state, "CSPX", [bad],
                captured_at="2026-11-20T18:00:00+00:00",
            )
        with self.assertRaises(ValueError):
            merge_asset_bars(
                state, "CSPX", [bar("2026-11-20"), bar("2026-11-20")],
                captured_at="2026-11-20T18:00:00+00:00",
            )

    def test_unknown_asset_and_invalid_state_are_rejected(self):
        state = empty_market_data_state()
        with self.assertRaises(ValueError):
            merge_asset_bars(
                state, "TSLA", [bar("2026-11-20")],
                captured_at="2026-11-20T18:00:00+00:00",
            )
        state["mode"] = "LIVE"
        with self.assertRaises(ValueError):
            merge_asset_bars(
                state, "CSPX", [bar("2026-11-20")],
                captured_at="2026-11-20T18:00:00+00:00",
            )

    def test_persisted_source_calendar_and_listing_are_pinned(self):
        state = empty_market_data_state()
        state["source"] = "YAHOO"
        with self.assertRaises(ValueError):
            validate_market_data_state(state)

        state = empty_market_data_state()
        state["calendar"] = "XNYS"
        with self.assertRaises(ValueError):
            validate_market_data_state(state)

        state = empty_market_data_state()
        state["assets"]["CNDX"]["symbol"] = "CNDX.SIX"
        with self.assertRaises(ValueError):
            validate_market_data_state(state)


if __name__ == "__main__":
    unittest.main()
