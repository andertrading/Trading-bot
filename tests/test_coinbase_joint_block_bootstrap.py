import unittest
import numpy as np

from research.coinbase_joint_block_bootstrap import (
    _sample_week_indices,
    bootstrap_synchronized_weekly_blocks,
)


class CoinbaseJointBlockBootstrapTests(unittest.TestCase):
    def test_circular_block_indices_preserve_requested_length_and_contiguity(self):
        indices = _sample_week_indices(5, 2, np.random.default_rng(7))
        self.assertEqual(len(indices), 5)
        for offset in range(0, len(indices) - 1, 2):
            self.assertEqual((int(indices[offset + 1]) - int(indices[offset])) % 5, 1)

    def test_bootstrap_is_reproducible_and_keeps_zero_trade_weeks(self):
        windows = [
            {"weekly_r": [1.0, 0.0, -1.0], "weekly_trade_count": [1, 0, 1]},
            {"weekly_r": [2.0, -2.0], "weekly_trade_count": [2, 2]},
        ]
        first = bootstrap_synchronized_weekly_blocks(
            windows, block_length=1, replicates=500, seed=123
        )
        second = bootstrap_synchronized_weekly_blocks(
            windows, block_length=1, replicates=500, seed=123
        )
        self.assertEqual(first, second)
        self.assertEqual(first["replicates"], 500)
        self.assertEqual(first["block_length_weeks"], 1)
        self.assertEqual(len(first["mean_gross_R_per_trade_95pct_interval"]), 2)
        self.assertGreaterEqual(first["resampled_trade_count_95pct_interval"][0], 0)

    def test_rejects_mismatched_weekly_arrays(self):
        with self.assertRaisesRegex(ValueError, "matching non-empty"):
            bootstrap_synchronized_weekly_blocks(
                [{"weekly_r": [1.0], "weekly_trade_count": []}],
                block_length=1, replicates=10,
            )


if __name__ == "__main__":
    unittest.main()
