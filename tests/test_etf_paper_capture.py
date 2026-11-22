import unittest
from unittest.mock import patch

import requests

from paper_market_data import empty_market_data_state
from research import etf_paper_capture


class EtfPaperCaptureStorageTests(unittest.TestCase):
    @patch("research.etf_paper_capture._upstash_request")
    def test_load_missing_key_returns_empty_isolated_state(self, request):
        request.return_value = {"result": None}
        state = etf_paper_capture.load_state(
            "https://upstash.example", "test-token"
        )
        self.assertEqual(state, empty_market_data_state())
        self.assertIn(
            "/get/tradingbot:etf_daily_paper_data:v1",
            request.call_args.args[1],
        )

    @patch("research.etf_paper_capture._upstash_request")
    def test_load_rejects_invalid_persisted_json(self, request):
        request.return_value = {"result": "{not-json"}
        with self.assertRaisesRegex(RuntimeError, "invalid"):
            etf_paper_capture.load_state(
                "https://upstash.example", "test-token"
            )

    @patch("research.etf_paper_capture._upstash_request")
    def test_save_uses_dedicated_key_and_requires_confirmation(self, request):
        request.return_value = {"result": "OK"}
        state = empty_market_data_state()
        size = etf_paper_capture.save_state(
            "https://upstash.example", "test-token", state
        )
        self.assertGreater(size, 0)
        args = request.call_args
        self.assertEqual(args.args[0], "POST")
        self.assertIn(
            "/set/tradingbot:etf_daily_paper_data:v1",
            args.args[1],
        )
        self.assertEqual(args.kwargs["headers"]["Authorization"], "Bearer test-token")

    @patch("research.etf_paper_capture._upstash_request")
    def test_save_fails_closed_without_upstash_confirmation(self, request):
        request.return_value = {"result": None}
        with self.assertRaisesRegex(RuntimeError, "did not confirm"):
            etf_paper_capture.save_state(
                "https://upstash.example", "test-token",
                empty_market_data_state(),
            )

    @patch("research.etf_paper_capture.requests.request")
    def test_network_error_does_not_leak_provider_response_details(self, request):
        request.side_effect = requests.RequestException(
            "authorization Bearer highly-sensitive-test-secret"
        )
        with self.assertRaises(RuntimeError) as raised:
            etf_paper_capture._upstash_request(
                "GET",
                "https://upstash.example/get/key",
                headers={"Authorization": "Bearer highly-sensitive-test-secret"},
                timeout=5,
            )
        self.assertNotIn("highly-sensitive-test-secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
