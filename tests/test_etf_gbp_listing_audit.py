import unittest

import pandas as pd

from research.etf_gbp_listing_audit import (
    assess_target_dates,
    compare_ohlc,
    convert_gbp_pence_to_usd,
    ohlc_integrity,
    trusted_primary_fields,
)


def make_frame(rows):
    return pd.DataFrame(
        rows,
        columns=["Open", "High", "Low", "Close"],
        index=pd.to_datetime(["2025-10-24", "2025-10-27"][: len(rows)]),
    )


class TestEtfGbpListingAudit(unittest.TestCase):
    def test_gbx_prices_convert_to_usd_using_same_session_fx_close(self):
        gbx = make_frame([
            [54243.0, 54701.0, 54220.0, 54693.0],
            [55000.0, 55200.0, 54800.0, 55100.0],
        ])
        fx = make_frame([
            [1.32, 1.34, 1.31, 1.3306],
        ])
        converted, missing = convert_gbp_pence_to_usd(gbx, fx)
        self.assertEqual(missing, ["2025-10-27"])
        self.assertEqual(len(converted), 1)
        self.assertAlmostEqual(converted.iloc[0]["Open"], 542.43 * 1.3306)
        self.assertAlmostEqual(converted.iloc[0]["Close"], 546.93 * 1.3306)

    def test_comparison_reports_field_level_deviations(self):
        usd = make_frame([
            [721.0, 728.0, 720.0, 727.0],
            [730.0, 735.0, 729.0, 734.0],
        ])
        converted = usd.copy()
        converted["High"] = converted["High"] * 1.002
        result = compare_ohlc(usd, converted)
        self.assertEqual(result["common_sessions"], 2)
        self.assertGreater(result["fields"]["High"]["max_abs_pct"], 0.19)
        self.assertEqual(result["fields"]["Close"]["max_abs_pct"], 0.0)

    def test_missing_primary_session_is_candidate_only_not_approved(self):
        usd = make_frame([[730.0, 735.0, 729.0, 734.0]])
        alternate = make_frame([
            [721.0, 728.0, 720.0, 727.0],
            [730.0, 735.0, 729.0, 734.0],
        ])
        result = assess_target_dates(usd, alternate, ["2025-10-24"])
        self.assertEqual(
            result[0]["status"], "CANDIDATE_ONLY_NO_PRIMARY_CROSSCHECK"
        )
        self.assertIn("gbx_candidate_ohlc_usd", result[0])

    def test_trusted_fields_exclude_corrupt_low_without_discarding_close(self):
        row = pd.Series({
            "Open": 100.0, "High": 102.0, "Low": 101.5, "Close": 101.0
        })
        trusted = trusted_primary_fields(row)
        self.assertNotIn("Low", trusted)
        self.assertTrue({"Open", "High", "Close"}.issubset(trusted))

    def test_ohlc_integrity_rejects_non_coherent_bars(self):
        frame = make_frame([
            [100.0, 102.0, 99.0, 101.0],
            [100.0, 99.0, 101.0, 101.0],
        ])
        result = ohlc_integrity(frame)
        self.assertEqual(result["invalid_rows"], 1)


if __name__ == "__main__":
    unittest.main()
