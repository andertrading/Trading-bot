import unittest

from research.twelvedata_split_adjustment_audit import (
    _max_ohlc_deviation_pct,
    _parse_twelvedata_payload,
    _parse_yahoo_payload,
    _validate_yahoo_split_event,
)


class TestTwelveDataSplitAdjustmentAudit(unittest.TestCase):

    def test_parses_twelvedata_metadata_and_daily_ohlc(self):
        payload = {
            "meta": {
                "symbol": "TSLA",
                "exchange": "NASDAQ",
                "mic_code": "XNAS",
                "type": "Common Stock",
                "currency": "USD",
            },
            "values": [{
                "datetime": "2022-08-24",
                "open": "295",
                "high": "300",
                "low": "290",
                "close": "297",
            }],
        }
        bars, metadata = _parse_twelvedata_payload(payload, "TSLA")
        self.assertEqual(bars["2022-08-24"]["close"], 297.0)
        self.assertEqual(metadata["mic_code"], "XNAS")

    def test_rejects_wrong_twelvedata_mic(self):
        payload = {
            "meta": {
                "symbol": "TSLA",
                "exchange": "NASDAQ",
                "mic_code": "XNYS",
                "type": "Common Stock",
                "currency": "USD",
            },
            "values": [{
                "datetime": "2022-08-24",
                "open": "295",
                "high": "300",
                "low": "290",
                "close": "297",
            }],
        }
        with self.assertRaisesRegex(ValueError, "mic_code"):
            _parse_twelvedata_payload(payload, "TSLA")

    def test_parses_yahoo_ohlc_and_split_event(self):
        payload = {
            "chart": {
                "error": None,
                "result": [{
                    "timestamp": [1661347800],
                    "indicators": {
                        "quote": [{
                            "open": [295.0],
                            "high": [300.0],
                            "low": [290.0],
                            "close": [297.0],
                        }]
                    },
                    "events": {
                        "splits": {
                            "1661385600": {
                                "date": 1661385600,
                                "numerator": 3,
                                "denominator": 1,
                                "splitRatio": "3:1",
                            }
                        }
                    },
                }],
            }
        }
        bars, events = _parse_yahoo_payload(payload)
        self.assertEqual(len(bars), 1)
        self.assertEqual(events[0]["ratio"], 3.0)

    def test_validates_expected_split_date_and_ratio(self):
        events = [{"date": "2022-08-25", "ratio": 3.0}]
        self.assertEqual(
            _validate_yahoo_split_event(events, "2022-08-25", 3.0),
            events[0],
        )
        with self.assertRaisesRegex(ValueError, "split event mismatch"):
            _validate_yahoo_split_event(events, "2022-08-25", 10.0)

    def test_compares_all_ohlc_fields(self):
        td = {
            "2022-08-24": {
                "open": 295.0, "high": 300.0, "low": 290.0, "close": 297.0
            },
            "2022-08-25": {
                "open": 296.0, "high": 301.0, "low": 291.0, "close": 298.0
            },
            "2022-08-26": {
                "open": 297.0, "high": 302.0, "low": 292.0, "close": 299.0
            },
            "2022-08-29": {
                "open": 298.0, "high": 303.0, "low": 293.0, "close": 300.0
            },
        }
        yahoo = {day: dict(values) for day, values in td.items()}
        deviation, common, comparisons = _max_ohlc_deviation_pct(td, yahoo)
        self.assertEqual(deviation, 0.0)
        self.assertEqual(len(common), 4)
        self.assertEqual(comparisons, 16)

    def test_rejects_ohlc_mismatch_even_if_close_matches(self):
        td = {
            f"2022-08-{day}": {
                "open": 295.0, "high": 300.0, "low": 290.0, "close": 297.0
            }
            for day in ("22", "23", "24", "25")
        }
        yahoo = {day: dict(values) for day, values in td.items()}
        yahoo["2022-08-24"]["low"] = 280.0
        with self.assertRaisesRegex(ValueError, "OHLC cross-source deviation"):
            _max_ohlc_deviation_pct(td, yahoo)
